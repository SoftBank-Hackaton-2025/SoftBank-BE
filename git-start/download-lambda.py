# import json
# import boto3
# import os
# from typing import Dict, Any


# stepfunctions_client = boto3.client('stepfunctions')
# STATE_MACHINE_ARN = 'arn:aws:states:ap-northeast-2:273354645391:stateMachine:all-in-one-state-machine'

# def lambda_handler(event, context):

#     try:
#         if isinstance(event.get('body'), str):
#             body = json.loads(event['body'])
#         else:
#             body = event
        
#         request_id = body.get('request_id')
#         cloud = body.get('cloud')

#         # 입력 검증
#         if not request_id or not cloud:
#             return {
#                 'statusCode': 400,
#                 'body': json.dumps({
#                     'error': 'request_id and cloud are required'
#                 })
#             }

#        # Step Functions 입력 데이터 준비
#         step_input = {
#             'request_id': request_id,
#             'cloud': cloud
#         }

#         # Step Functions 실행 (동기 실행)
#         response = stepfunctions_client.start_sync_execution(
#             stateMachineArn=STATE_MACHINE_ARN,
#             input=json.dumps(step_input)
#         )

#         # 실행 상태 확인
#         if response['status'] != 'SUCCEEDED':
#             error_msg = response.get('cause', 'Step Functions execution failed')
#             print(f"Step Functions execution failed: {error_msg}")
#             return {
#                 'statusCode': 500,
#                 'body': json.dumps({
#                     'error': 'Processing failed',
#                     'details': error_msg
#                 })
#             }

#         # 결과 파싱
#         output = json.loads(response['output'])

#         # 병렬 실행 결과는 리스트로 반환됨
#         # parallel_results가 있으면 사용, 없으면 output 자체가 리스트일 수 있음
#         if 'parallel_results' in output:
#             results = output['parallel_results']
#         elif isinstance(output, list):
#             results = output
#         else:
#             print(f"Unexpected output format: {output}")
#             return {
#                 'statusCode': 500,
#                 'headers': {
#                     'Content-Type': 'application/json',
#                     'Access-Control-Allow-Origin': '*'
#                 },
#                 'body': json.dumps({
#                     'error': 'Unexpected output format from Step Functions'
#                 })
#             }

#         # 각 람다의 결과 추출 (순서: terraform, actions, cli)
#         terraform_result = results[0]
#         actions_result = results[1]
#         cli_result = results[2]
        
#         # 최종 응답 구성
#         final_response = {
#             'cloud': cloud,
#             'terraform': terraform_result.get('presigned_url', ''),
#             'actions': actions_result.get('presigned_url', ''),
#             'cli': cli_result.get('presigned_url', '')
#         }

#         return {
#             'statusCode': 200,
#             'headers': {
#                 'Content-Type': 'application/json',
#                 'Access-Control-Allow-Origin': '*'
#             },
#             'body': json.dumps(final_response)
#         }
        
#     except json.JSONDecodeError as e:
#         print(f"JSON parsing error: {str(e)}")
#         return {
#             'statusCode': 400,
#             'body': json.dumps({
#                 'error': 'Invalid JSON format',
#                 'details': str(e)
#             })
#         }
    
#     except Exception as e:
#         print(f"Unexpected error: {str(e)}")
#         return {
#             'statusCode': 500,
#             'body': json.dumps({
#                 'error': 'Internal server error',
#                 'details': str(e)
#             })
#         }

import json
import boto3
import os
from typing import Dict, Any


stepfunctions_client = boto3.client('stepfunctions')
STATE_MACHINE_ARN = os.environ.get(
    'STATE_MACHINE_ARN',
    'arn:aws:states:ap-northeast-2:273354645391:stateMachine:all-in-one-state-machine'
)


def lambda_handler(event, context):
    try:
        # API Gateway 요청 파싱
        if isinstance(event.get('body'), str):
            body = json.loads(event['body'])
        else:
            body = event
        
        request_id = body.get('request_id')
        cloud = body.get('cloud', 'aws').lower()
        
        # 입력 검증
        if not request_id:
            return create_response(400, {
                'error': 'request_id is required'
            })

        print(f"Starting Step Functions - request_id: {request_id}, cloud: {cloud}")

        # Step Functions 입력 데이터 준비
        step_input = {
            'request_id': request_id,
            'cloud': cloud
        }

        # Step Functions 실행 (동기 실행)
        response = stepfunctions_client.start_sync_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            input=json.dumps(step_input)
        )

        print(f"Step Functions status: {response['status']}")

        # 실행 상태 확인
        if response['status'] != 'SUCCEEDED':
            error_msg = response.get('cause', 'Step Functions execution failed')
            print(f"Step Functions failed: {error_msg}")
            
            return create_response(500, {
                'error': 'Processing failed',
                'details': error_msg
            })

        # 결과 파싱
        output = json.loads(response['output'])
        print(f"Step Functions output: {json.dumps(output, default=str)}")

        # lambdaResults 배열에서 결과 추출
        if 'lambdaResults' not in output:
            print(f"lambdaResults not found in output. Keys: {output.keys()}")
            return create_response(500, {
                'error': 'Unexpected output format from Step Functions',
                'details': 'lambdaResults not found'
            })
        
        results = output['lambdaResults']
        
        # 결과 개수 확인
        if len(results) < 3:
            print(f"Expected 3 results, got {len(results)}")
            return create_response(500, {
                'error': 'Incomplete results from Step Functions',
                'details': f'Expected 3 results, got {len(results)}'
            })

        # 각 Lambda의 결과에서 presigned_url 추출
        terraform_url = results[0].get('presigned_url', '')
        actions_url = results[1].get('presigned_url', '')
        cli_url = results[2].get('presigned_url', '')

        # 결과 검증
        if not terraform_url or not actions_url or not cli_url:
            print(f"Missing URLs - terraform: {bool(terraform_url)}, "
                  f"actions: {bool(actions_url)}, cli: {bool(cli_url)}")
            return create_response(500, {
                'error': 'Incomplete URLs from Step Functions'
            })

        # 최종 응답 구성
        final_response = {
            'cloud': cloud,
            'terraform': terraform_url,
            'actions': actions_url,
            'cli': cli_url
        }

        print(f"Success! Returning response with all URLs")
        return create_response(200, final_response)
        
    except json.JSONDecodeError as e:
        print(f"JSON parsing error: {str(e)}")
        return create_response(400, {
            'error': 'Invalid JSON format'
        })
    
    except KeyError as e:
        print(f"KeyError: {str(e)}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")
        return create_response(500, {
            'error': 'Missing required field in Step Functions output',
            'details': str(e)
        })
    
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")
        return create_response(500, {
            'error': 'Internal server error',
            'details': str(e)
        })


def create_response(status_code: int, body: dict) -> dict:
    """
    API Gateway 응답을 생성하는 헬퍼 함수
    """
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*'
        },
        'body': json.dumps(body, ensure_ascii=False)
    }
