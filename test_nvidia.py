import asyncio
import os
import sys

sys.path.insert(0, "/Users/bluesea/Applications/Mjauto/Scripts")
sys.path.insert(0, "/Users/bluesea/Applications/Mjauto/Scripts/modules")

from harness_agent import _call_nvidia, get_sys_prompt

async def main():
    messages = [
        {"role": "system", "content": get_sys_prompt()},
        {"role": "user", "content": "오늘 서울 날씨 어때?"}
    ]
    try:
        res, engine = await _call_nvidia(messages)
        print("Response:", res)
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    asyncio.run(main())
