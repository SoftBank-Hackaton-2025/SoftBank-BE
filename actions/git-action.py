import json
import boto3
import os
from datetime import datetime
from botocore.exceptions import ClientError

# ✅ 환경 변수
BUCKET_NAME = os.environ.get("BUCKET_NAME", "deployment-tr-bucket")
REGION = "ap-northeast-2"

# ✅ Bedrock Runtime
session = boto3.session.Session(region_name=REGION)
bedrock = session.client("bedrock-runtime", region_name=REGION)

MODEL_ID = "arn:aws:bedrock:ap-northeast-2:273354645391:inference-profile/apac.amazon.nova-pro-v1:0"

s3 = boto3.client("s3", region_name=REGION)


def lambda_handler(event, context):
    try:
        # 1) Parse Request
        body = event.get("body", event)
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError:
                body = {}
        
        request_id = body.get("request_id")
        cloud = body.get("cloud")

    

        if not request_id or not cloud:
            return create_response(400, {"error": "request_id and cloud are required"})
        
        if cloud not in ["aws", "azure", "gcp"]:
            return create_response(400, {"error": "cloud must be one of: aws, azure, gcp"})

        terraform_key = f"results/{request_id}/terraform-{cloud}.tf"
        terraform_content = read_s3_file(BUCKET_NAME, terraform_key)

        github_actions_yml = generate_github_actions(terraform_content, cloud)

        yml_key = f"results/{request_id}/github-actions-{cloud}.yml"
        upload_to_s3(BUCKET_NAME, yml_key, github_actions_yml)

        presigned_url = generate_url(BUCKET_NAME, yml_key, 3600)

        return create_response(200, {
            "actions": presigned_url
        })

    except Exception as e:
        print(f"Error: {str(e)}")
        return create_response(500, {"error": str(e)})


def read_s3_file(bucket, key):
    """S3에서 파일 읽기"""
    response = s3.get_object(Bucket=bucket, Key=key)
    return response['Body'].read().decode('utf-8')

def generate_github_actions(terraform_content, cloud):
    """
    Bedrock Claude를 사용하여 Terraform 코드 기반 GitHub Actions YML 생성
    """
    prompt = f"""
You are a DevOps expert. Analyze the Terraform code below and generate a production-ready GitHub Actions workflow YAML file.

**Requirements:**
1. Optimized workflow for {cloud.upper()} cloud environment
2. Include Terraform init, plan, and apply stages
3. Execute plan on Pull Request, apply on main branch merge
4. Appropriate environment variables and secrets configuration
5. Error handling and notifications
6. Apply security best practices (OIDC authentication, etc.)

**Terraform Code:**
```hcl
{terraform_content}
```

**Response Format:**
- Output ONLY the YAML file
- Add comments explaining each step
- Provide a complete, immediately usable workflow
"""

    # Bedrock API 호출
    # Nova Pro API 호출 (Converse API 사용)
    request_body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "text": prompt
                    }
                ]
            }
        ],
        "inferenceConfig": {
            "maxTokens": 4000,
            "temperature": 0.3,
            "topP": 0.9
        }
    }
    
    response = bedrock.converse(
        modelId=MODEL_ID,
        messages=request_body["messages"],
        inferenceConfig=request_body["inferenceConfig"]
    )
        
    # Claude 응답에서 텍스트 추출
    github_actions_yml = response['output']['message']['content'][0]['text']
    
    # 코드 블록 제거 (```yaml ... ``` 형태)
    github_actions_yml = github_actions_yml.strip()

    if github_actions_yml.startswith("```"):
        lines = github_actions_yml.split("\n")
        # 첫 줄(```) 제거
        if len(lines) > 0 and lines[0].startswith("```"):
            lines = lines[1:]
        # 마지막 줄(```) 제거
        if len(lines) > 0 and lines[-1].strip() == "```":
            lines = lines[:-1]
        github_actions_yml = "\n".join(lines)
    
    return github_actions_yml


def upload_to_s3(bucket, key, content):
    """S3에 파일 업로드"""
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=content.encode('utf-8'),
        ContentType='text/yaml'
    )
    print(f"Uploaded to S3: s3://{bucket}/{key}")


def generate_url(bucket, key, expiration=3600):
    """Presigned URL 생성"""
    url = s3.generate_presigned_url(
        'get_object',
        Params={'Bucket': bucket, 'Key': key},
        ExpiresIn=expiration
    )
    return url

def create_response(status_code, body):
    """API Gateway Response 생성"""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*'
        },
        'body': json.dumps(body, ensure_ascii=False)
    }
