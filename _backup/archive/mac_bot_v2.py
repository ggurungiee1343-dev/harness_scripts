import os
import sys
import asyncio
import subprocess
import unicodedata
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from telegram.constants import ChatAction

# 1. 설정 및 경로 로드
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

# 2. 전문 모듈 및 엔진 로드
try:
    from modules import wiki_manager, file_manager, memory_engine, system_monitor, news_engine
    from MacBot import semantic_engine, logic_engine
    print("✅ 맥봇(MacBot) 전용 모듈 및 세만틱 엔진 로드 완료")
except ImportError as e:
    print(f"⚠️ 모듈 로드 중 오류 발생: {e}")
    # 로컬 경로에서 직접 임포트 시도 (fallback)
    import semantic_engine
    import logic_engine
    from modules import wiki_manager, file_manager, memory_engine, system_monitor, news_engine

# 3. 인스턴스 초기화
wiki = wiki_manager.WikiManager(str(config.WORK_SPACE))
file_m = file_manager.FileManager(str(config.WORK_SPACE))
memory = memory_engine.MemoryEngine(str(config.WORK_SPACE))
sys_mon = system_monitor.SystemMonitor(threshold_gb=config.MEM_THRESHOLD_GB)
news = news_engine.NewsEngine(str(config.WORK_SPACE))

# 세만틱 엔진 지연 로드 (메모리 절약)
semantic_instance = None
def get_semantic():
    global semantic_instance
    if semantic_instance is None:
        print("🧠 [Lazy Load] 세만틱 엔진(MPNet) 로딩 중...")
        from MacBot import semantic_engine
        semantic_instance = semantic_engine.SemanticEngine(str(config.WORK_SPACE))
    return semantic_instance

logic = logic_engine.LogicEngine(str(config.WORK_SPACE), modules={
    "system_monitor": sys_mon,
    "news_engine": news,
    "memory_engine": memory,
    "wiki_manager": wiki,
    "file_manager": file_m,
    "semantic_engine_lazy": get_semantic # 람다나 함수 형태로 전달 가능하도록 로직 엔진 수정 필요할 수 있음
})

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_user.id != config.ALLOWED_ID: 
        return
        
    user_text = update.message.text
    print(f"[📩 맥봇 수신] {user_text}")

    # 1. '분석 중' 상태 표시
    status_msg = await update.message.reply_text("🚀 맥봇 분석 중...")

    try:
        # 1. 세만틱 검색 시도 (명령어: @검색)
        if user_text.startswith("@검색"):
            query = user_text.replace("@검색", "").strip()
            results = get_semantic().search(query)
            
            if isinstance(results, str): # 에러 메시지인 경우
                response_text = results
            else:
                response_text = "🔍 **지식 가든 검색 결과:**\n\n"
                for path, snippet, score in results:
                    response_text += f"- **[{path}](file://{path})** (유사도: {score:.2f})\n"
                    response_text += f"  > {snippet}...\n\n"
        elif user_text.startswith("@복구"):
            await status_msg.edit_text("🚨 전 계통 복구 프로세스 가동... (LM Studio 포함)")
            # 비동기로 스크립트 실행
            script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "recovery_system.sh")
            subprocess.Popen(["bash", script_path])
            return # 스크립트가 봇을 종료하므로 응답 생략
        else:
            # 2. 일반 로직 엔진 처리
            response_text = logic.process_message(user_text)

        # 3. 응답 전송 및 상태 메시지 삭제
        await status_msg.delete()
        await update.message.reply_text(unicodedata.normalize('NFC', response_text))
    except Exception as e:
        await status_msg.edit_text(f"⚠️ 맥봇 내부 오류: {e}")

async def auto_heal_loop():
    """60초마다 시스템 건강 상태를 체크하고 필요시 자율 복구 가동"""
    script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "recovery_system.sh")
    while True:
        try:
            if sys_mon.auto_heal(script_path):
                print("🚨 [Auto-Heal] 메모리 임계값 도달. 복구 스크립트 실행 중...")
            await asyncio.sleep(60)
        except Exception as e:
            print(f"⚠️ Auto-Heal Loop Error: {e}")
            await asyncio.sleep(60)

async def main():
    # MACBOT_TOKEN (기존 CODEX_BOT_TOKEN) 사용
    token = getattr(config, "MACBOT_TOKEN", None)
    if not token:
        # 대비용으로 환경변수 직접 조회
        token = os.getenv("CODEX_BOT_TOKEN")

    if not token:
        print("❌ MACBOT_TOKEN이 설정되지 않았습니다.")
        return

    app = Application.builder().token(token).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    print("🚀 맥봇(MacBot) 가동 중... (Phase 5 NPU Engine)")
    
    # 백그라운드 루프 실행 (자가 치유)
    asyncio.create_task(auto_heal_loop())
    
    # run_polling 대신 직접 제어 (async main을 위해)
    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        # 루프 유지
        while True:
            await asyncio.sleep(3600)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
