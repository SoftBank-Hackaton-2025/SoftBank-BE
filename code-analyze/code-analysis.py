import json
import os
import zipfile
import mimetypes
import posixpath
import re
from io import BytesIO
from typing import List, Dict, Any, Set
from collections import defaultdict

import boto3
from botocore.exceptions import ClientError

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Max-Age": "86400"
}

# ====== Config ======
REGION = "ap-northeast-2"
BUCKET_NAME = "deployment-tr-bucket"

# 분석 결과(metadata.json) S3 저장 여부
SAVE_METADATA_TO_S3 = True

# Bedrock Runtime (Seoul)
session = boto3.session.Session(region_name=REGION)
bedrock = session.client("bedrock-runtime", region_name=REGION)
s3_client = session.client("s3", region_name=REGION)

MODEL_ID = "arn:aws:bedrock:ap-northeast-2:273354645391:inference-profile/apac.amazon.nova-pro-v1:0"

# ====== Limits / Heuristics ======
MAX_FILES_PER_PROJECT = 50           # 프로젝트당 최대 파일 수
MAX_TOTAL_FILES = 120                # 전체 최대 파일 수
MAX_BYTES_PER_FILE = 16_000          # 파일당 전송 상한 (minified text 기준)
MAX_TOTAL_BYTES = 350_000            # 전체 전송 상한 (minified text 총합)
MAX_LINE_CHARS = 200                 # minify 시 라인 길이 상한
PAYLOAD_SOFT_CAP_BYTES = 1_200_000   # LLM 호출 페이로드 상한

TEXT_EXTS = {
    ".txt", ".md", ".json", ".yaml", ".yml",
    ".tf", ".tfvars", ".tf.json",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt",
    ".go", ".rb", ".php", ".cs", ".c", ".cpp", ".h", ".hpp",
    ".gradle", ".groovy", ".properties",
    ".sh", ".bash", ".zsh", ".ps1",
    ".Dockerfile", "Dockerfile",
    ".ini", ".env", ".conf", ".cfg",
}
BINARY_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".pdf", ".zip", ".tar", ".gz", ".mp3", ".wav"}

# ZIP 내부 스킵 규칙
SKIP_DIRS = {
    "node_modules", ".git", ".idea", ".vscode",
    "build", "dist", "target", ".gradle", ".next", ".cache", ".turbo",
    "__MACOSX"
}
SKIP_PREFIXES = ("./", "._")

SENSITIVE_KEYS = ("secret", "password", "passwd", "token", "apikey", "access_key", "secret_key")
SAFE_ERROR_SLICE = 500

# ====== Project root markers ======
PROJECT_ROOT_MARKERS = {
    # Build/dependency files
    "package.json", "pom.xml", "build.gradle", "build.gradle.kts",
    "settings.gradle", "settings.gradle.kts", "requirements.txt",
    "pyproject.toml", "go.mod", "Gemfile", "Cargo.toml",
    # Container/orchestration
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    # IaC
    "main.tf", "versions.tf",
}

# ====== Signal files (whitelist) ======
SIGNAL_BASENAMES = {
    "package.json","pnpm-lock.yaml","yarn.lock","pom.xml",
    "build.gradle","build.gradle.kts","settings.gradle","settings.gradle.kts",
    "requirements.txt","pyproject.toml","Pipfile","Pipfile.lock",
    "go.mod","Gemfile","Cargo.toml","Cargo.lock",
    "Dockerfile","docker-compose.yml","docker-compose.yaml",
    ".gitlab-ci.yml",".travis.yml","cdk.json",
}
SIGNAL_SUFFIXES = (".tf", ".tfvars", ".tf.json", ".tfvars.json", ".yaml", ".yml", ".properties")
SIGNAL_PATH_PARTS = {".github/workflows", "src/main/resources"}

# ====== Minify (주석/공백 제거) ======
COMMENT_RE = {
    "default": re.compile(r"$^"),
    "js": re.compile(r"(//.*?$)|(/\*.*?\*/)", re.DOTALL | re.MULTILINE),
    "ts": re.compile(r"(//.*?$)|(/\*.*?\*/)", re.DOTALL | re.MULTILINE),
    "java": re.compile(r"(//.*?$)|(/\*.*?\*/)", re.DOTALL | re.MULTILINE),
    "kt": re.compile(r"(//.*?$)|(/\*.*?\*/)", re.DOTALL | re.MULTILINE),
    "go": re.compile(r"(//.*?$)|(/\*.*?\*/)", re.DOTALL | re.MULTILINE),
    "py": re.compile(r"(^\s*#.*?$)|('''.*?'''|\"\"\".*?\"\"\")", re.DOTALL | re.MULTILINE),
    "sh": re.compile(r"(^\s*#.*?$)", re.MULTILINE),
    "yml": re.compile(r"(^\s*#.*?$)", re.MULTILINE),
    "yaml": re.compile(r"(^\s*#.*?$)", re.MULTILINE),
    "properties": re.compile(r"(^\s*#.*?$)", re.MULTILINE),
}

# ====== Prompt shape hint ======
SHAPE_HINT = (
    "services[].name,services[].language,services[].framework,services[].artifact,"
    "services[].docker.hasDockerfile,services[].docker.ports[],services[].env,services[].dependsOn[],"
    "services[].runtime.javaVersion,services[].runtime.nodeVersion,services[].runtime.pythonVersion,"
    "infrastructure.aws.ec2,infrastructure.aws.alb,infrastructure.aws.rds,infrastructure.aws.s3,"
    "infrastructure.aws.ecr,infrastructure.aws.vpc,infrastructure.aws.iam,infrastructure.aws.lambda,"
    "infrastructure.aws.apigw,infrastructure.aws.bedrock,infrastructure.aws.kafka_msksqs,"
    "infrastructure.aws.redis_elasticache,infrastructure.external[],"
    "deployment.buildTool,deployment.ci,deployment.containerOrchestration,"
    "deployment.terraformHints.terraformRequiredProviders[],"
    "deployment.terraformHints.terraformModulesCandidates[],"
    "deployment.terraformHints.variables.*,deployment.terraformHints.outputs.*,"
    "findings[].type,findings[].message,findings[].path"
)

# ====== Error helpers ======
def is_debug_mode(event) -> bool:
    env_debug = os.getenv("DEBUG", "").lower() in ("1", "true", "yes", "on")
    try:
        qs = event.get("queryStringParameters") or {}
        qs_debug = str(qs.get("debug", "")).lower() in ("1", "true", "yes", "on")
    except Exception:
        qs_debug = False
    return env_debug or qs_debug

def build_error_response(status: int, message: str, exc: Exception, context, debug: bool):
    aws_req_id = getattr(context, "aws_request_id", None)
    payload = {"message": "실패", "reason": message, "requestId": aws_req_id}
    if isinstance(exc, ClientError):
        err = exc.response.get("Error", {})
        code = err.get("Code")
        msg  = err.get("Message")
        payload["error"] = {
            "type": "AWS.ClientError",
            "code": code,
            "message": (msg[:SAFE_ERROR_SLICE] if msg else None) if debug else code,
            "serviceRequestId": exc.response.get("ResponseMetadata", {}).get("RequestId"),
        }
    else:
        if debug:
            payload["error"] = {
                "type": exc.__class__.__name__,
                "message": (str(exc)[:SAFE_ERROR_SLICE] if exc else None),
            }
    return {"statusCode": status, "body": json.dumps(payload, ensure_ascii=False)}

# ====== Utils ======
def is_binary_candidate(name: str) -> bool:
    _, ext = posixpath.splitext(name)
    if ext.lower() in BINARY_EXTS:
        return True
    ctype, _ = mimetypes.guess_type(name)
    return bool(ctype and not ctype.startswith("text"))

def should_skip_path(path: str) -> bool:
    norm = posixpath.normpath(path).lstrip("/")
    if norm.endswith("/"):
        return True
    if any(norm.startswith(p) for p in SKIP_PREFIXES):
        return True
    parts = norm.split("/")
    if any(part in SKIP_DIRS for part in parts[:-1]):
        return True
    if posixpath.basename(norm).startswith("._"):
        return True
    return False

def redact_secrets(text: str) -> str:
    lines = text.splitlines()
    redacted = []
    for line in lines:
        l = line.lower()
        if any(k in l for k in SENSITIVE_KEYS):
            if "=" in line:
                k, _, _ = line.partition("=")
                redacted.append(f"{k}=***REDACTED***")
            elif ":" in line:
                k, _, _ = line.partition(":")
                redacted.append(f"{k}: ***REDACTED***")
            else:
                redacted.append("***REDACTED LINE***")
        else:
            redacted.append(line)
    return "\n".join(redacted)

def is_signal_path(path: str) -> bool:
    b = posixpath.basename(path)
    if b in SIGNAL_BASENAMES:
        return True
    if b.startswith("Dockerfile"):
        return True
    if b.lower().startswith("readme"):
        return True
    if b.startswith("application") and (b.endswith(".yml") or b.endswith(".yaml") or b.endswith(".properties")):
        return True
    if b.endswith(SIGNAL_SUFFIXES):
        return True
    parts = path.split("/")
    if any(p in parts for p in SIGNAL_PATH_PARTS):
        return True
    return False

def strip_comments_and_minify(text: str, ext: str) -> str:
    lang = ext.lower().lstrip(".")
    rx = COMMENT_RE.get(lang, COMMENT_RE["default"])
    text = rx.sub("", text)
    lines = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS] + " …"
        lines.append(line)
    out = "\n".join(lines)
    if len(out.encode("utf-8")) > MAX_BYTES_PER_FILE:
        out = out.encode("utf-8")[:MAX_BYTES_PER_FILE].decode("utf-8", errors="ignore") + "\n… [TRUNCATED]"
    return out

def strip_and_cap_bytes(content: bytes, ext: str) -> str:
    text = content.decode("utf-8", errors="replace")
    text = redact_secrets(text)
    text = strip_comments_and_minify(text, ext)
    return text

# ====== Project detection ======
def detect_project_roots(all_paths: List[str]) -> Dict[str, Set[str]]:
    """
    프로젝트 루트 디렉토리 감지
    Returns: {project_root_path: set_of_files_in_project}
    """
    project_roots = {}
    
    # 1단계: 마커 파일이 있는 디렉토리를 프로젝트 루트 후보로 수집
    for path in all_paths:
        if should_skip_path(path) or path.endswith("/"):
            continue
        
        basename = posixpath.basename(path)
        if basename in PROJECT_ROOT_MARKERS:
            # 이 파일의 디렉토리를 프로젝트 루트로 등록
            dir_path = posixpath.dirname(path) or "."
            if dir_path not in project_roots:
                project_roots[dir_path] = set()
    
    # 프로젝트가 없으면 전체를 하나의 프로젝트로 간주
    if not project_roots:
        project_roots["."] = set()
    
    # 2단계: 각 파일을 가장 가까운 프로젝트에 할당
    for path in all_paths:
        if should_skip_path(path) or path.endswith("/"):
            continue
        
        # 파일이 속한 프로젝트 루트 찾기 (가장 깊은 depth의 루트 선택)
        assigned_root = None
        max_depth = -1
        
        for root in project_roots.keys():
            # path가 root 아래에 있는지 확인
            if root == "." or path.startswith(root + "/") or path == root:
                depth = root.count("/")
                if depth > max_depth:
                    max_depth = depth
                    assigned_root = root
        
        if assigned_root:
            project_roots[assigned_root].add(path)
    
    # 빈 프로젝트 제거
    project_roots = {k: v for k, v in project_roots.items() if v}
    
    return project_roots

# ====== Ultra-compact prompt builder ======
def build_llm_prompt(file_summaries: List[Dict[str, Any]], project_context: str = "") -> str:
    header = (
        f"Analyze {project_context}. "
        "Return JSON only (no backticks, no prose). If uncertain, omit fields. "
        "Fill only these keys: " + SHAPE_HINT + "\n=== FILES ===\n"
    )
    parts = [header]
    for fs in file_summaries:
        parts.append(f"[{fs['path']}]\n")
        parts.append(fs["content"])
        parts.append("\n---\n")
    parts.append("Output strictly valid JSON.")
    return "".join(parts)

# ====== Bedrock invoke with payload guard ======
def safe_invoke_bedrock(prompt: str, max_tokens: int = 1500) -> str:
    payload_messages = [
        {
            "role": "user", 
            "content": [
                {
                    "text": prompt
                }
            ]
        }
    ]
    
    inference_config = {
        "maxTokens": max_tokens,
        "temperature": 0.0,
        "topP": 0.9
    }
    payload_check = json.dumps({
        "messages": payload_messages,
        "inferenceConfig": inference_config
    }, ensure_ascii=False)
    payload_bytes = len(payload_check.encode("utf-8"))

    if payload_bytes > PAYLOAD_SOFT_CAP_BYTES:
        raise ValueError(f"Prompt too large: {payload_bytes} bytes (cap={PAYLOAD_SOFT_CAP_BYTES})")

    resp = bedrock.converse(
        modelId=MODEL_ID,
        messages=payload_messages,
        inferenceConfig=inference_config
    )

    return resp['output']['message']['content'][0]['text']

# ====== Merge metadata from multiple projects ======
def merge_metadata(metadata_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    """여러 프로젝트의 메타데이터를 하나로 병합"""
    merged = {
        "services": [],
        "infrastructure": {
            "aws": {},
            "external": []
        },
        "deployment": {},
        "findings": [],
        "projects": []  # 프로젝트별 정보 추가
    }
    
    for idx, meta in enumerate(metadata_list):
        project_name = meta.get("projectName", f"project-{idx+1}")
        
        # 프로젝트 정보 저장
        merged["projects"].append({
            "name": project_name,
            "root": meta.get("projectRoot", "."),
            "serviceCount": len(meta.get("services", []))
        })
        
        # services 병합 (프로젝트명 prefix 추가)
        for svc in meta.get("services", []):
            svc_copy = svc.copy()
            if "name" in svc_copy:
                svc_copy["name"] = f"{project_name}/{svc_copy['name']}"
            svc_copy["project"] = project_name
            merged["services"].append(svc_copy)
        
        # infrastructure 병합
        infra = meta.get("infrastructure", {})
        aws_infra = infra.get("aws", {})
        for key, value in aws_infra.items():
            if value:  # 값이 있는 경우만
                if key not in merged["infrastructure"]["aws"]:
                    merged["infrastructure"]["aws"][key] = value
                elif isinstance(value, dict):
                    merged["infrastructure"]["aws"][key].update(value)
        
        ext_infra = infra.get("external", [])
        merged["infrastructure"]["external"].extend(ext_infra)
        
        # deployment 병합 (첫 번째 것 우선 또는 병합)
        deploy = meta.get("deployment", {})
        if not merged["deployment"]:
            merged["deployment"] = deploy
        else:
            # terraform hints 병합
            if "terraformHints" in deploy:
                if "terraformHints" not in merged["deployment"]:
                    merged["deployment"]["terraformHints"] = deploy["terraformHints"]
        
        # findings 병합
        for finding in meta.get("findings", []):
            finding_copy = finding.copy()
            finding_copy["project"] = project_name
            merged["findings"].append(finding_copy)
    
    return merged

# ====== Handler ======
def lambda_handler(event, context):
    debug = is_debug_mode(event)

    # 1) Parse Request
    body = event.get("body", event)
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            body = {}

    uuid = body.get("request_id") or event.get("request_id")
    req_file_name = body.get("file_name") or event.get("file_name")

    if not uuid or not req_file_name:
        return {"statusCode": 400, "body": json.dumps({"message": "실패"})}

    zip_key = f"uploads/{uuid}/{req_file_name}"
    result_prefix = f"results/{uuid}/"

    try:
        # 2) Download ZIP from S3
        print(f"S3 다운로드 시작: s3://{BUCKET_NAME}/{zip_key}")
        zip_obj = s3_client.get_object(Bucket=BUCKET_NAME, Key=zip_key)
        zip_content = zip_obj["Body"].read()
        print(f"S3 다운로드 완료: s3://{BUCKET_NAME}/{zip_key}")

        # 3) Unzip and detect projects
        print("프로젝트 감지 시작")
        with zipfile.ZipFile(BytesIO(zip_content)) as zip_ref:
            all_paths = [name for name in zip_ref.namelist() 
                        if not name.endswith("/") and not should_skip_path(name)]
            
            # 프로젝트 루트 감지
            project_roots = detect_project_roots(all_paths)
            print(f"감지된 프로젝트 수: {len(project_roots)}")
            for root, files in project_roots.items():
                print(f"  - {root}: {len(files)} files")
            
            # 4) 프로젝트별로 파일 수집 및 분석
            metadata_list = []
            total_files_collected = 0
            used_total_bytes = 0
            
            for project_root, project_files in project_roots.items():
                if total_files_collected >= MAX_TOTAL_FILES:
                    print(f"[LIMIT] 전체 파일 수 상한 도달: {MAX_TOTAL_FILES}")
                    break
                
                project_name = posixpath.basename(project_root) if project_root != "." else "root"
                print(f"\n[프로젝트: {project_name}] 분석 시작 (root: {project_root})")
                
                # Signal 파일 필터링
                candidates = [p for p in project_files if is_signal_path(p)]
                
                # 우선순위 정렬
                def score(p: str) -> int:
                    p_l = p.lower()
                    base = posixpath.basename(p_l)
                    if p_l.endswith((".tf", ".tfvars", ".tf.json")): return 100
                    if "docker" in p_l or "compose" in p_l: return 90
                    if base in ("package.json","pom.xml","build.gradle","build.gradle.kts"): return 80
                    if ("application" in p_l and (p_l.endswith(".yml") or p_l.endswith(".yaml") or p_l.endswith(".properties"))): return 70
                    if base.startswith("readme"): return 60
                    return 10
                
                candidates.sort(key=score, reverse=True)
                
                # 파일 수집 (프로젝트당 제한 + 전체 제한 고려)
                file_summaries = []
                project_bytes = 0
                remaining_total_quota = MAX_TOTAL_FILES - total_files_collected
                project_limit = min(MAX_FILES_PER_PROJECT, remaining_total_quota)
                
                for name in candidates[:project_limit]:
                    if used_total_bytes >= MAX_TOTAL_BYTES:
                        break
                    
                    raw = zip_ref.read(name)
                    _, ext = posixpath.splitext(name)
                    snippet = strip_and_cap_bytes(raw, ext)
                    size = len(snippet.encode("utf-8"))
                    
                    if used_total_bytes + size > MAX_TOTAL_BYTES:
                        remain = MAX_TOTAL_BYTES - used_total_bytes
                        if remain <= 0:
                            break
                        snippet = snippet.encode("utf-8")[:remain].decode("utf-8", errors="ignore") + "\n… [TRUNCATED]"
                        size = len(snippet.encode("utf-8"))
                    
                    # 프로젝트 루트 기준 상대 경로로 변환
                    rel_path = name
                    if project_root != "." and name.startswith(project_root + "/"):
                        rel_path = name[len(project_root)+1:]
                    
                    file_summaries.append({
                        "path": rel_path,
                        "languageHint": ext.lstrip("."),
                        "content": snippet
                    })
                    project_bytes += size
                    used_total_bytes += size
                    total_files_collected += 1
                
                print(f"[프로젝트: {project_name}] 수집 완료: {len(file_summaries)} files, {project_bytes} bytes")
                
                # 5) Bedrock 분석 (프로젝트별)
                if file_summaries:
                    try:
                        prompt = build_llm_prompt(
                            file_summaries, 
                            project_context=f"project '{project_name}' at '{project_root}'"
                        )
                        llm_raw = safe_invoke_bedrock(prompt, max_tokens=1500)
                        llm_text = llm_raw.strip()
                        
                        # 코드펜스 제거
                        if llm_text.startswith("```"):
                            llm_text = llm_text.strip("`")
                            idx = llm_text.find("{")
                            if idx >= 0:
                                llm_text = llm_text[idx:]
                            ridx = llm_text.rfind("}")
                            if ridx >= 0:
                                llm_text = llm_text[:ridx+1]
                        
                        metadata = json.loads(llm_text)
                        metadata["projectName"] = project_name
                        metadata["projectRoot"] = project_root
                        metadata_list.append(metadata)
                        print(f"[프로젝트: {project_name}] 분석 성공")
                        
                    except Exception as e:
                        print(f"[프로젝트: {project_name}] 분석 실패: {str(e)[:200]}")
                        # 실패해도 계속 진행
                        continue
            
            # 6) 메타데이터 병합
            if metadata_list:
                if len(metadata_list) == 1:
                    # 단일 프로젝트는 그대로 사용
                    final_metadata = metadata_list[0]
                else:
                    # 여러 프로젝트는 병합
                    final_metadata = merge_metadata(metadata_list)
                
                # S3 저장
                meta_key = f"{result_prefix}metadata.json"
                if SAVE_METADATA_TO_S3:
                    s3_client.put_object(
                        Bucket=BUCKET_NAME,
                        Key=meta_key,
                        Body=json.dumps(final_metadata, ensure_ascii=False, indent=2).encode("utf-8"),
                        ContentType="application/json"
                    )
                    print(f"\nMetadata written: s3://{BUCKET_NAME}/{meta_key}")
                    print(f"총 분석된 프로젝트 수: {len(metadata_list)}")
                    print(f"총 수집된 파일 수: {total_files_collected}")
                    print(f"총 사용된 바이트: {used_total_bytes}")
                else:
                    print("[INFO] SAVE_METADATA_TO_S3=False (metadata not stored)")
            else:
                print("[WARN] 분석된 프로젝트가 없습니다")
        
        # 7) 성공 응답
        return {
            "statusCode": 200,
            "headers": CORS_HEADERS,
            "body": json.dumps({
                "message": "성공",
                "projectsAnalyzed": len(metadata_list),
                "filesCollected": total_files_collected
            }, ensure_ascii=False)
        }

    except ClientError as ce:
        print(f"[AWS ClientError] {str(ce)[:300]}")
        return build_error_response(502, "AWS 클라이언트 오류", ce, context, debug)
    except Exception as e:
        print(f"[Error] {type(e).__name__}: {str(e)[:300]}")
        return build_error_response(500, "내부 처리 중 오류 발생", e, context, debug)
