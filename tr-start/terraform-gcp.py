import json
import boto3
import os

BUCKET_NAME = "deployment-tr-bucket"

# ✅ Bedrock Runtime
session = boto3.session.Session(region_name="ap-northeast-2")
bedrock = session.client("bedrock-runtime", region_name="ap-northeast-2")

MODEL_ID = "arn:aws:bedrock:ap-northeast-2:273354645391:inference-profile/apac.amazon.nova-pro-v1:0"

# ✅ S3 클라이언트 (서울 리전)
s3 = boto3.client("s3", region_name="ap-northeast-2")


def lambda_handler(event, context):

    try :
        request_id = event['request_id']
        survey = event['survey']

        metadata_key = f"results/{request_id}/metadata.json"
        metadata_obj = s3.get_object(Bucket=BUCKET_NAME, Key=metadata_key)
        metadata = json.loads(metadata_obj['Body'].read().decode('utf-8'))

        # Bedrock으로 Terraform 생성
        prompt = f"""
        You are a senior DevOps engineer specializing in **Google Cloud Platform (GCP)** infrastructure as code using **Terraform**.

        Your task is to generate a complete, syntactically valid **Terraform configuration (.tf)** for GCP **based on the metadata and survey information below**.

        ### Instructions
        - Output only **pure Terraform HCL syntax**, exactly as it would appear in a `.tf` file.
        - **Do NOT include markdown code fences**, comments, explanations, or any non-HCL text.
        - The code must be ready to use in Terraform directly.
        - Include appropriate:
            - `terraform` and `provider "google"` blocks
            - GCP resource definitions (e.g., compute, storage, IAM)
            - variables and outputs where applicable
        - Use consistent indentation and correct resource naming conventions.
        - Focus on creating **functional and realistic** GCP infrastructure code that satisfies the metadata and survey context.

        ### Input Data
        Metadata:
        {json.dumps(metadata, indent=2)}

        Survey:
        {json.dumps(survey, indent=2)}

        ### Output
        Generate only the Terraform (.tf) configuration below:
        """

        response = bedrock.invoke_model(
            modelId=MODEL_ID,
            body=json.dumps({
                "messages" : [
                    {
                        "role": "user",
                        "content": [
                            {
                                "text" : prompt,
                            }
                        ]
                    }
                ],
                "inferenceConfig" : {
                    "max_new_tokens" : 4096,
                    "temperature" : 0.7
                }
            }),
            contentType="application/json",
        )

        result = json.loads(response.get("body").read())
        terraform_content = result['output']['message']['content'][0]['text']

        # S3에 Terraform 파일 저장
        terraform_key = f"results/{request_id}/terraform-gcp.tf"

        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=terraform_key,
            Body=terraform_content.encode('utf-8'),
            ContentType='text/plain'
        )

        return {
            'request_id': request_id,
            'provider': 'gcp',
            'terraform_key': terraform_key,
            'status': 'success'
        }
    except Exception as e:
        print(f"Error: {str(e)}")
        raise
