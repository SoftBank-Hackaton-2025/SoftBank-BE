import json
import boto3
import os
from typing import Dict, Any
from datetime import datetime


BUCKET_NAME = os.environ.get("BUCKET_NAME", "deployment-tr-bucket")
REGION = "ap-northeast-2"
PRESIGNED_URL_EXPIRATION = int(os.environ.get('PRESIGNED_URL_EXPIRATION', '3600'))

bedrock_client = boto3.client('bedrock-runtime', region_name=REGION)

# BEDROCK_MODEL_ID = "arn:aws:bedrock:ap-northeast-2:273354645391:inference-profile/apac.anthropic.claude-3-5-sonnet-20240620-v1:0"
BEDROCK_MODEL_ID = "anthropic.claude-3-5-sonnet-20240620-v1:0"

s3_client = boto3.client('s3', region_name=REGION)

def lambda_handler(event, context):

    try:
        request_id = event.get('request_id')
        if not request_id:
            return {
                'statusCode': 400,
                'error': 'request_id is required',
                'presigned_url': ''
            }

        cloud = event.get('cloud', 'aws').lower()
        valid_clouds = ['aws', 'gcp', 'azure']
        if cloud not in valid_clouds:
            return {
                'statusCode': 400,
                'error': f'Invalid cloud type. Must be one of: {valid_clouds}',
                'presigned_url': ''
            }

        cli_commands = generate_cli_commands(cloud, request_id)

        s3_key = f"results/{request_id}/cli-{cloud}.txt"

        # 메타데이터 추가
        metadata = {
            'request_id': request_id,
            'cloud': cloud,
            'generated_at': datetime.utcnow().isoformat(),
            'generator': 'bedrock-claude'
        }

        # S3에 CLI 명령어 저장
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=s3_key,
            Body=cli_commands.encode('utf-8'),
            ContentType='text/plain',
            Metadata=metadata
        )

        # Presigned URL 생성
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': BUCKET_NAME,
                'Key': s3_key
            },
            ExpiresIn=PRESIGNED_URL_EXPIRATION
        )

        return {
            'statusCode': 200,
            'presigned_url': presigned_url,
        }
        
    except Exception as e:
        print(f"Error in create-cloud-cli: {str(e)}")
        return {
            'statusCode': 500,
            'error': str(e),
            'presigned_url': ''
        }

def generate_cli_commands(cloud: str, request_id: str) -> str:
    """
    Amazon Bedrock을 사용하여 클라우드별 CLI 명령어 생성
    """
    
    prompt = f"""You are a cloud infrastructure expert. Generate a comprehensive set of CLI commands for deploying infrastructure on {cloud.upper()}.

The commands should include:
1. Authentication and configuration
2. Resource creation (VPC, subnets, security groups, compute instances, storage)
3. Deployment steps
4. Verification commands
5. Cleanup commands (commented out)

Format the output as a shell script with clear comments and sections.
Make it production-ready and follow {cloud.upper()} best practices.

Request ID: {request_id}
Cloud Platform: {cloud.upper()}
"""

    # Bedrock API 호출 (Claude 3.5 Sonnet)
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096,
        "temperature": 0.3,
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ]
    }
    
    response = bedrock_client.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps(body)
    )
    
    response_body = json.loads(response['body'].read())
    cli_commands = response_body['content'][0]['text']
    
    return cli_commands
