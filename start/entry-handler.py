import json
import boto3
import os
import uuid

s3 = boto3.client("s3")

BUCKET = os.environ["BUCKET_NAME"]

def lambda_handler(event, context):
    # ✅ 1. request_id 생성
    request_id = str(uuid.uuid4())

    # ✅ 2. 업로드 경로
    upload_key = f"uploads/{request_id}/source.zip"

    # ✅ 3. presigned URL 생성
    presigned_url = s3.generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": BUCKET,
            "Key": upload_key,
            "ContentType": "application/zip"
        },
        ExpiresIn=300  # 5분 유효
    )

    # ✅ 4. 결과 반환
    response = {
        "upload_url": presigned_url,
        "request_id": request_id
    }

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
        },
        "body": json.dumps(response)
    }
