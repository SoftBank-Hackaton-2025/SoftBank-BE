import json
import boto3
import os
import time
from datetime import datetime

BUCKET_NAME = "deployment-tr-bucket"
s3 = boto3.client('s3', region_name='ap-northeast-2')

stepfunctions = boto3.client('stepfunctions')

def generate_terraform_presigned_urls(request_id, providers=['aws', 'gcp', 'azure']):
    terraform_urls = {}
    
    for provider in providers:
        key = f"results/{request_id}/terraform-{provider}.tf"
        try:
            # 파일 존재 확인
            s3.head_object(Bucket=BUCKET_NAME, Key=key)
            
            # Pre-signed URL 생성 (1시간 유효)
            url = s3.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': BUCKET_NAME,
                    'Key': key
                },
                ExpiresIn=3600
            )
            terraform_urls[provider] = url
            
        except s3.exceptions.ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == '404':
                print(f"Terraform file not found: {key}")
                terraform_urls[provider] = None
            else:
                print(f"Error accessing {provider} terraform: {e}")
                terraform_urls[provider] = None
        except Exception as e:
            print(f"Error generating URL for {provider}: {e}")
            terraform_urls[provider] = None
    
    return terraform_urls

def lambda_handler(event, context):
    try:
        body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        request_id = body.get('request_id')
        survey = body.get('survey')

        if not request_id or not survey:
            return {
                'statusCode': 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({'error': 'request_id and survey are required'})
            }

        state_machine_arn = "arn:aws:states:ap-northeast-2:273354645391:stateMachine:NestedWorkflow"

        # Step Functions 동기 실행 시작
        execution_name = f"tf-{request_id}-{int(time.time())}"
        response = stepfunctions.start_sync_execution(
            stateMachineArn=state_machine_arn,
            input=json.dumps({
                'request_id': request_id,
                'survey': survey
            }),
            name=execution_name
        )

        # 실행 상태 확인
        if response['status'] == 'SUCCEEDED':
            # Step Functions 결과 파싱
            output = json.loads(response['output'])

            # Terraform Pre-signed URL 생성
            terraform_urls = generate_terraform_presigned_urls(request_id)
            
            return {
                'statusCode': 200,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({
                    'costs': {
                        'aws': output.get('aws', '0.00'),
                        'gcp': output.get('gcp', '0.00'),
                        'azure': output.get('azure', '0.00')
                    },
                    'terraform_urls': terraform_urls
                })
            }
        else:
            # 실행 실패
            return {
                'statusCode': 500,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({
                    'error': 'Step Functions execution failed',
                    'status': response['status'],
                    'cause': response.get('cause', '')
                })
            }
        
    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({'error': str(e)})
        }
