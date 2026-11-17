import boto3
import json
import os
from botocore.exceptions import ClientError

EVENTS_TABLE_NAME = "Events_Table"
BOOKINGS_TABLE_NAME = "Bookings_Table"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED_JSON_PATH = os.path.join(BASE_DIR, "backend-lambda", "seed-events.json")

session = boto3.Session()
dynamodb = session.client("dynamodb")
ddb_resource = session.resource("dynamodb")

print("=" * 60)
print("📦 [Step 1] ติดตั้ง Amazon DynamoDB (Events & Bookings)")
print("=" * 60)

def create_table_if_not_exists(params):
    tname = params["TableName"]
    try:
        dynamodb.create_table(**params)
        print(f"  ⏳ กำลังสร้างตาราง {tname}...")
        dynamodb.get_waiter("table_exists").wait(TableName=tname)
        print(f"  ✅ สร้างตาราง {tname} สำเร็จ")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceInUseException":
            print(f"  ℹ️ ตาราง {tname} มีอยู่แล้ว")
        else:
            raise

# 1. Events_Table
create_table_if_not_exists({
    "TableName": EVENTS_TABLE_NAME,
    "KeySchema": [{"AttributeName": "event_id", "KeyType": "HASH"}],
    "AttributeDefinitions": [{"AttributeName": "event_id", "AttributeType": "S"}],
    "BillingMode": "PAY_PER_REQUEST",
})

# 2. Bookings_Table
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

# 3. นำเข้าข้อมูล Seed
if os.path.exists(SEED_JSON_PATH):
    with open(SEED_JSON_PATH, "r", encoding="utf-8") as f:
        seed_items = json.load(f)
    table = ddb_resource.Table(EVENTS_TABLE_NAME)
    for item in seed_items:
        table.put_item(Item=item)
    print(f"  ✅ นำเข้าข้อมูลอีเวนต์เริ่มต้น {len(seed_items)} รายการเรียบร้อย")
else:
    print(f"  ⚠️ ไม่พบไฟล์ {SEED_JSON_PATH}")

print("=" * 60)
print("🎉 เสร็จสิ้นขั้นตอนที่ 1 (DynamoDB)")
print("=" * 60)
