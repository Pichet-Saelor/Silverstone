import boto3
import zipfile
import os

PROJECT_PREFIX = "Silverstone"
USER_POOL_NAME = f"{PROJECT_PREFIX}-Users"
APP_CLIENT_NAME = f"{PROJECT_PREFIX}-WebClient"
API_NAME = f"{PROJECT_PREFIX}-API"

session = boto3.Session()
region = session.region_name or "us-east-1"
cognito = session.client("cognito-idp")
apigw = session.client("apigateway")

print("=" * 60)
print("🎨 [Step 5] กำหนดค่า Frontend (config.js & frontend-html.zip)")
print("=" * 60)

# ค้นหา Cognito
user_pool_id = None
client_id = None
for p in cognito.list_user_pools(MaxResults=50).get("UserPools", []):
    if p["Name"] == USER_POOL_NAME:
        user_pool_id = p["Id"]
        break

if user_pool_id:
    for c in cognito.list_user_pool_clients(UserPoolId=user_pool_id, MaxResults=50).get("UserPoolClients", []):
        if c["ClientName"] == APP_CLIENT_NAME:
            client_id = c["ClientId"]
            break

if not user_pool_id:
    user_pool_id = input("👉 ไม่พบ Cognito User Pool อัตโนมัติ กรุณากรอก User Pool ID : ").strip()
if not client_id:
    client_id = input("👉 ไม่พบ Cognito Client ID อัตโนมัติ กรุณากรอก App Client ID : ").strip()

# ค้นหา API Gateway
api_url = None
for a in apigw.get_rest_apis(limit=50).get("items", []):
    if a["name"] == API_NAME:
        api_url = f"https://{a['id']}.execute-api.{region}.amazonaws.com/prod"
        break

if not api_url:
    api_url = input("👉 ไม่พบ API Gateway URL อัตโนมัติ กรุณากรอก Base URL : ").strip()

config_js_content = f"""window.__AWS_CONFIG__ = {{
  VITE_AWS_REGION: "{region}",
  VITE_COGNITO_USER_POOL_ID: "{user_pool_id}",
  VITE_COGNITO_CLIENT_ID: "{client_id}",
  VITE_API_BASE_URL: "{api_url}"
}};
"""

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend-html")
CONFIG_JS_PATH = os.path.join(FRONTEND_DIR, "config.js")
INDEX_HTML_PATH = os.path.join(FRONTEND_DIR, "index.html")
ZIP_PATH = os.path.join(BASE_DIR, "frontend-html.zip")

os.makedirs(FRONTEND_DIR, exist_ok=True)
with open(CONFIG_JS_PATH, "w", encoding="utf-8") as f:
    f.write(config_js_content.strip() + "\n")
print("  ✅ บันทึกไฟล์ frontend-html/config.js สำเร็จ")

with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.write(INDEX_HTML_PATH, arcname="index.html")
    zf.write(CONFIG_JS_PATH, arcname="config.js")
print("  ✅ สร้างไฟล์  Silverstone/frontend-html.zip สำหรับอัปโหลดใน AWS Amplify สำเร็จ")

print("-" * 60)
print(f"Region          : {region}")
print(f"Cognito Pool ID : {user_pool_id}")
print(f"Cognito Client  : {client_id}")
print(f"API Base URL    : {api_url}")
print("=" * 60)
print("🎉 เสร็จสิ้นขั้นตอนที่ 5 (Frontend Config)")
print("=" * 60)
