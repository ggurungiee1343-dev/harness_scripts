"""
hermes_harness.py — ACID 에이전트 제어 하네스 핸들러 (Harness V2.5)
==============================================================
/harness, /hdod, /hstatus, /hrollback 명령어 및
인라인 버튼 콜백 수신 핸들러(_handle_harness_callback)를 처리합니다.
"""

import sys
from pathlib import Path
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

# 하네스 스크립트 경로 추가
sys.path.append("/Users/bluesea/Applications/Mjauto/Scripts")
try:
    from agent_harness import AgentHarness
except ImportError:
    pass

# hermes_local 공통 설정 및 유틸 임포트
from hermes_local import (
    check_user,
    HARNESS_ENABLED,
    HARNESS_SESSIONS,
    HCFG
)


async def cmd_harness(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /harness [작업명] — ACID 하네스 파이프라인 시작
    Layer 1+2(Cold-Start) → Layer 3(신선도) → Layer 5(Git 격리) 순서로 실행 후
    에이전트 작업 대기 → DoD 검증 버튼 제공
    """
    if not await check_user(update): return

    if not HARNESS_ENABLED:
        await update.message.reply_text(
            "⚠️ 하네스 모듈을 찾을 수 없습니다.\n"
            "`agent_harness.py` 와 `harness_config.py` 를 Scripts/ 폴더에 배치해 주세요."
        )
        return

    if not context.args:
        guide = (
            "🛡️ *에이전트 제어 하네스 (ACID v2.5)*\n\n"
            "사용법: `/harness [작업명세]`\n\n"
            "*6-레이어 파이프라인:*\n"
            "• L1+2: Cold-Start 준비도 + 필수 문서 확인\n"
            "• L3: 지식 부패 신선도 체크\n"
            "• L5: Git 격리 브랜치 생성 (Isolation)\n"
            "• ⏸ 에이전트 작업 구간 (버튼 클릭으로 재개)\n"
            "• L4: 기계 검증 DoD 실행\n"
            "• L5: Atomic 커밋 또는 완전 롤백\n"
            "• L6: 실패 시 5-레이어 진단 루프\n\n"
            "기타: `/hdod` `/hstatus` `/hrollback`"
        )
        await update.message.reply_text(guide, parse_mode="Markdown")
        return

    task_name = " ".join(context.args)
    msg = await update.message.reply_text(
        f"🛡️ *하네스 파이프라인 시작*\n작업: `{task_name}`\n\n⏳ Layer 1+2+3 검사 중...",
        parse_mode="Markdown"
    )

    import asyncio as _asyncio, uuid as _uuid
    loop = _asyncio.get_event_loop()

    def _layers_123():
        h = AgentHarness()
        if not h.check_readiness():
            return False, None, "❌ [L2] Cold-Start 실패 — 필수 문서 누락"
        h.check_staleness()
        branch = h.prepare_task_environment(task_name)
        return True, branch, ""

    ok_flag, branch, detail = await loop.run_in_executor(None, _layers_123)

    if not ok_flag:
        await msg.edit_text(
            f"🛡️ *하네스 파이프라인*\n\n{detail}\n\n"
            "👉 AGENTS\\.md 및 스크립트\\_정보\\.md 를 먼저 생성하세요.",
            parse_mode="Markdown"
        )
        return

    sk = str(_uuid.uuid4())[:8]
    HARNESS_SESSIONS[sk] = {"branch": branch, "task": task_name}

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ DoD 검증 실행 (작업 완료 후 클릭)", callback_data=f"harness_dod_{sk}")],
        [InlineKeyboardButton("🔄 작업 취소 (롤백)", callback_data=f"harness_rollback_{sk}")],
    ])
    await msg.edit_text(
        f"🛡️ *하네스 격리 환경 준비 완료*\n\n"
        f"✅ L1\\+2: Cold-Start 통과\n"
        f"✅ L3: 신선도 체크 완료\n"
        f"✅ L5: 격리 브랜치 생성\n"
        f"   `{branch}`\n\n"
        f"📝 작업: `{task_name}`\n\n"
        f"에이전트 작업 완료 후 아래 버튼을 눌러 DoD 검증을 실행하세요.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


async def cmd_hdod(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/hdod — 현재 상태에서 DoD 검증만 즉시 실행"""
    if not await check_user(update): return
    if not HARNESS_ENABLED:
        await update.message.reply_text("⚠️ 하네스 모듈이 없습니다."); return

    msg = await update.message.reply_text("🧪 *DoD 기계 검증 실행 중...*", parse_mode="Markdown")
    import asyncio as _asyncio
    verified, detail = await _asyncio.get_event_loop().run_in_executor(
        None, lambda: AgentHarness().run_verification()
    )
    if verified:
        await msg.edit_text(
            "🧪 *DoD 검증 결과*\n\n✅ 모든 필수 검증 통과\n"
            "커밋하거나 `/harness [작업명]` 으로 정식 파이프라인을 실행하세요.",
            parse_mode="Markdown"
        )
    else:
        await msg.edit_text(
            f"🧪 *DoD 검증 결과*\n\n❌ 검증 실패\n\n"
            f"```\n{detail[:600]}\n```\n\n진단 확인: `/hstatus`",
            parse_mode="Markdown"
        )


async def cmd_hstatus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/hstatus — 마지막 진단 로그 출력"""
    if not await check_user(update): return
    if not HARNESS_ENABLED:
        await update.message.reply_text("⚠️ 하네스 모듈이 없습니다."); return

    import json as _json
    log_path = Path(HCFG.DIAGNOSTIC_LOG)
    if not log_path.exists():
        await update.message.reply_text("📊 진단 로그 없음 (아직 실패가 발생하지 않았습니다.)"); return
    try:
        logs = _json.loads(log_path.read_text(encoding="utf-8"))
    except Exception:
        await update.message.reply_text("📊 진단 로그 파일이 손상되었습니다."); return
    if not logs:
        await update.message.reply_text("📊 진단 로그가 비어 있습니다."); return

    last = logs[-1]
    lines = [
        "📊 *마지막 하네스 진단 보고서*", "",
        f"🕒 `{last.get('timestamp','?')}`",
        f"📌 세션 `{last.get('session_id','?')}`",
        f"🔀 브랜치 `{last.get('failed_branch','?')}`",
        f"📋 작업 `{last.get('task_name','?')}`", "",
        "*에러 요약:*",
        f"```\n{str(last.get('raw_error',''))[:400]}\n```", "",
        "*5대 레이어 점검:*",
    ]
    for layer, d in last.get("triage_guide", {}).items():
        lines.append(f"• *{layer}*: {d.get('question','')}")
    lines.append(f"\n(총 {len(logs)}개 로그 중 마지막)")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_hrollback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/hrollback — 긴급 롤백 (미커밋 변경 전체 stash)"""
    if not await check_user(update): return
    if not HARNESS_ENABLED:
        await update.message.reply_text("⚠️ 하네스 모듈이 없습니다."); return

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔴 롤백 확인 (취소 불가)", callback_data="harness_emergency_rollback")
    ]])
    await update.message.reply_text(
        "⚠️ *긴급 롤백*\n\n"
        "미커밋 변경사항을 모두 `git stash` 로 제거하고 메인 브랜치로 복귀합니다.\n"
        "정말 실행하시겠습니까?",
        reply_markup=keyboard, parse_mode="Markdown"
    )


async def _handle_harness_callback(query, context):
    """하네스 InlineKeyboard 콜백 전용 처리기"""
    import asyncio as _asyncio
    data = query.data

    if data.startswith("harness_dod_"):
        sk = data.replace("harness_dod_", "")
        session = HARNESS_SESSIONS.get(sk)
        if not session:
            await query.edit_message_text(query.message.text + "\n\n⚠️ 세션 만료 또는 이미 처리됨")
            return
        await query.edit_message_text(query.message.text + "\n\n⏳ L4 DoD 기계 검증 실행 중...")

        def _vf():
            h = AgentHarness()
            v, detail = h.run_verification()
            ok = h.finalize(session["branch"], v, session["task"])
            if not ok:
                h.trigger_diagnostic_loop(session["branch"], detail, session["task"])
            return ok, detail

        success, detail = await _asyncio.get_event_loop().run_in_executor(None, _vf)
        HARNESS_SESSIONS.pop(sk, None)
        suffix = (
            f"\n\n{'='*26}\n✅ DoD 통과 → Atomic 커밋 완료\n브랜치: `{session['branch']}`"
            if success else
            f"\n\n{'='*26}\n❌ DoD 실패 → 완전 롤백 및 격리 브랜치 폐기 완료\n진단: /hstatus\n`{detail[:300]}`"
        )
        await query.edit_message_text(query.message.text + suffix, parse_mode="Markdown")

    elif data.startswith("harness_rollback_"):
        sk = data.replace("harness_rollback_", "")
        session = HARNESS_SESSIONS.pop(sk, None)
        branch = session["branch"] if session else "현재 브랜치"

        def _rb():
            h = AgentHarness()
            h._run("git reset --hard HEAD")
            h._run(f"git checkout {h.current_branch}")
            if branch != h.current_branch and branch.startswith("agent/"):
                h._run(f"git branch -D {branch}")

        await _asyncio.get_event_loop().run_in_executor(None, _rb)
        await query.edit_message_text(
            query.message.text + f"\n\n{'='*26}\n🔄 롤백 및 에이전트 브랜치 폐기 완료: `{branch}`",
            parse_mode="Markdown"
        )

    elif data == "harness_emergency_rollback":
        def _emerg():
            h = AgentHarness()
            h._run("git add -A && git stash")
            h._run(f"git checkout {h.current_branch}")
            return h.current_branch

        cb = await _asyncio.get_event_loop().run_in_executor(None, _emerg)
        await query.edit_message_text(
            f"🔄 *긴급 롤백 완료*\n\n복귀 브랜치: `{cb}`\n"
            f"복구하려면: `/exec git stash pop`",
            parse_mode="Markdown"
        )
