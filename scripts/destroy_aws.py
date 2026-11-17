import boto3
import sys
import time
from botocore.exceptions import ClientError

PROJECT_PREFIX = "Silverstone"
EVENTS_TABLE_NAME = "Events_Table"
BOOKINGS_TABLE_NAME = "Bookings_Table"
LAMBDA_FUNC_NAME = f"{PROJECT_PREFIX}-Backend"
API_NAME = f"{PROJECT_PREFIX}-API"
USER_POOL_NAME = f"{PROJECT_PREFIX}-Users"

session = boto3.Session()
sts = session.client("sts")
account_id = sts.get_caller_identity()["Account"]

dynamodb = session.client("dynamodb")
cognito = session.client("cognito-idp")
lam = session.client("lambda")
apigw = session.client("apigateway")

print("=" * 60)
print(f"🧹 เริ่มต้นลบทรัพยากรของ {PROJECT_PREFIX} (Account: {account_id})")
print("=" * 60)

auto_yes = "--yes" in sys.argv or "-y" in sys.argv
if not auto_yes:
    confirm = input("⚠️ ยืนยันการลบข้อมูลและทรัพยากร? พิมพ์ 'yes' เพื่อดำเนินการ : ").strip().lower()
    if confirm != "yes":
        print("❌ ยกเลิกการลบ")
        sys.exit(0)

print("\n[1/4] กำลังลบ Amazon API Gateway...")
apis = [a for a in apigw.get_rest_apis(limit=50).get("items", []) if a["name"] == API_NAME]
for idx, api in enumerate(apis):
    while True:
        try:
            apigw.delete_rest_api(restApiId=api["id"])
            print(f"  ✅ ลบ {API_NAME} ({api['id']}) สำเร็จ")
            if idx < len(apis) - 1:
                time.sleep(35)
            break
        except ClientError as e:
            if e.response["Error"]["Code"] == "TooManyRequestsException":
                time.sleep(35)
            else:
                raise

print("\n[2/4] กำลังลบ AWS Lambda Function...")
try:
    lam.delete_function(FunctionName=LAMBDA_FUNC_NAME)
    print(f"  ✅ ลบ {LAMBDA_FUNC_NAME} สำเร็จ")
except ClientError:
    print(f"  ℹ️ ไม่พบ Lambda Function ชื่อ {LAMBDA_FUNC_NAME}")

print("\n[3/4] กำลังลบ Amazon Cognito User Pool...")
for p in cognito.list_user_pools(MaxResults=50).get("UserPools", []):
    if p["Name"] == USER_POOL_NAME:
        pool_info = cognito.describe_user_pool(UserPoolId=p["Id"])["UserPool"]
        if "Domain" in pool_info:
            cognito.delete_user_pool_domain(Domain=pool_info["Domain"], UserPoolId=p["Id"])
            time.sleep(2)
        if pool_info.get("DeletionProtection") == "ACTIVE":
            cognito.update_user_pool(UserPoolId=p["Id"], DeletionProtection="INACTIVE")
        cognito.delete_user_pool(UserPoolId=p["Id"])
        print(f"  ✅ ลบ {USER_POOL_NAME} ({p['Id']}) สำเร็จ")

print("\n[4/4] กำลังลบตาราง Amazon DynamoDB...")
for tname in [EVENTS_TABLE_NAME, BOOKINGS_TABLE_NAME]:
    try:
        dynamodb.delete_table(TableName=tname)
        print(f"  ✅ ลบตาราง {tname} สำเร็จ")
    except ClientError:
        print(f"  ℹ️ ไม่พบตาราง {tname}")

print("\n" + "=" * 60)
print("🎉 ลบทรัพยากรหลังบ้านทั้งหมดเรียบร้อยแล้ว!")
print("=" * 60)