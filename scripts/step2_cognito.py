import boto3

USER_POOL_NAME = "Silverstone-Users"
APP_CLIENT_NAME = "Silverstone-WebClient"

session = boto3.Session()
cognito = session.client("cognito-idp")

print("=" * 60)
print(f"🔐 [Step 2] ติดตั้ง Amazon Cognito ({USER_POOL_NAME})")
print("=" * 60)

user_pool_id = None
client_id = None

for p in cognito.list_user_pools(MaxResults=50).get("UserPools", []):
    if p["Name"] == USER_POOL_NAME:
        user_pool_id = p["Id"]
        break

if not user_pool_id:
    print(f"  ⏳ กำลังสร้าง User Pool: {USER_POOL_NAME}...")
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
    print(f"  ⏳ กำลังสร้าง App Client: {APP_CLIENT_NAME}...")
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

print("-" * 60)
print(f"🔑 User Pool ID : {user_pool_id}")
print(f"🔑 App Client ID: {client_id}")
print("=" * 60)
print("🎉 เสร็จสิ้นขั้นตอนที่ 2 (Cognito)")
print("=" * 60)
