import json
import boto3
import os
from typing import Dict, Any

s3_client = boto3.client('s3')
BUCKET_NAME = os.environ.get('BUCKET_NAME', 'deployment-tr-bucket')
PRESIGNED_URL_EXPIRATION = int(os.environ.get('PRESIGNED_URL_EXPIRATION', '3600'))

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

        s3_key = f"results/{request_id}/github-actions-{cloud}.yml"
        s3_client.head_object(Bucket=BUCKET_NAME, Key=s3_key)

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
            'request_id' : request_id,
            'statusCode': 200,
            'presigned_url': presigned_url,
        }
        
    except Exception as e:
        print(f"Error in get-actions: {str(e)}")
        return {
            'statusCode': 500,
            'error': str(e),
            'presigned_url': ''
        }
