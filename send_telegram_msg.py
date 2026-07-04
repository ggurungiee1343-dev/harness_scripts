import os
import sys
import requests
from dotenv import load_dotenv

sys.path.insert(0, "/Users/bluesea/Applications/Mjauto/Scripts")
import config  # noqa: E402 — ALLOWED_ID (Mjauto/.env)

# hermes_local.py(라이브 봇)와 동일한 우선순위: HERMES1_BOT_TOKEN 우선,
# ~/.hermes/.env 기준. config.TELEGRAM_TOKEN(HARNESS_BOT_TOKEN)은 이 봇과 무관 — 2026-07-02 확인
load_dotenv("/Users/bluesea/.hermes/.env")
bot_token = os.getenv("HERMES1_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
chat_id = config.ALLOWED_ID

if not bot_token or not chat_id:
    print("Error: HERMES1_BOT_TOKEN(~/.hermes/.env) or ALLOWED_USER_ID(Mjauto/.env) not found")
    sys.exit(1)

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
