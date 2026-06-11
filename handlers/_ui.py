"""handlers._ui — UI 명령어"""
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from handlers._base import (router, cove_engine_instance, _audit_engine, safe_reply, safe_edit,
    logger, add_to_history, _call_llm, _get_mem_info,
    check_user, _make_keyboard)

async def cmd_verify_harness(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/verify_harness — Hermes 구조 건강 자동 진단

    AI 의견이 아닌 실제 수치로 시스템 구조의 탄탄함을 측정한다.
    - 파일 비대화 (줄 수 기반 🔴🟡🟢 등급)
    - 모듈 의존성 (SRP 위반 감지)
    - 문서 동기화 (02_스크립트 정보.md ↔ 실제 파일 표류 감지)
    - 서비스 상태 (launchctl 기반)
    - 메모리 파일 건강 (L1/L2/L3 JSON 검증)
    - 임시 파일 잔재 (.bak/_old 탐지)
    """
    if not await check_user(update):
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 진단 실행", callback_data="verify_harness_run")],
        [InlineKeyboardButton("❌ 취소", callback_data="verify_harness_cancel")]
    ])

    await safe_reply(update.message, 
        "🔍 **Hermes 구조 건강 자동 진단**\n\n"
        "6개 항목을 측정합니다:\n"
        "• 파일 비대화 (줄 수 기반)\n"
        "• 모듈 의존성 (SRP 위반)\n"
        "• 문서 동기화\n"
        "• 서비스 상태\n"
        "• 메모리 파일 건강\n"
        "• 임시 파일 잔재\n\n"
        "진단을 실행하시겠습니까?",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


async def callback_verify_harness_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """verify_harness 진단 실행 callback"""
    query = update.callback_query
    await query.answer()

    await query.edit_message_text("🔍 진단 중... (이 과정은 30초 정도 걸립니다)")

    try:
        from modules.harness_verifier import run_full_check
        report = run_full_check()
        await query.edit_message_text(report, parse_mode='HTML')
    except Exception as e:
        logger.error(f"verify_harness 실패: {e}")
        await query.edit_message_text(f"❌ 진단 실패: {e}")


async def callback_verify_harness_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """verify_harness 취소 callback"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ 진단이 취소되었습니다.")
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """웰컴 메시지 및 메뉴 버튼 배치"""
    if not await check_user(update):
        return

    welcome_text = (
        '👋 안녕하십니까, 박사님! **Harness V2.5 자율 관제 센터**에 오신 것을 환영합니다.\n\n'
        '옵시디언 위키 교차 검증과 AI 자율 에러 수정 엔진이 백그라운드에 완벽하게 빌드되었습니다.\n'
        '아래 버튼 메뉴나 명령어를 사용하여 관제를 시작해 주십시오.'
    )

    await safe_reply(update.message, 
        welcome_text,
        reply_markup=_make_keyboard(),
        parse_mode='HTML'
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """명령어 도움말 출력"""
    if not await check_user(update):
        return

    help_text = (
        '🤖 <b>헤르메스 v9.2 전체 명령어 가이드</b>\n\n'

        '🔹 <b>질의응답 &amp; 팩트체크</b>\n'
        '• <code>/ask [질문]</code> — CoVe 팩트체크 기반 Q&amp;A (위키 연동)\n'
        '• <code>/cove [질문]</code> — 독립형 CoVe 4단계 심층 팩트체크 (devil: 반론 모드)\n'
        '• <code>/web [URL] [질문]</code> — 웹페이지 내용 요약/분석\n'
        '• <code>/search [검색어]</code> — FTS5 전문 검색 (세션요약/의사결정/핸드오프)\n'
        '• <code>/grill [문서경로] [질문]</code> — Vault 문서 읽고 LLM 기반 Q&amp;A\n\n'

        '🧠 <b>메모리 &amp; Dreaming</b>\n'
        '• <code>/memory</code> — L1/L2/L3 메모리 3계층 상태 조회\n'
        '• <code>/memory health</code> — 메모리 정제 상태 (보유율/forget 대상)\n'
        '• <code>/memory forget</code> — 소멸 대상 확인 (dry-run) / <code>confirm</code>으로 실행\n'
        '• <code>/dreaming</code> — 대화/작업 → Journal/Memory/hot.md 자동 분배 (L3 성장)\n'
        '• <code>/clip [내용]</code> — 텍스트를 Clippings 폴더에 .md로 즉시 저장\n'
        '• <code>/goal [목표]</code> — 장기 목표 설정 (Dreaming 시 헌법과 함께 감사)\n\n'

        '📈 <b>주식 분석 (V_FINAL 전략)</b>\n'
        '• <code>/market</code> — 시장 상태 (NASDAQ/VIX/섹터 3중 필터)\n'
        '• <code>/scan</code> — 4티어 요약 스캔 (🟢매수 🟡대기 🔵구조강함 ⬜SEPA)\n'
        '• <code>/scan buy</code> — 🟢 최종 매수 신호 전체\n'
        '• <code>/scan watch</code> — 🟡 진입 대기 (SEPA+LEADER, 타이밍만 없는 종목)\n'
        '• <code>/scan strong</code> — 🔵 구조 강함 (정배열+ADX≥40, 52W 미달 포함)\n'
        '• <code>/scan sepa</code> — ⬜ SEPA 통과 전체 목록 (알파점수 순)\n'
        '• <code>/stock TICKER [계좌] [리스크]</code> — 단일 종목 상세 분석 + 포지션 사이징\n'
        '• <code>/watchlist [add/rm/list/scan] [TICKER]</code> — 관심종목 관리\n'
        '• <code>/positions</code> — 열린 포지션 확인\n'
        '• <code>/result TICKER 매수가 매도가 [수량] [메모]</code> — 매매 결과 기록\n'
        '• <code>/backtest</code> — 누적 매매 성과 통계 (승률/RR/평균 수익률)\n'
        '• 차트 사진 전송 → V_FINAL 기준 차트 패턴 분석\n\n'

        '📦 <b>Vault (옵시디언 보관소 관리)</b>\n'
        '• <code>/vault check</code> — 종합 정합성 진단 (캐시/프론트매터/중복)\n'
        '• <code>/vault duplicates</code> — 중복 문서 상세 스캔\n'
        '• <code>/vault graph</code> — Graphify 그래프 분석 (고립 문서/허브 노드)\n\n'

        '🤖 <b>자동화 시스템 (Self-Harness)</b>\n'
        '• <b>Wiki Git</b> — 위키 버전 관리 (306개 문서, 매 작업 후 자동 커밋)\n'
        '• <b>SessionStart Hook</b> — Claude Code 새 세션 시작 시 01_hot.md 자동 주입\n'
        '• <b>Wiki 자동 lint</b> — 매일 새벽 3:17 미커밋/임시파일 감지 → 알림\n'
        '• <b>WeaknessMiner</b> — 명령어 반복 실패 자동 감지 (3회↑ → 텔레그램 알림 + 에러보고서 기록)\n'
        '• 상세 사용법: <code>wiki/00_Meta/자동화_시스템_사용법.md</code>\n\n'

        '📄 <b>논문 &amp; 연구 (Research)</b>\n'
        '• <code>/paper humanize [텍스트]</code> — 법률/논문 문체 학술 변환\n'
        '• <code>/paper draft [주제]</code> — 논문 초안 생성 (개요→초안→문체 3단계)\n'
        '• <code>/paper review [문서]</code> — 논문 검토\n'
        '• <code>/paper list</code> — 저장된 논문 목록\n'
        '• <code>/paper compare [id1] [id2]</code> — 두 논문/번들 비교\n'
        '• <code>/research [질문]</code> — Knowledge Mesh 전체 파이프라인 (로컬검색+타임라인+클러스터)\n'
        '• <code>/research local [질문]</code> — 로컬 위키 검색만\n'
        '• <code>/research topics</code> — 전체 주제 목록\n'
        '• <code>/research stats</code> — Knowledge Mesh 통계\n\n'

        '🪴 <b>지식 정원 &amp; 인덱싱</b>\n'
        '• <code>/ingest</code> — Clippings → 위키 자동 이관 (즉시 피드백 + 백그라운드)\n'
        '• <code>/ingest scan</code> — 루트 방치 파일 스캔 (태그+요약만)\n'
        '• <code>/ingest interrogate</code> — 사전 질문 생성 모드\n'
        '• <code>/retry</code> — 마지막 실패 작업 정보 확인 &amp; 재시도 안내\n'
        '• <code>/recent</code> — 위키 최근 수정 문서 5개 목록 및 요약\n\n'

        '⚙️ <b>시스템 &amp; 모델 제어</b>\n'
        '• <code>/status</code> — 시스템 건강 진단 (CPU/메모리/디스크/LLM)\n'
        '• <code>/verify_harness</code> — 🆕 구조 건강 자동 진단 (파일비대화/SRP/문서표류/서비스/메모리/임시파일 → 점수화)\n'
        '• <code>/model list</code> — 사용 가능한 모델 목록\n'
        '• <code>/model switch [모드]</code> — 실시간 LLM 모델 전환\n'
        '• <code>/caveman [on|off|status]</code> — 시스템 프롬프트 압축 (토큰 75% 절감)\n'
        '• <code>/audit</code> — 시스템 감사 (포트/SSH키/환경변수)\n'
        '• <code>/secreview [경로]</code> — 보안 코드 리뷰 (git diff 분석)\n'
        '• <code>/restart_bot</code> — 텔레그램 봇 재시작\n\n'

        '🤝 <b>위임 &amp; 오케스트레이션</b>\n'
        '• <code>/delegate [작업 설명]</code> — 백그라운드 비동기 위임 실행\n'
        '• <code>/orchestrate [목표]</code> — Multi-Agent 오케스트레이션 (목표 분해→병렬 실행→합성)\n'
        '• <code>/kanban</code> — Kanban 보드 관리\n\n'

        '💾 <b>세션 핸드오프</b>\n'
        '• <code>/handoff save</code> — 현재 세션 저장\n'
        '• <code>/handoff load [id]</code> — 세션 복원\n'
        '• <code>/handoff list</code> — 저장된 세션 목록\n'
        '• <code>/handoff latest</code> — 최근 세션 불러오기\n\n'

        '🔧 <b>Bash &amp; 실행</b>\n'
        '• <code>/exec [명령어]</code> — 자율 에러 복구형 Bash 실행 (SKILL.md 자동 학습)\n\n'

        '📋 <b>메타 &amp; 브리핑</b>\n'
        '• <code>/claude_brief</code> — Claude Code용 시스템 브리핑(claude_briefing.md) 생성\n'
        '• <code>/tag [텍스트]</code> — 텍스트 자동 태그 추출\n\n'

        '💡 버튼 메뉴에서도 주요 기능에 바로 접근 가능합니다.'
    )

    await safe_reply(update.message, help_text, parse_mode='HTML')
