"""handlers._system_ops — 핸드오프/검색/봇재시작 명령어 (2026-06-09 _system.py에서 분리)"""
import os, subprocess, asyncio
from telegram import Update
from telegram.ext import ContextTypes
from handlers._base import (router, logger, add_to_history, _call_llm, safe_reply, safe_edit,
    _get_mem_info, check_user, BASE_DIR, _reply_long)

async def cmd_handoff(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/handoff [save|load|list|latest|delete] [session_id] — 세션 핸드오프 관리"""
    if not await check_user(update):
        return

    try:
        from modules.dialectic_layer import (
            save_handoff, load_handoff, get_latest_handoff,
            list_handoffs, delete_handoff
        )
    except Exception as e:
        await safe_reply(update.message, f"❌ 핸드오프 모듈 로드 실패: {e}")
        return

    if not context.args or context.args[0] == "list":
        handoffs = list_handoffs()
        if not handoffs:
            await safe_reply(update.message, "📭 저장된 핸드오프가 없습니다.")
            return
        lines = ["**📋 핸드오프 목록**\n"]
        for h in handoffs:
            lines.append(
                f"- `{h['session_id'][:24]}` | {h['title'] or '(제목 없음)'} | "
                f"{h['system_mode'] or '-'} | {h['updated_at'][:16]}"
            )
        await safe_reply(update.message, "\n".join(lines), parse_mode='HTML')

    elif context.args[0] == "latest":
        h = get_latest_handoff()
        if not h:
            await safe_reply(update.message, "📭 저장된 핸드오프가 없습니다.")
            return
        lines = [
            f"**📌 최근 핸드오프**",
            f"세션: `{h['session_id'][:32]}`",
            f"제목: {h['title'] or '(없음)'}",
            f"모드: {h['system_mode'] or '-'}",
            f"갱신: {h['updated_at'][:16]}",
        ]
        if h.get("summary"):
            lines.append(f"\n**요약:**\n{h['summary'][:500]}")
        if h.get("messages"):
            lines.append(f"\n**메시지:** {len(h['messages'])}개")
        await safe_reply(update.message, "\n".join(lines), parse_mode='HTML')

    elif context.args[0] == "save":
        session_id = context.args[1] if len(context.args) > 1 else "manual_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        title = " ".join(context.args[2:]) if len(context.args) > 2 else ""
        h_id = save_handoff(
            session_id=session_id,
            title=title or f"수동 저장 ({datetime.now().strftime('%m/%d %H:%M')})",
            summary=f"수동 핸드오프 저장. 시간: {datetime.now().isoformat(timespec='seconds')}",
            system_mode=get_current_mode(),
        )
        await safe_reply(update.message, 
            f"✅ 핸드오프 저장 완료\n세션: `{session_id}`\nID: `{h_id}`",
            parse_mode='HTML'
        )

    elif context.args[0] == "load":
        if len(context.args) < 2:
            await safe_reply(update.message, "⚠️ 세션 ID를 입력하세요: `/handoff load <session_id>`")
            return
        session_id = context.args[1]
        h = load_handoff(session_id=session_id)
        if not h:
            await safe_reply(update.message, f"⚠️ `{session_id}` 핸드오프를 찾을 수 없습니다.")
            return
        lines = [
            f"**📂 핸드오프 로드**",
            f"세션: `{h['session_id'][:32]}`",
            f"제목: {h.get('title', '') or '(없음)'}",
            f"모드: {h.get('system_mode', '') or '-'}",
            f"갱신: {h['updated_at'][:16]}",
        ]
        if h.get("messages"):
            lines.append(f"\n**메시지 ({len(h['messages'])}개):**")
            for msg in h["messages"][-5:]:
                content = msg["content"][:100]
                icon = "🧑" if msg["role"] == "user" else "🤖"
                lines.append(f"{icon} {content}")
        await safe_reply(update.message, "\n".join(lines), parse_mode='HTML')

    elif context.args[0] == "delete":
        if len(context.args) < 2:
            await safe_reply(update.message, "⚠️ 삭제할 세션 ID를 입력하세요: `/handoff delete <session_id>`")
            return
        ok = delete_handoff(session_id=context.args[1])
        if ok:
            await safe_reply(update.message, f"✅ 핸드오프 삭제 완료: `{context.args[1]}`", parse_mode='HTML')
        else:
            await safe_reply(update.message, f"⚠️ `{context.args[1]}` 핸드오프를 찾을 수 없습니다.")
    else:
        await safe_reply(update.message, 
            f"⚠️ 알 수 없는 액션: `{context.args[0]}`\n"
            f"사용법: `/handoff [list|latest|save|load|delete]`",
            parse_mode='HTML'
        )


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/search <질의어> — FTS5 전문 검색 (세션 요약/의사결정/핸드오프)"""
    if not await check_user(update):
        return

    if not context.args:
        await safe_reply(update.message, 
            "🔍 **FTS5 전문 검색**\n\n"
            "사용법: `/search <질의어>`\n"
            "예: `/search 모델 전환`\n"
            "     `/search \"DeepSeek 오류\"`",
            parse_mode='HTML'
        )
        return

    query = " ".join(context.args)
    try:
        from modules.dialectic_layer import search_all, rebuild_index
    except Exception as e:
        await safe_reply(update.message, f"❌ FTS5 모듈 로드 실패: {e}")
        return

    if query == "--rebuild":
        rebuild_index()
        await safe_reply(update.message, "✅ FTS5 인덱스 재구축 완료")
        return

    results = search_all(query, limit=5)
    if not results:
        await safe_reply(update.message, f"🔍 `{query}` 검색 결과가 없습니다.")
        return

    source_icons = {"summaries": "📝", "decisions": "⚖️", "handoffs": "📌"}
    lines = [f"**🔍 '{query}' 검색 결과 ({len(results)}건)**\n"]
    for r in results:
        icon = source_icons.get(r["source"], "📄")
        label = r["source"][:-1].capitalize() if r["source"] != "summaries" else "요약"
        snippet = r.get("snippet", "")
        if not snippet and r.get("row"):
            row = r["row"]
            if r["source"] == "summaries":
                snippet = (row.get("summary", "") or "")[:200]
            elif r["source"] == "decisions":
                snippet = (row.get("title", "") or "")[:200]
            elif r["source"] == "handoffs":
                snippet = (row.get("summary", "") or "")[:200]
        lines.append(f"{icon} **[{label}]** {snippet[:300]}")
        id_val = r.get("id_value", "")
        if id_val:
            lines.append(f"   `{id_val[:32]}`")

    await safe_reply(update.message, "\n".join(lines), parse_mode='HTML')


cmd_fs = cmd_search  # /fs alias for FTS5 search


async def cmd_restart_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/restart_bot — 텔레그램 봇 재시작"""
    if not await check_user(update):
        return

    import subprocess
    from pathlib import Path
    
    BOT_PLIST = Path.home() / "Library/LaunchAgents/com.hermes.bot.plist"
    
    msg = await safe_reply(update.message, "🔄 <b>Bot 재시작 진행 중...</b>", parse_mode='HTML')
    
    if not BOT_PLIST.is_file():
        await safe_edit(msg, f"❌ 에러: {BOT_PLIST} 를 찾을 수 없습니다.")
        return

    await safe_edit(msg, "🔁 <b>Bot 재시작 명령 전송됨</b>\n현재 프로세스 종료 후 nohup으로 재실행합니다.", parse_mode='HTML')

    import asyncio, os, signal
    SCRIPTS = str(Path.home() / "Applications/Mjauto/Scripts")
    PYTHON  = "/usr/local/bin/python3"

    async def _restart_routine():
        await asyncio.sleep(1)
        # OLD 먼저 종료, NEW는 1초 후 시작 — 동시 실행으로 인한 Telegram 409 Conflict 방지
        # (두 인스턴스 동시 폴링 → 신규가 Conflict 수신 → Conflict 핸들러로 신규도 사망하는 문제 수정)
        import subprocess as sp
        old_pid = os.getpid()
        sp.Popen(
            f"sleep 1 && {PYTHON} -u {SCRIPTS}/hermes_local.py"
            f" >> {SCRIPTS}/hermes_launchd.log"
            f" 2>> {SCRIPTS}/hermes_launchd.error.log",
            shell=True,
            start_new_session=True,
        )
        os.kill(old_pid, signal.SIGTERM)

    asyncio.create_task(_restart_routine())

