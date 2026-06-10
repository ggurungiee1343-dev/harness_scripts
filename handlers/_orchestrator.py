"""handlers._orchestrator — Multi-Agent 오케스트레이션

복잡한 목표를 독립적인 하위 작업으로 분해 → 병렬 실행 → 결과 합성.

Workflow:
  1. LLM이 목표를 2~4개 하위 작업으로 분해 (task decomposition)
  2. 각 하위 작업을 asyncio.gather 로 병렬 실행 (isolated context)
  3. 중간 결과 LLM이 수집 및 합성 (synthesis)

커맨드: /orchestrate <복잡한 목표>
"""

import asyncio
import json
import re
import datetime
import uuid
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from handlers._base import (
    router, cove_engine_instance, _audit_engine,
    logger, add_to_history, _call_llm, _get_mem_info, check_user
)

# ── 상수 ────────────────────────────────────────────────────
MAX_SUBTASKS = 4           # 최대 분해 개수
SUBAGENT_TIMEOUT = 120     # 서브에이전트 타임아웃 (초)
VERSION = "v1.0"

# ── 진행 중인 오케스트레이션 추적 ──────────────────────────────
_active_orchestrations: dict = {}  # {task_id: info_dict}


async def _llm_decompose(goal: str) -> list[dict]:
    """LLM에 목표를 전달하여 하위 작업 목록을 생성.

    Returns:
        [{"id": "1", "task": "설명", "tools": "검색", "deps": []}, ...]
    """
    prompt = (
        f"다음 복잡한 목표를 {MAX_SUBTASKS}개 이하의 독립적인 하위 작업으로 분해하세요.\n\n"
        f"목표: {goal}\n\n"
        f"각 하위 작업은 독립적으로 실행 가능해야 하며, 다른 하위 작업의 결과에 의존하지 않아야 합니다.\n"
        f"JSON 배열 형식으로만 답변하세요:\n"
        f'[{{"id": "1", "task": "하위 작업 설명", "tools": "필요한 도구", "deps": []}}]\n\n'
        f"tools 필드는 다음 중 하나: 검색, 코드, 파일, 브라우저, 조사"
    )
    resp = await _call_llm(prompt)
    resp = resp.strip()
    # JSON 블록 추출
    json_match = re.search(r'\[.*\]', resp, re.DOTALL)
    if json_match:
        try:
            tasks = json.loads(json_match.group())
            if isinstance(tasks, list) and len(tasks) <= MAX_SUBTASKS:
                return tasks
        except json.JSONDecodeError:
            pass
    # Fallback: 단일 작업
    return [{"id": "1", "task": goal, "tools": "조사", "deps": []}]


async def _run_subagent(task: dict, goal: str, agent_index: int) -> dict:
    """하나의 하위 에이전트 실행 (isolated context).

    각 subagent는 독립적인 LLM 호출로 작업을 수행.

    Args:
        task: 하위 작업 정보
        goal: 원본 목표 (맥락 전달용)
        agent_index: 에이전트 인덱스 번호

    Returns:
        {"id": str, "task": str, "result": str, "status": str, "duration": float}
    """
    start = datetime.datetime.now()
    task_id = task.get("id", str(agent_index))
    task_desc = task.get("task", "알 수 없는 작업")
    tools = task.get("tools", "조사")

    try:
        sub_prompt = (
            f"당신은 전문 에이전트 #{agent_index}입니다. 다음 하위 작업을 수행하세요.\n\n"
            f"전체 목표: {goal}\n"
            f"당신의 작업: {task_desc}\n"
            f"사용 가능 도구: {tools}\n\n"
            f"작업을 완료하고 결과를 상세히 보고하세요. "
            f"가능한 한 구체적이고 실행 가능한 결과를 도출하세요."
        )
        result_text = await _call_llm(sub_prompt)

        duration = (datetime.datetime.now() - start).total_seconds()

        logger.info(f"🤖 [Orchestrator] Subagent #{agent_index} 완료 "
                     f"({duration:.1f}s): {task_desc[:50]}...")

        return {
            "id": task_id,
            "task": task_desc,
            "result": result_text.strip(),
            "status": "success",
            "duration": round(duration, 1),
            "tools": tools,
        }

    except Exception as e:
        duration = (datetime.datetime.now() - start).total_seconds()
        logger.error(f"❌ [Orchestrator] Subagent #{agent_index} 실패: {e}")

        return {
            "id": task_id,
            "task": task_desc,
            "result": f"❌ 오류: {str(e)}",
            "status": "failed",
            "duration": round(duration, 1),
            "tools": tools,
        }


async def _llm_synthesize(goal: str, sub_results: list[dict]) -> str:
    """LLM으로 모든 서브에이전트 결과를 종합하여 최종 응답 생성."""
    parts = [f"# 전체 목표\n{goal}\n\n# 하위 작업 결과\n"]

    for sr in sub_results:
        status_icon = "✅" if sr["status"] == "success" else "❌"
        parts.append(
            f"## {status_icon} 작업 #{sr['id']}: {sr['task'][:80]}\n"
            f"**상태**: {sr['status']} | **소요시간**: {sr['duration']:.1f}s | **도구**: {sr.get('tools', '?')}\n\n"
            f"{sr['result'][:2000]}\n"
        )

    context_text = "\n".join(parts)

    synthesis_prompt = (
        f"당신은 오케스트레이터입니다. 여러 전문 에이전트의 결과를 수집하여 "
        f"통합된 최종 응답으로 합성하세요.\n\n"
        f"{context_text}\n\n"
        f"다음 형식으로 응답하세요:\n"
        f"1. **요약**: 전체 작업의 핵심 결과 (2~3문장)\n"
        f"2. **세부 결과**: 각 하위 작업별 주요 발견점\n"
        f"3. **결론 및 다음 단계**: 종합적 결론과 권장 사항"
    )

    return await _call_llm(synthesis_prompt)


def _format_progress_bar(count: int, total: int, width: int = 12) -> str:
    """텍스트 진행바 생성."""
    filled = int(count / max(total, 1) * width)
    bar = "▓" * filled + "░" * (width - filled)
    pct = int(count / max(total, 1) * 100)
    return f"`[{bar}] {pct}%`"


async def cmd_orchestrate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/orchestrate <복잡한 목표> — Multi-Agent 오케스트레이션 실행

    복잡한 목표를 분해 → 병렬 실행 → 결과 합성.

    사용법:
        /orchestrate 최신 논문 3편을 찾아 분석하고 요약해줘
        /orchestrate 백엔드 API 문서를 검토하고 테스트 케이스 작성
    """
    if not await check_user(update):
        return

    if not context.args:
        await update.message.reply_text(
            "🤖 **Multi-Agent Orchestrator**\n\n"
            "복잡한 목표를 여러 전문 에이전트로 분해하여 병렬 처리합니다.\n\n"
            "사용법:\n"
            "`/orchestrate <목표>` — 새 오케스트레이션 시작\n"
            "`/orchestrate status` — 현재 실행 중인 작업 목록\n\n"
            "예시:\n"
            "`/orchestrate 최신 AI 뉴스를 크롤링하고 분석해서 요약해줘`\n"
            "`/orchestrate 프로젝트 구조를 분석하고 개선점을 도출해줘`",
            parse_mode='Markdown'
        )
        return

    goal = ' '.join(context.args)

    # ── Mayor 대시보드 (Stage 5 루프 감독관, 2026-06-09) ──
    if goal.lower() in ("mayor", "mayor status", "루프", "loops"):
        from modules.mayor_agent import mayor
        # 활성 루프 대시보드 (완료 포함)
        dashboard = mayor.dashboard(include_done=True)
        mayor.prune_old()  # 오래된 완료 항목 정리
        await update.message.reply_text(dashboard, parse_mode='Markdown')
        return

    # 상태 확인
    if goal.lower() == "status":
        if not _active_orchestrations:
            await update.message.reply_text("📋 현재 실행 중인 오케스트레이션이 없습니다.")
        else:
            lines = ["📋 **실행 중인 오케스트레이션:**\n"]
            for tid, info in list(_active_orchestrations.items()):
                icon = "🔄" if info["status"] == "running" else "✅" if info["status"] == "done" else "❌"
                lines.append(f"{icon} `{tid}`: {info.get('goal', '?')[:60]} — {info['status']}")
            await update.message.reply_text("\n".join(lines), parse_mode='Markdown')
        return

    task_id = str(uuid.uuid4())[:8]
    msg = await update.message.reply_text(
        f"🤖 **Multi-Agent Orchestrator {VERSION}**\n\n"
        f"🎯 **목표**: {goal}\n\n"
        f"**① 분해 중...** 🤔\n"
        f"하위 작업 분석",
        parse_mode='Markdown'
    )

    # ── Phase 1: Task Decomposition ───────────────────────
    _active_orchestrations[task_id] = {
        "goal": goal, "status": "decomposing", "started": str(datetime.datetime.now()),
        "subtasks": [], "results": [],
    }

    subtasks = await _llm_decompose(goal)
    n_sub = len(subtasks)

    _active_orchestrations[task_id]["status"] = "running"
    _active_orchestrations[task_id]["subtasks"] = subtasks

    progress_bar = _format_progress_bar(0, n_sub)
    await msg.edit_text(
        f"🤖 **Multi-Agent Orchestrator {VERSION}**\n\n"
        f"🎯 **목표**: {goal}\n\n"
        f"**② {n_sub}개 하위 작업 병렬 실행 중...** 🔄\n\n"
        + "\n".join(f"  {i+1}. {t.get('task', '?')[:80]}" for i, t in enumerate(subtasks)) +
        f"\n\n진행도: {progress_bar}"
        f"\n\n각 에이전트가 작업을 수행 중입니다...",
        parse_mode='Markdown'
    )

    # ── Phase 2: Parallel Execution ───────────────────────
    tasks = [_run_subagent(t, goal, i + 1) for i, t in enumerate(subtasks)]
    sub_results = await asyncio.gather(*tasks)

    _active_orchestrations[task_id]["results"] = sub_results

    # 진행바 업데이트
    success_count = sum(1 for r in sub_results if r["status"] == "success")
    progress_bar = _format_progress_bar(success_count, n_sub)

    await msg.edit_text(
        f"🤖 **Multi-Agent Orchestrator {VERSION}**\n\n"
        f"🎯 **목표**: {goal}\n\n"
        f"**③ 결과 합성 중...** 🔄\n\n"
        + "\n".join(
            f"  {'✅' if r['status']=='success' else '❌'} {r['task'][:70]} ({r['duration']:.1f}s)"
            for r in sub_results
        ) +
        f"\n\n진행도: {progress_bar}",
        parse_mode='Markdown'
    )

    # ── Phase 3: Synthesis ─────────────────────────────────
    try:
        synthesis = await _llm_synthesize(goal, sub_results)
    except Exception as e:
        synthesis = f"❌ 합성 중 오류 발생: {e}\n\n" + "\n\n".join(
            f"**작업 #{r['id']}**: {r['task'][:60]}\n{r['result'][:500]}"
            for r in sub_results
        )

    _active_orchestrations[task_id]["status"] = "done"
    total_duration = sum(r["duration"] for r in sub_results)

    # ── 최종 응답 생성 ───────────────────────────────────
    sub_lines = []
    for r in sub_results:
        icon = "✅" if r["status"] == "success" else "❌"
        sub_lines.append(f"{icon} `#{r['id']}` {r['task'][:60]} ({r['duration']:.1f}s)")

    final_text = (
        f"🤖 **Multi-Agent Orchestration 완료** (`{task_id}`)\n"
        f"🎯 `{goal[:80]}`\n\n"
        f"**📊 실행 통계**\n"
        f"• 총 소요시간: `{sum(r['duration'] for r in sub_results):.1f}s`"
        f" (병렬 `{max(r['duration'] for r in sub_results):.1f}s`)\n"
        f"• 하위 작업: `{n_sub}개` (성공 `{success_count}`, 실패 `{n_sub - success_count}`)\n\n"
        + "\n".join(sub_lines) +
        f"\n\n**📋 합성 결과**\n{synthesis[:3500]}" +
        f"\n\n_더 자세한 내용은 `/orchestrate status`_"
    )

    try:
        await msg.edit_text(final_text[:4096], parse_mode='Markdown')
    except Exception:
        await msg.edit_text(final_text[:4096])

    logger.info(f"🤖 [Orchestrator] 완료: {task_id} — {n_sub}개 작업, {total_duration:.1f}s 총")
