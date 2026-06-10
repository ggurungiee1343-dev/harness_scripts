import os
import sys
import requests
from dotenv import load_dotenv

# ~/.hermes/.env 로딩
env_path = "/Users/bluesea/.hermes/.env"
load_dotenv(env_path)

bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
allowed_users = os.getenv("TELEGRAM_ALLOWED_USERS")

if not bot_token or not allowed_users:
    print("Error: TOKEN or ALLOWED_USERS not found in .env")
    sys.exit(1)

chat_id = allowed_users.split(",")[0].strip()

# 인자 처리 (전송할 텍스트 입력받음)
if len(sys.argv) < 2:
    print("Usage: python3 send_telegram_msg.py \"Your message here\"")
    sys.exit(1)

message_text = sys.argv[1]

url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
payload = {
    "chat_id": chat_id,
    "text": message_text,
    "parse_mode": "HTML"
}

try:
    response = requests.post(url, json=payload)
    if response.status_code == 200:
        print("Success: Message sent to Telegram!")
    else:
        print(f"Failed: {response.status_code} - {response.text}")
except Exception as e:
    print(f"Error: {e}")
