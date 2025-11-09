import boto3
import json
import re

BUCKET_NAME = "deployment-tr-bucket"

# ✅ Bedrock Runtime
session = boto3.session.Session(region_name="ap-northeast-2")
bedrock = session.client("bedrock-runtime", region_name="ap-northeast-2")

MODEL_ID = "arn:aws:bedrock:ap-northeast-2:273354645391:inference-profile/apac.amazon.nova-pro-v1:0"

# ✅ S3 클라이언트 (서울 리전)
s3 = boto3.client("s3", region_name="ap-northeast-2")

def lambda_handler(event, context):

    try : 
        request_id = event["request_id"]
        terraform_key = event["terraform_key"]

        # S3에서 .tf 파일 읽기
        terraform_obj = s3.get_object(Bucket=BUCKET_NAME, Key=terraform_key)
        terraform_content = terraform_obj["Body"].read().decode("utf-8")

        # Bedrock으로 비용 계산
        prompt = f"""
        You are a cloud financial analyst specializing in **AWS cost estimation** from **Terraform configurations**.

        Your task:
        - Read and understand the Terraform configuration below.
        - Identify each AWS resource (e.g., EC2, Lambda, S3, RDS, VPC, CloudFront, API Gateway, etc.).
        - Estimate the **total monthly cost in USD** based on **typical on-demand pricing** in **us-east-1**.
        - Include all major cost factors such as compute, storage, networking, and data transfer.

        ### Output Format
        - Output only **one numeric value** (e.g., `153.47`)
        - Use exactly **two decimal places**
        - **Do NOT include** currency symbols, text, units, comments, markdown, or explanations.

        ### Terraform Configuration
        {terraform_content}
        """



        response = bedrock.invoke_model(
            modelId=MODEL_ID,
            body=json.dumps({
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
                    "max_new_tokens": 1024,
                    "temperature": 0.7,
                    "top_p": 0.9
                }
            }),
            contentType="application/json",
            accept="application/json"
        )

        result = json.loads(response.get("body").read())
        cost = result['output']['message']['content'][0]['text'].strip()

        # 숫자만 추출
        cost = re.search(r'\d+(?:\.\d+)?', cost)
        if cost: 
            cost = cost.group(0)

        return {
            "request_id": request_id,
            "provider" : "aws",
            "cost": cost
        }

    except Exception as e:
        print(f"Error: {str(e)}")
        raise
