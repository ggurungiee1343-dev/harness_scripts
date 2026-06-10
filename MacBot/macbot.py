import os
import sys
import asyncio
import unicodedata
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from telegram.constants import ChatAction

# 1. 설정 및 경로 로드
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

# 2. 전문 모듈 로드
try:
    from modules import (
        wiki_manager, file_manager, memory_engine, 
        system_monitor, news_engine
    )
    import logic_engine
    print("✅ 맥봇(MacBot) 전용 모듈 및 로직 엔진 로드 완료")
except ImportError as e:
    print(f"⚠️ 모듈 로드 중 오류 발생: {e}")

# 3. 인스턴스 초기화
wiki = wiki_manager.WikiManager(str(config.WORK_SPACE))
file_m = file_manager.FileManager(str(config.WORK_SPACE))
memory = memory_engine.MemoryEngine(str(config.WORK_SPACE))
sys_mon = system_monitor.SystemMonitor(threshold_gb=config.MEM_THRESHOLD_GB)
news = news_engine.NewsEngine(str(config.WORK_SPACE))

# 로직 엔진 초기화
logic = logic_engine.LogicEngine(str(config.WORK_SPACE), modules={
    "system_monitor": sys_mon,
    "news_engine": news,
    "memory_engine": memory,
    "wiki_manager": wiki,
    "file_manager": file_m
})

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_user.id != config.ALLOWED_ID: 
        return
        
    user_text = update.message.text
    print(f"[📩 맥봇 수신] {user_text}")

    # 1. '입력 중...' 상태 표시
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    # 2. 로직 엔진을 통한 응답 생성 (LLM 호출 없음)
    response_text = logic.process_message(user_text)

    # 3. 응답 전송 (NFC 정규화 포함)
    await update.message.reply_text(unicodedata.normalize('NFC', response_text))

def main():
    # MACBOT_TOKEN 사용 (config에 추가됨)
    token = getattr(config, "MACBOT_TOKEN", None)
    if not token:
        print("❌ MACBOT_TOKEN이 설정되지 않았습니다. config.py를 확인하세요.")
        return

    app = Application.builder().token(token).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    print("🚀 맥봇(MacBot) v1.0 가동 중... (LLM 없는 지능형 자동화)")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
