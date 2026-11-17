import boto3
import zipfile
import io
import os
import time
from botocore.exceptions import ClientError

PROJECT_PREFIX = "Silverstone"
LAMBDA_FUNC_NAME = f"{PROJECT_PREFIX}-Backend"
EVENTS_TABLE_NAME = "Events_Table"
BOOKINGS_TABLE_NAME = "Bookings_Table"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_JS_PATH = os.path.join(BASE_DIR, "backend-lambda", "index.js")

session = boto3.Session()
sts = session.client("sts")
lam = session.client("lambda")

try:
    account_id = sts.get_caller_identity()["Account"]
except Exception:
    account_id = "Unknown"

role_arn = f"arn:aws:iam::{account_id}:role/LabRole"

print("=" * 60)
print(f"⚡ [Step 3] ติดตั้ง/อัปเดต AWS Lambda ({LAMBDA_FUNC_NAME})")
print("=" * 60)

while True:
    stripe_key = input("👉 กรอก Stripe Secret Key (ขึ้นต้นด้วย sk_) : ").strip()
    if stripe_key.startswith("sk_"):
        break
    print("  ❌ จำเป็นต้องระบุ Stripe Secret Key ที่ขึ้นต้นด้วย sk_")

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
    print(f"  ⏳ กำลังสร้าง Lambda Function: {LAMBDA_FUNC_NAME}...")
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
        print(f"  ℹ️ พบฟังก์ชันเดิม กำลังอัปเดตโค้ดและค่า Environment Variables...")
        lam.update_function_code(FunctionName=LAMBDA_FUNC_NAME, ZipFile=zip_bytes)
        time.sleep(3)
        lam_res = lam.update_function_configuration(
            FunctionName=LAMBDA_FUNC_NAME,
            Timeout=15,
            Environment={"Variables": env_vars}
        )
        lambda_arn = lam_res["FunctionArn"]
        print(f"  ✅ อัปเดต Lambda Function สำเร็จ : {LAMBDA_FUNC_NAME}")
    else:
        raise

print("-" * 60)
print(f"🚀 Lambda ARN : {lambda_arn}")
print("=" * 60)
print("🎉 เสร็จสิ้นขั้นตอนที่ 3 (Lambda)")
print("=" * 60)
