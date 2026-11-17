import boto3
import time
from botocore.exceptions import ClientError

PROJECT_PREFIX = "Silverstone"
API_NAME = f"{PROJECT_PREFIX}-API"
LAMBDA_FUNC_NAME = f"{PROJECT_PREFIX}-Backend"
USER_POOL_NAME = f"{PROJECT_PREFIX}-Users"

session = boto3.Session()
region = session.region_name or "us-east-1"
sts = session.client("sts")
apigw = session.client("apigateway")
cognito = session.client("cognito-idp")
lam = session.client("lambda")

try:
    account_id = sts.get_caller_identity()["Account"]
except Exception:
    account_id = "Unknown"

print("=" * 60)
print(f"🌐 [Step 4] ติดตั้ง Amazon API Gateway (REST API: {API_NAME})")
print("=" * 60)

# ค้นหา Cognito User Pool อัตโนมัติ
user_pool_id = None
for p in cognito.list_user_pools(MaxResults=50).get("UserPools", []):
    if p["Name"] == USER_POOL_NAME:
        user_pool_id = p["Id"]
        break

if not user_pool_id:
    user_pool_id = input(f"👉 ไม่พบ User Pool '{USER_POOL_NAME}' อัตโนมัติ กรุณากรอก User Pool ID: ").strip()
else:
    print(f"  ℹ️ ใช้ Cognito User Pool : {user_pool_id}")

# ตรวจสอบ Lambda ARN
try:
    lam_info = lam.get_function(FunctionName=LAMBDA_FUNC_NAME)
    lambda_arn = lam_info["Configuration"]["FunctionArn"]
    print(f"  ℹ️ ใช้ Lambda Function : {LAMBDA_FUNC_NAME}")
except Exception:
    lambda_arn = f"arn:aws:lambda:{region}:{account_id}:function:{LAMBDA_FUNC_NAME}"

# ลบ API Gateway เดิมหากมี
old_apis = [a for a in apigw.get_rest_apis(limit=50).get("items", []) if a["name"] == API_NAME]
for idx, old_api in enumerate(old_apis):
    while True:
        try:
            apigw.delete_rest_api(restApiId=old_api["id"])
            print(f"  🧹 ลบ API Gateway ตัวเก่า ({old_api['id']}) เรียบร้อย")
            if idx < len(old_apis) - 1:
                print("  ⏳ รอ 35 วินาทีตามข้อจำกัด Rate Limit ของ AWS...")
                time.sleep(35)
            break
        except ClientError as e:
            if e.response["Error"]["Code"] == "TooManyRequestsException":
                print("  ⏳ ติด Rate Limit การลบ API Gateway กำลังรอ 35 วินาที...")
                time.sleep(35)
            else:
                raise

print(f"  ⏳ กำลังสร้าง REST API: {API_NAME}...")
api_res = apigw.create_rest_api(name=API_NAME, endpointConfiguration={"types": ["REGIONAL"]})
api_id = api_res["id"]
root_id = apigw.get_resources(restApiId=api_id)["items"][0]["id"]

auth_res = apigw.create_authorizer(
    restApiId=api_id,
    name="CognitoAuth",
    type="COGNITO_USER_POOLS",
    providerARNs=[f"arn:aws:cognito-idp:{region}:{account_id}:userpool/{user_pool_id}"],
    identitySource="method.request.header.Authorization"
)
authorizer_id = auth_res["id"]

routes = [
    ("events", "GET", False),
    ("bookings", "POST", True),
    ("my-bookings", "GET", True),
    ("cancel-booking", "POST", True),
    ("simulate-payment", "POST", True),
]
lambda_uri = f"arn:aws:apigateway:{region}:lambda:path/2015-03-31/functions/{lambda_arn}/invocations"

for path_part, http_method, use_auth in routes:
    res_id = apigw.create_resource(restApiId=api_id, parentId=root_id, pathPart=path_part)["id"]
    apigw.put_method(
        restApiId=api_id, resourceId=res_id, httpMethod=http_method,
        authorizationType="COGNITO_USER_POOLS" if use_auth else "NONE",
        authorizerId=authorizer_id if use_auth else ""
    )
    apigw.put_integration(
        restApiId=api_id, resourceId=res_id, httpMethod=http_method,
        type="AWS_PROXY", integrationHttpMethod="POST", uri=lambda_uri
    )
    apigw.put_method(restApiId=api_id, resourceId=res_id, httpMethod="OPTIONS", authorizationType="NONE")
    apigw.put_integration(restApiId=api_id, resourceId=res_id, httpMethod="OPTIONS", type="MOCK", requestTemplates={"application/json": '{"statusCode": 200}'})
    apigw.put_method_response(
        restApiId=api_id, resourceId=res_id, httpMethod="OPTIONS", statusCode="200",
        responseParameters={"method.response.header.Access-Control-Allow-Headers": False, "method.response.header.Access-Control-Allow-Methods": False, "method.response.header.Access-Control-Allow-Origin": False}
    )
    apigw.put_integration_response(
        restApiId=api_id, resourceId=res_id, httpMethod="OPTIONS", statusCode="200",
        responseParameters={"method.response.header.Access-Control-Allow-Headers": "'Content-Type,Authorization'", "method.response.header.Access-Control-Allow-Methods": "'GET,POST,OPTIONS'", "method.response.header.Access-Control-Allow-Origin": "'*'"}
    )

try:
    lam.add_permission(
        FunctionName=LAMBDA_FUNC_NAME,
        StatementId=f"apigw-invoke-{api_id}",
        Action="lambda:InvokeFunction",
        Principal="apigateway.amazonaws.com",
        SourceArn=f"arn:aws:execute-api:{region}:{account_id}:{api_id}/*/*"
    )
except ClientError:
    pass

apigw.create_deployment(restApiId=api_id, stageName="prod")
api_url = f"https://{api_id}.execute-api.{region}.amazonaws.com/prod"

print("-" * 60)
print(f"🌐 API Gateway URL : {api_url}")
print("=" * 60)
print("🎉 เสร็จสิ้นขั้นตอนที่ 4 (API Gateway)")
print("=" * 60)
