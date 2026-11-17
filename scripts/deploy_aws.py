import boto3
import json
import time
import zipfile
import io
import os
from botocore.exceptions import ClientError

PROJECT_PREFIX = "Silverstone"
EVENTS_TABLE_NAME = "Events_Table"
BOOKINGS_TABLE_NAME = "Bookings_Table"
LAMBDA_FUNC_NAME = f"{PROJECT_PREFIX}-Backend"
API_NAME = f"{PROJECT_PREFIX}-API"
USER_POOL_NAME = f"{PROJECT_PREFIX}-Users"
APP_CLIENT_NAME = f"{PROJECT_PREFIX}-WebClient"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_JS_PATH = os.path.join(BASE_DIR, "backend-lambda", "index.js")
SEED_JSON_PATH = os.path.join(BASE_DIR, "backend-lambda", "seed-events.json")
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend-html")
CONFIG_JS_PATH = os.path.join(FRONTEND_DIR, "config.js")
INDEX_HTML_PATH = os.path.join(FRONTEND_DIR, "index.html")
ZIP_PATH = os.path.join(BASE_DIR, "frontend-html.zip")

session = boto3.Session()
region = session.region_name or "us-east-1"
sts = session.client("sts")
account_id = sts.get_caller_identity()["Account"]

print("=" * 60)
print(f"🚀 ระบบติดตั้งอัตโนมัติ {PROJECT_PREFIX} (Account: {account_id})")
print("=" * 60)

while True:
    stripe_key = input("กรอก Stripe Secret Key : ").strip()
    if stripe_key.startswith("sk_"):
        break
    print("  ❌ จำเป็นต้องระบุ Stripe Secret Key ที่ขึ้นต้นด้วย sk_")

dynamodb = session.client("dynamodb")
ddb_resource = session.resource("dynamodb")
cognito = session.client("cognito-idp")
lam = session.client("lambda")
apigw = session.client("apigateway")

print("\n[1/4] กำลังสร้างตาราง Amazon DynamoDB และนำเข้าข้อมูลอีเวนต์...")
def create_table_if_not_exists(params):
    tname = params["TableName"]
    try:
        dynamodb.create_table(**params)
        dynamodb.get_waiter("table_exists").wait(TableName=tname)
        print(f"  ✅ สร้างตาราง {tname} สำเร็จ")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceInUseException":
            print(f"  ℹ️ ตาราง {tname} มีอยู่แล้ว")
        else:
            raise

create_table_if_not_exists({
    "TableName": EVENTS_TABLE_NAME,
    "KeySchema": [{"AttributeName": "event_id", "KeyType": "HASH"}],
    "AttributeDefinitions": [{"AttributeName": "event_id", "AttributeType": "S"}],
    "BillingMode": "PAY_PER_REQUEST",
})

create_table_if_not_exists({
    "TableName": BOOKINGS_TABLE_NAME,
    "KeySchema": [{"AttributeName": "booking_id", "KeyType": "HASH"}],
    "AttributeDefinitions": [
        {"AttributeName": "booking_id", "AttributeType": "S"},
        {"AttributeName": "user_id", "AttributeType": "S"},
    ],
    "GlobalSecondaryIndexes": [{
        "IndexName": "user_id-index",
        "KeySchema": [{"AttributeName": "user_id", "KeyType": "HASH"}],
        "Projection": {"ProjectionType": "ALL"},
    }],
    "BillingMode": "PAY_PER_REQUEST",
})

if os.path.exists(SEED_JSON_PATH):
    with open(SEED_JSON_PATH, "r", encoding="utf-8") as f:
        seed_items = json.load(f)
    table = ddb_resource.Table(EVENTS_TABLE_NAME)
    for item in seed_items:
        table.put_item(Item=item)
    print(f"  ✅ นำเข้าข้อมูลอีเวนต์ตัวอย่าง {len(seed_items)} รายการเรียบร้อย")

print("\n[2/4] กำลังสร้าง Amazon Cognito User Pool...")
user_pool_id = None
client_id = None
for p in cognito.list_user_pools(MaxResults=50).get("UserPools", []):
    if p["Name"] == USER_POOL_NAME:
        user_pool_id = p["Id"]
        break

if not user_pool_id:
    pool_res = cognito.create_user_pool(
        PoolName=USER_POOL_NAME,
        UsernameAttributes=["email"],
        AutoVerifiedAttributes=["email"],
        Policies={"PasswordPolicy": {"MinimumLength": 6, "RequireUppercase": False, "RequireLowercase": True, "RequireNumbers": True, "RequireSymbols": False}}
    )
    user_pool_id = pool_res["UserPool"]["Id"]
    print(f"  ✅ สร้าง User Pool สำเร็จ : {user_pool_id}")
else:
    print(f"  ℹ️ ใช้ User Pool เดิม : {user_pool_id}")

for c in cognito.list_user_pool_clients(UserPoolId=user_pool_id, MaxResults=50).get("UserPoolClients", []):
    if c["ClientName"] == APP_CLIENT_NAME:
        client_id = c["ClientId"]
        break

if not client_id:
    client_res = cognito.create_user_pool_client(
        UserPoolId=user_pool_id,
        ClientName=APP_CLIENT_NAME,
        GenerateSecret=False,
        ExplicitAuthFlows=["ALLOW_USER_SRP_AUTH", "ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
    )
    client_id = client_res["UserPoolClient"]["ClientId"]
    print(f"  ✅ สร้าง App Client สำเร็จ : {client_id}")
else:
    print(f"  ℹ️ ใช้ App Client เดิม : {client_id}")

print("\n[3/4] กำลังสร้าง/อัปเดต AWS Lambda Function...")
role_arn = f"arn:aws:iam::{account_id}:role/LabRole"
if not os.path.exists(BACKEND_JS_PATH):
    raise FileNotFoundError(f"ไม่พบไฟล์ {BACKEND_JS_PATH}")

zip_buffer = io.BytesIO()
with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.write(BACKEND_JS_PATH, arcname="index.js")
zip_bytes = zip_buffer.getvalue()

env_vars = {
    "EVENTS_TABLE": EVENTS_TABLE_NAME,
    "BOOKINGS_TABLE": BOOKINGS_TABLE_NAME,
    "STRIPE_SECRET_KEY": stripe_key,
}

try:
    lam_res = lam.create_function(
        FunctionName=LAMBDA_FUNC_NAME,
        Runtime="nodejs20.x",
        Role=role_arn,
        Handler="index.handler",
        Code={"ZipFile": zip_bytes},
        Timeout=15,
        Environment={"Variables": env_vars}
    )
    lambda_arn = lam_res["FunctionArn"]
    print(f"  ✅ สร้าง Lambda Function สำเร็จ : {LAMBDA_FUNC_NAME}")
except ClientError as e:
    if e.response["Error"]["Code"] == "ResourceConflictException":
        lam.update_function_code(FunctionName=LAMBDA_FUNC_NAME, ZipFile=zip_bytes)
        time.sleep(3)
        lam_res = lam.update_function_configuration(FunctionName=LAMBDA_FUNC_NAME, Environment={"Variables": env_vars})
        lambda_arn = lam_res["FunctionArn"]
        print(f"  ✅ อัปเดต Lambda Function สำเร็จ : {LAMBDA_FUNC_NAME}")
    else:
        raise

print("\n[4/4] กำลังสร้าง Amazon API Gateway (REST API)...")
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

config_js_content = f"""window.__AWS_CONFIG__ = {{
  VITE_AWS_REGION: "{region}",
  VITE_COGNITO_USER_POOL_ID: "{user_pool_id}",
  VITE_COGNITO_CLIENT_ID: "{client_id}",
  VITE_API_BASE_URL: "{api_url}"
}};
"""
os.makedirs(FRONTEND_DIR, exist_ok=True)
with open(CONFIG_JS_PATH, "w", encoding="utf-8") as f:
    f.write(config_js_content.strip() + "\n")

with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.write(INDEX_HTML_PATH, arcname="index.html")
    zf.write(CONFIG_JS_PATH, arcname="config.js")

print("\n" + "=" * 60)
print(f"🎉 ติดตั้งระบบหลังบ้าน {PROJECT_PREFIX} สำเร็จ")
print("=" * 60)
print(f"API Gateway URL: {api_url}")
print("-" * 60)
print("✅ สร้างไฟล์  Silverstone/frontend-html.zip สำหรับอัปโหลดใน AWS Amplify สำเร็จ")
print("=" * 60)
