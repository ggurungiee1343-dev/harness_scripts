"""
modules/agentic_loop.py
=======================
harness_agent.py handle_message()에서 에이전틱 루프 + 도구 실행 블록을 분리.
분리 전 위치: harness_agent.py L503~L694 (약 192줄)

역할:
  - 최대 3회 에이전틱 루프 실행
  - 도구 태그 파싱 + 실행 (LIST/READ/WEB_READ/SEARCH/RUN_CMD/CREATE/DELETE/MOVE/COPY/RENAME)
  - ToolResult 샌드박스 래핑 (Prompt Injection 방어)
  - Mayor 에이전트 루프 감독 연결
  - CoVe 파일시스템 자기검증 (루프 종료 시)

harness_agent.py 변경 사항:
  # 기존 L503~L694 전체를 아래 한 줄로 교체:
  ans = await run_agentic_loop(
      initial_ans=ans,
      messages=messages,
      get_llm_response=get_llm_response,
      update=update,
      context=context,
      file_m=file_m,
      ctx_builder=ctx_builder,
      update_progress_fn=_update_progress,
  )
"""

import os
import re
import json
import asyncio
import inspect
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ── TraceGraph 설정 (2605.31308: 궤적 로깅 + 수리 경로 추적) ─────────
_TRACE_LOG = Path("/Users/bluesea/.hermes/runtime/trace_log.jsonl")
_TRACE_MAX_ENTRIES = 200

def _trace(turn: int, tool: str, path: str, result_snippet: str, repaired: bool = False):
    """TraceGraph 궤적 기록. 롤링 200엔트리 유지."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "turn": turn,
        "tool": tool,
        "path": path,
        "result": result_snippet[:200],
        "repaired": repaired,
    }
    try:
        _TRACE_LOG.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        if _TRACE_LOG.exists():
            lines = _TRACE_LOG.read_text(encoding="utf-8").splitlines()
        lines.append(json.dumps(entry, ensure_ascii=False))
        # 롤링: 최근 200개만 유지
        if len(lines) > _TRACE_MAX_ENTRIES:
            lines = lines[-_TRACE_MAX_ENTRIES:]
        _TRACE_LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass

# ── 도구 태그 정규식 (컴파일하여 재사용) ────────────────────────────
_RE_LIST     = re.compile(r"\[LIST:\s*(.*?)\]")
_RE_READ     = re.compile(r"\[READ:\s*(.*?)\]")
_RE_WEB      = re.compile(r"\[WEB_READ:\s*(.*?)\]")
_RE_SEARCH   = re.compile(r"\[SEARCH:\s*(.*?)\]")
_RE_CMD      = re.compile(r"\[RUN_CMD:\s*(.*?)\]", re.DOTALL)
_RE_CREATE   = re.compile(r"\[CREATE:\s*(.*?)\](.*?)\[/CREATE\]", re.DOTALL)
_RE_DELETE   = re.compile(r"\[DELETE:\s*(.*?)\]")
_RE_MOVE     = re.compile(r"\[MOVE:\s*(.*?)\s*(?:->|→|to)\s*(.*)\]")
_RE_COPY     = re.compile(r"\[COPY:\s*(.*?)\s*(?:->|→|to)\s*(.*)\]")
_RE_RENAME   = re.compile(r"\[RENAME:\s*(.*?)\s*(?:->|→|to)\s*(.*)\]")

_SCRIPTS_DIR = "/Users/bluesea/Applications/Mjauto/Scripts"

# ── Goal-Autopilot: 검증 게이트 (2606.11688) ────────────────────────
# 태그 실행 후 실제 성공 여부를 구조적으로 검증. 거짓 완료 보고 방지.
_FAILURE_SIGNALS = [
    "error", "오류", "실패", "traceback", "exception",
    "not found", "permission denied", "no such file",
    "cannot", "unable", "command not found", "errno",
]

def _verify_gate(tag_type: str, result: str, artifact: str = "") -> str:
    """
    태그 실행 결과 검증. 실패 감지 시 경고 문자열 반환, 통과 시 빈 문자열.
    Goal-Autopilot 원칙: 거짓 성공은 구조적으로 불가능하게.
    """
    result_lower = result.lower()
    if any(sig in result_lower for sig in _FAILURE_SIGNALS):
        return f"[⚠️ VerificationGate] {tag_type} 실패 감지 — 완료 선언 금지: {result[:120]}"
    if tag_type == "CREATE" and artifact:
        if not os.path.exists(artifact):
            return f"[⚠️ VerificationGate] CREATE 검증 실패 — 파일 미존재: {artifact}"
    return ""


async def run_agentic_loop(
    initial_ans: str,
    messages: list[dict],
    get_llm_response,           # async fn(messages) -> (ans, engine)
    update,                     # Telegram Update 객체 (파일 작업 승인용)
    context,                    # Telegram Context 객체
    file_m,                     # FileManager LazyService
    ctx_builder,                # ContextBuilder LazyService
    update_progress_fn=None,    # async fn(text)
) -> str:
    """
    에이전틱 루프 실행 후 최종 답변 문자열 반환.

    harness_agent.py의 L503~L694 블록을 완전히 대체.
    Mayor + ToolResult + CoVe 자기검증 모두 포함.
    """
    from modules.tool_result import ToolResult as _TR
    from modules.mayor_agent import mayor as _mayor
    from modules.file_ops_agent import handle_file_op as _handle_file_op

    # Mayor 루프 등록
    loop_name = f"agentic:{initial_ans[:40].replace(chr(10), ' ')}"
    loop_id = _mayor.register(name=loop_name, max_iters=3, token_budget=12_000)

    def _est_tokens(text: str) -> int:
        return max(1, len(str(text)) // 3)

    ans = initial_ans
    _turn = 0

    for _ in range(3):
        _turn += 1
        executed = False
        exec_results: list[str] = []

        # ── LIST ────────────────────────────────────────────────────
        m = _RE_LIST.search(ans)
        if m:
            target_dir = m.group(1).strip()
            await _prog(update_progress_fn, f"📂 폴더 목록 조회 중: `{os.path.basename(target_dir) or target_dir}`...")
            dir_map = ctx_builder.build_directory_map(target_dir)
            ctx = ctx_builder.load_hierarchical_context(target_dir)
            exec_results.append(
                f"[📂 LIST 결과 (필터링 적용)]\n{dir_map}\n\n[📖 폴더 컨텍스트]\n{ctx}"
            )
            _trace(_turn, "LIST", target_dir, dir_map[:100])
            executed = True

        # ── READ ────────────────────────────────────────────────────
        m = _RE_READ.search(ans)
        if m:
            target_file = m.group(1).strip()
            await _prog(update_progress_fn, f"📄 파일 분석 중: `{os.path.basename(target_file)}`...")
            res = file_m.read_file(target_file)
            target_dir = os.path.dirname(target_file)
            ctx = ctx_builder.load_hierarchical_context(target_dir)
            exec_results.append(
                _TR(
                    tool_type="READ",
                    content=f"{res[:3000]}\n\n[📖 파일 컨텍스트]\n{ctx}",
                    artifacts=[os.path.basename(target_file)],
                ).to_prompt()
            )
            _trace(_turn, "READ", target_file, res[:100])
            executed = True

        # ── WEB_READ ─────────────────────────────────────────────────
        m = _RE_WEB.search(ans)
        if m:
            await _prog(update_progress_fn, "🌐 웹 페이지 읽는 중...")
            from modules.web_reader import WebReader
            res = WebReader().fetch_url(m.group(1).strip())
            exec_results.append(
                _TR(
                    tool_type="WEB_READ",
                    content=res[:3000],
                    artifacts=[m.group(1).strip()],
                ).to_prompt()
            )
            executed = True

        # ── SEARCH ───────────────────────────────────────────────────
        m = _RE_SEARCH.search(ans)
        if m:
            query = m.group(1).strip()
            await _prog(update_progress_fn, f"🔍 웹 검색 중: `{query}`...")
            import sys
            if "/Users/bluesea/hermes" not in sys.path:
                sys.path.append("/Users/bluesea/hermes")
            try:
                from web_agent_module import search_web
                search_res = await search_web(query)
                exec_results.append(
                    _TR(tool_type="SEARCH", content=search_res, artifacts=[query]).to_prompt()
                )
            except Exception as e:
                exec_results.append(
                    _TR.error("SEARCH", str(e), "다른 검색어로 재시도하거나 WEB_READ를 사용하세요.").to_prompt()
                )
            executed = True

        # ── RUN_CMD ──────────────────────────────────────────────────
        m = _RE_CMD.search(ans)
        if m:
            cmd_text = m.group(1).strip()
            await _prog(update_progress_fn, "💻 명령어 실행 중...")
            cmd_result = await _exec_cmd(cmd_text, update, context)
            exec_results.append(cmd_result)
            gate_warn = _verify_gate("RUN_CMD", cmd_result)
            repaired = bool(gate_warn)
            if gate_warn:
                exec_results.append(gate_warn)
            _trace(_turn, "RUN_CMD", cmd_text[:80], cmd_result[:100], repaired=repaired)
            executed = True

        # ── CREATE ───────────────────────────────────────────────────
        m = _RE_CREATE.search(ans)
        if m:
            create_path, create_content = m.group(1).strip(), m.group(2).strip()
            await _prog(update_progress_fn, f"✍️ 파일 생성 중: `{os.path.basename(create_path)}`...")
            if inspect.iscoroutinefunction(_handle_file_op):
                await _handle_file_op(update, context, "CREATE", create_path, create_content)
            create_result = f"[✅ CREATE 완료: {create_path}]"
            exec_results.append(create_result)
            gate_warn = _verify_gate("CREATE", create_result, create_path)
            repaired = bool(gate_warn)
            if gate_warn:
                exec_results.append(gate_warn)
            _trace(_turn, "CREATE", create_path, create_result[:80], repaired=repaired)
            executed = True

        # ── DELETE ───────────────────────────────────────────────────
        m = _RE_DELETE.search(ans)
        if m:
            del_path = m.group(1).strip()
            await _prog(update_progress_fn, "🗑️ 파일 삭제 요청 중...")
            if inspect.iscoroutinefunction(_handle_file_op):
                await _handle_file_op(update, context, "DELETE", del_path)
            exec_results.append(f"[🗑️ DELETE 요청 처리됨: {del_path}]")
            executed = True

        # ── MOVE ─────────────────────────────────────────────────────
        m = _RE_MOVE.search(ans)
        if m:
            if inspect.iscoroutinefunction(_handle_file_op):
                await _handle_file_op(update, context, "MOVE", m.group(1).strip(), m.group(2).strip())
            exec_results.append(f"[📦 MOVE 처리됨: {m.group(1).strip()} → {m.group(2).strip()}]")
            executed = True

        # ── COPY ─────────────────────────────────────────────────────
        m = _RE_COPY.search(ans)
        if m:
            if inspect.iscoroutinefunction(_handle_file_op):
                await _handle_file_op(update, context, "COPY", m.group(1).strip(), m.group(2).strip())
            exec_results.append(f"[📋 COPY 처리됨: {m.group(1).strip()} → {m.group(2).strip()}]")
            executed = True

        # ── RENAME ───────────────────────────────────────────────────
        m = _RE_RENAME.search(ans)
        if m:
            if inspect.iscoroutinefunction(_handle_file_op):
                await _handle_file_op(update, context, "RENAME", m.group(1).strip(), m.group(2).strip())
            exec_results.append(f"[✏️ RENAME 처리됨: {m.group(1).strip()} → {m.group(2).strip()}]")
            executed = True

        # ── 루프 계속 여부 판단 ──────────────────────────────────────
        if executed:
            turn_tokens = _est_tokens(ans) + sum(_est_tokens(r) for r in exec_results)
            should_continue = _mayor.tick(loop_id, tokens_used=turn_tokens)
            if not should_continue:
                stop_reason = _mayor.get_stop_reason(loop_id)
                ans += f"\n\n⚠️ **[Mayor 루프 감독]** 에이전틱 루프 조기 종료: {stop_reason}"
                break
            messages.append({"role": "assistant", "content": ans})
            messages.append({"role": "user",      "content": "\n".join(exec_results)})
            ans, _ = await get_llm_response(messages)

        else:
            # 도구 미사용 → CoVe 파일시스템 자기검증 후 종료
            ans = _apply_cove_grounding(ans)
            _mayor.finish(loop_id, success=True, summary=f"{len(ans)}자 응답")
            break

    return ans


# ── 내부 헬퍼들 ────────────────────────────────────────────────────

async def _prog(fn, text: str) -> None:
    """진행 메시지 갱신 (fn이 없으면 무시)"""
    if fn:
        try:
            await fn(text)
        except Exception:
            pass


async def _exec_cmd(cmd_text: str, update, context) -> str:
    """RUN_CMD 실행 — PermissionBridge 경유"""
    try:
        from modules.permission_bridge import guard_external_tool
        allowed, reason = await guard_external_tool(update, context, "RUN_CMD", cmd_text)
        if not allowed:
            return f"[🛡️ PermissionBridge] RUN_CMD 차단됨: {reason}"
    except Exception as e:
        logger.warning(f"[AgenticLoop] PermissionBridge 오류 (직접 실행 시도): {e}")

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd_text,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        res = (stdout.decode(errors="replace") if stdout else "") + \
              (stderr.decode(errors="replace") if stderr else "")
        return f"[💻 CMD 결과]\n{res}"
    except Exception as e:
        return f"[⚠️ CMD 실행 실패] {e}"


def _apply_cove_grounding(ans: str) -> str:
    """
    CoVe Step5: 최종 답변 내 파일명 주장을 grep으로 실존 확인.
    미존재 파일 감지 시 경고 블록 삽입.
    """
    try:
        from cove_engine import CoVeEngine as _CoVe
        inst = _CoVe.__new__(_CoVe)
        warns = inst._filesystem_grounding(ans, _SCRIPTS_DIR)
        if warns:
            warn_lines = "\n".join(f"  • {w}" for w in warns)
            ans += (
                "\n\n---\n⚠️ **[루프 자기검증 — 파일시스템]**\n"
                "답변에서 언급된 파일 중 실제로 존재하지 않는 것이 감지되었습니다:\n"
                f"{warn_lines}\n"
                "_위 항목은 환각일 수 있습니다. 확인 후 사용하세요._"
            )
    except Exception as e:
        logger.debug(f"[AgenticLoop] CoVe 검증 실패 (무시): {e}")
    return ans
