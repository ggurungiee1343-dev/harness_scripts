"""core_agents.py — Agent 클래스 레이어 (2026-06-09 core_reducer.py에서 분리)
SourceChannel, AgentContext, ArchitecturalJudgmentGate, DecisionAgent, ExecutionAgent, KnowledgeAgent
"""

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional

# v9.2: SIA + Monitoring 통합
from modules.sia_engine import SelfImprovingAgent
from modules.monitoring_engine import MonitoringEngine, MetricSnapshot

log = logging.getLogger("core_reducer")



# ═══════════════════════════════════════════════════════════════════════════
#  AgentContext — 불변 입력 컨텍스트 (v9.0)
# ═══════════════════════════════════════════════════════════════════════════

class SourceChannel(Enum):
    TELEGRAM = "telegram"
    SLACK    = "slack"
    EMAIL    = "email"
    WEBHOOK  = "webhook"
    CLI      = "cli"
    CRON     = "cron"


@dataclass(frozen=True)
class AgentContext:
    """
    불변 컨텍스트 — 요청 단위 상태 컨테이너.
    Stateless 원칙: 각 reduce() 호출마다 새 컨텍스트 생성.
    장기 상태는 DB에 저장, 이 객체에는 저장하지 않음.
    """
    user_query:      str
    source_channel:  SourceChannel
    timestamp:       float  = field(default_factory=time.time)
    hot_context:     str    = ""
    rejected_buffer: tuple  = field(default_factory=tuple)
    pems_score:      float  = 0.5
    user_id:         str    = "anonymous"
    session_id:      str    = ""
    metadata:        dict   = field(default_factory=dict)
    execution_state: str    = "RUNNING"   # RUNNING | PAUSED | DONE | ERROR
    current_step:    int    = 0

    def get_hash(self) -> str:
        """컨텍스트 고유 해시 — Context Hash 캐싱(v9.1)에 사용."""
        serialized = f"{self.user_query}|{self.hot_context}|{str(self.rejected_buffer)}"
        return hashlib.sha256(serialized.encode()).hexdigest()

    def to_dict(self) -> dict:
        data = asdict(self)
        data["source_channel"] = self.source_channel.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "AgentContext":
        d = dict(data)
        if isinstance(d.get("source_channel"), str):
            d["source_channel"] = SourceChannel(d["source_channel"])
        if isinstance(d.get("rejected_buffer"), list):
            d["rejected_buffer"] = tuple(d["rejected_buffer"])
        return cls(**d)


# ═══════════════════════════════════════════════════════════════════════════
#  ArchitecturalJudgmentGate — 결정론적 숏컷 (v9.2 Priority 4, Harness 7)
# ═══════════════════════════════════════════════════════════════════════════

class ArchitecturalJudgmentGate:
    """
    Tunguz 하네스 7번 기둥: 비용 최적화.
    LLM 호출 없이 로컬 코드로 즉시 처리 가능한 명령어를 분류합니다.
    → 토큰 비용 0원, 응답 시간 <10ms
    """
    _SHORTCUTS: dict = {
        "/status": {"action": "system", "command": "status"},
        "/pause":  {"action": "system", "command": "pause"},
        "/resume": {"action": "system", "command": "resume"},
        "/ping":   {"action": "system", "command": "ping"},
        "ping":    {"action": "system", "command": "ping"},
    }

    @staticmethod
    def is_deterministic_shortcut(query: str) -> tuple[bool, dict]:
        """시스템 명령어 직접 처리 판정 — LLM 호출이 필요 없는 결정론적 쿼리."""
        clean = query.strip().lower()
        for cmd, decision in ArchitecturalJudgmentGate._SHORTCUTS.items():
            if clean == cmd or clean.startswith(cmd + " "):
                return True, decision
        return False, {}


# ═══════════════════════════════════════════════════════════════════════════
#  DecisionAgent — 쿼리 분석 및 액션 결정
# ═══════════════════════════════════════════════════════════════════════════

class DecisionAgent:
    """
    사용자 쿼리를 분석하여 최적의 액션을 결정합니다.

    v9.0: 키워드 기반 룰 + LLM 폴백
    v9.1: Context Hash 캐싱으로 동일 컨텍스트 재연산 방지
    """

    def __init__(self, llm=None):
        self.llm = llm
        # 액션 키워드 매핑 (우선순위 순)
        self._action_rules: List[Dict[str, Any]] = [
            {"action": "web",  "keywords": ["/web ", "웹검색:", "웹 검색", "검색해줘", "찾아줘", "최신", "날씨", "뉴스", "주가", "검색 "]},
            {"action": "wiki", "keywords": ["/wiki ", "위키:", "노트에서", "문서에서", "기록에서"]},
            {"action": "tag",  "keywords": ["/tag ", "태그:", "태그 달아", "분류해줘"]},
            {"action": "exec", "keywords": ["/exec ", "실행:", "실행해줘", "명령어:"]},
        ]

    def _build_prompt(self, ctx: AgentContext) -> str:
        """
        Claude Best Practices 적용:
        - Chain-of-Thought 유도
        - 구조화된 JSON 출력 요청
        - hot_context 활용
        """
        return (
            "당신은 Hermes3의 액션 라우터입니다. "
            "사용자 쿼리를 분석하여 최적 액션을 JSON으로 반환하세요.\n\n"
            f"사용자 쿼리: {ctx.user_query}\n"
            f"컨텍스트: {ctx.hot_context[:200] if ctx.hot_context else '없음'}\n"
            f"PEMS 점수: {ctx.pems_score}\n\n"
            "가능한 액션: ask(질의응답), web(웹검색), wiki(로컬검색), tag(태깅), exec(실행)\n\n"
            "응답 형식 (JSON만):\n"
            '{"action": "액션명", "query": "처리할 쿼리", "confidence": 0.0~1.0, "reasoning": "선택 이유"}'
        )

    async def decide(self, ctx: AgentContext) -> Dict[str, Any]:
        """
        액션 결정 (룰 기반 → LLM 폴백).

        Returns:
            {"action": str, "query": str, "confidence": float, "reasoning": str}
        """
        query = ctx.user_query.strip()

        # 1단계: 룰 기반 빠른 결정
        for rule in self._action_rules:
            for kw in rule["keywords"]:
                if kw in query:
                    # 키워드 이후 실제 쿼리 추출
                    actual_query = query.split(" ", 1)[-1] if " " in query else query
                    return {
                        "action":     rule["action"],
                        "query":      actual_query,
                        "confidence": 0.9,
                        "reasoning":  f"키워드 매칭: '{kw}'",
                    }

        # 2단계: LLM 결정 (llm이 있을 때만)
        if self.llm:
            try:
                prompt = self._build_prompt(ctx)
                import inspect
                raw = await self.llm(prompt) if inspect.iscoroutinefunction(self.llm) \
                    else self.llm(prompt)
                # JSON 파싱
                import re
                m = re.search(r"\{.*\}", raw, re.DOTALL)
                if m:
                    parsed = json.loads(m.group())
                    if "action" in parsed:
                        return {
                            "action":     parsed.get("action", "ask"),
                            "query":      parsed.get("query", query),
                            "confidence": float(parsed.get("confidence", 0.7)),
                            "reasoning":  parsed.get("reasoning", "LLM 결정"),
                        }
            except Exception as e:
                log.debug(f"[DecisionAgent] LLM 폴백 실패: {e}")

        # 3단계: 기본값 (ask)
        return {
            "action":     "ask",
            "query":      query,
            "confidence": 0.5,
            "reasoning":  "기본 액션 (ask)",
        }


# ═══════════════════════════════════════════════════════════════════════════
#  ExecutionAgent — 액션 실행
# ═══════════════════════════════════════════════════════════════════════════

class ExecutionAgent:
    """
    결정(decision)을 실행으로 전환하는 코어 실행 에이전트.

    액션별 핸들러:
      ask  → CoVe 팩트체크 메모리 질의응답
      tag  → 문서 태깅 승인
      web  → hermes-web-search-plus 다중 프로바이더 (Fallback: wiki)
      wiki → HybridKnowledgeIndexer FTS5+벡터+RRF
      exec → Bash 실행 (Trajectory Regulation)
    """

    async def execute(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        """
        액션 디스패처.

        Returns:
            {"success": bool, "result": str, "action": str, "error": str|None}
        """
        action = decision.get("action", "ask")
        query  = decision.get("query",  "")

        log.info(f"[ExecutionAgent] 액션={action!r} 쿼리={query[:50]!r}")

        dispatch = {
            "ask":  self._execute_ask,
            "tag":  self._execute_tag,
            "web":  self._execute_web,
            "wiki": self._execute_wiki,
            "exec": self._execute_exec,
            "file": self._execute_file,
        }

        handler = dispatch.get(action, self._execute_unknown)
        try:
            result_text = await handler(query, decision)
            return {"success": True, "result": result_text, "action": action, "error": None}
        except Exception as e:
            log.error(f"[ExecutionAgent] {action} 실행 오류: {e}", exc_info=True)
            return {"success": False, "result": "", "action": action, "error": str(e)[:200]}

    # ── ask ──────────────────────────────────────────────────────────────

    async def _execute_ask(self, query: str, decision: Dict[str, Any]) -> str:
        """CoVe 팩트체크 + 메모리 기반 질의응답."""
        try:
            from handlers._memory import cmd_ask_logic
            result = await cmd_ask_logic(query)
            return result if result and result.strip() else f"✅ 질문: {query}\n(LLM 응답을 생성하지 못했습니다. 다시 시도해주세요.)"
        except Exception:
            log.debug("[ExecutionAgent] cmd_ask_logic 실패 → 기본 응답")
            return f"✅ 질문: {query}\n(LLM 응답 준비 중입니다)"

    # ── tag ──────────────────────────────────────────────────────────────

    async def _execute_tag(self, query: str, decision: Dict[str, Any]) -> str:
        """문서 태깅 및 승인 처리."""
        try:
            from handlers._approval import cmd_tag_logic
            return await cmd_tag_logic(query)
        except ImportError:
            return "⚠️ 태그 처리 불가 (핸들러 없음)"

    # ── web ──────────────────────────────────────────────────────────────

    async def _execute_web(self, query: str, decision: Dict[str, Any]) -> str:
        """
        [v9.1.4] duckduckgo_search 기반 웹 검색.
        실패 시 자동으로 로컬 위키 검색(Fallback)으로 전환.
        """
        if not query.strip():
            return "⚠️ 검색 쿼리가 비어있습니다."

        try:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=5))
        except ImportError:
            log.info("[Web] duckduckgo_search 없음 → Fallback wiki")
            return await self._execute_wiki(query, decision)
        except Exception as e:
            log.warning(f"[Web] duckduckgo 검색 실패 → Fallback wiki: {e}")
            return await self._execute_wiki(query, decision)

        if not results:
            log.info("[Web] 검색 결과 없음 → Fallback wiki")
            return await self._execute_wiki(query, decision)

        lines = [f"🌐 **웹 검색**: `{query}`\n"]
        for i, r in enumerate(results[:5], 1):
            title = r.get("title", "").strip()
            body  = r.get("body",  "").strip()
            href  = r.get("href",  "")
            if title or body:
                lines.append(f"{i}. **{title or '제목 없음'}**")
                if body:
                    lines.append(f"   {body[:200]}")
                if href:
                    lines.append(f"   [{href}]({href})")
                lines.append("")
        return "\n".join(lines)

    # ── wiki ─────────────────────────────────────────────────────────────

    async def _execute_wiki(self, query: str, decision: Dict[str, Any]) -> str:
        """
        [v9.1.2] Mjobsidian 위키 하이브리드 검색.
        SQLite FTS5 + TF-IDF 벡터 + RRF 병합.
        """
        if not query.strip():
            return "⚠️ 검색 쿼리가 비어있습니다."
        try:
            from modules.knowledge_indexer import get_indexer
            indexer = get_indexer()
            result = await indexer.search_hybrid(query)
            return f"📖 **위키 검색**: `{query}`\n\n{result}"
        except Exception as e:
            log.error(f"[Wiki] 검색 오류: {e}")
            return f"❌ 위키 검색 실패: {str(e)[:100]}"

    # ── exec ─────────────────────────────────────────────────────────────

    # 위험 명령어 블록리스트 (셸 파괴/시스템 손상 명령)
    _DANGEROUS_COMMANDS = (
        "rm -rf /", "rm -rf /*", "rm -rf ~", "rm -rf .",
        "mkfs", "dd if=", "format ", ":(){ :|:& };:",  # fork bomb
        "> /dev/sda", "> /dev/sdb",
        "chmod -R 000 /", "chown -R ",
        "mv /* /dev/null", "mv ~/* /dev/null",
        "wget -O- | sh", "curl | sh",
        "shutdown", "reboot", "halt", "poweroff",
        "init 0", "init 6",
    )

    async def _execute_exec(self, query: str, decision: Dict[str, Any]) -> str:
        """Bash 명령어 실행 (Trajectory Regulation + 보안 필터링)."""
        if not query.strip():
            return "⚠️ 실행할 명령어가 없습니다."

        # 보안 검증 1: 위험 명령어 차단
        query_lower = query.strip().lower()
        for dangerous in self._DANGEROUS_COMMANDS:
            if dangerous in query_lower:
                log.warning(f"[Security] 위험 명령어 차단: {query[:80]!r}")
                return (
                    f"⛔ **보안 차단**: 위험한 명령어 패턴이 감지되었습니다.\n"
                    f"`{query[:100]}`\n\n"
                    f"이 명령어는 시스템 손상 위험이 있어 실행이 차단되었습니다."
                )

        # 보안 검증 2: 한글/특수문자 포함 쿼리 차단 (셸 오류 방지)
        import re
        if re.search(r'[가-힣]', query):
            log.warning(f"[Security] 한글 포함 명령어 차단: {query[:80]!r}")
            return (
                f"⚠️ **보안 차단**: 명령어에 한글이 포함되어 있습니다.\n"
                f"`{query[:100]}`\n\n"
                f"셸 명령어는 영문/숫자/기호만 지원합니다."
            )

        # 보안 검증 3: 타임아웃은 async wait_for(30)으로 보장됨
        try:
            from modules.executor import execute_bash_command
            return await execute_bash_command(query)
        except ImportError:
            try:
                proc = await asyncio.create_subprocess_shell(
                    query,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=30)
                stdout = stdout_b.decode().strip()
                stderr = stderr_b.decode().strip()
                if proc.returncode == 0:
                    return f"✅ 실행 완료\n{stdout or '(출력 없음)'}"
                return f"❌ 실패 (code={proc.returncode})\n{stderr[:300]}"
            except asyncio.TimeoutError:
                return "❌ 명령어 타임아웃 (30초)"
            except Exception as e:
                return f"❌ 실행 오류: {str(e)[:100]}"

    # ── file ─────────────────────────────────────────────────────────────

    async def _execute_file(self, query: str, decision: Dict[str, Any]) -> str:
        """파일 읽기 — 허용된 경로 내에서만 접근 가능.

        보안:
          - 허용 경로: /Users/bluesea/Applications/ 만 접근 가능
          - 심볼릭 링크 공격 차단: os.path.realpath()로 실제 경로 검증
          - 대용량 파일 차단 (100MB 초과)
          - 한글/특수문자 포함 쿼리 차단
        """
        import re
        import os as _os

        # 보안 검증 1: 쿼리 정리
        file_path = query.strip().strip("\"'").split()[0] if query.strip() else ""
        if not file_path:
            return "⚠️ 파일 경로가 필요합니다."

        # 보안 검증 2: 한글 포함 경로 차단 (셸 오류 방지)
        if re.search(r'[가-힣]', file_path):
            return (
                f"⚠️ **보안 차단**: 파일 경로에 한글이 포함되어 있습니다.\n"
                f"`{file_path[:100]}`"
            )

        # 보안 검증 3: 실제 경로 해석 및 허용 범위 확인
        try:
            resolved = _os.path.realpath(_os.path.expanduser(file_path))
        except Exception as e:
            return f"❌ 경로 해석 오류: {str(e)[:100]}"

        ALLOWED_PREFIX = "/Users/bluesea/Applications/"
        if not resolved.startswith(ALLOWED_PREFIX):
            return (
                f"⛔ **보안 차단**: 허용되지 않은 경로입니다.\n"
                f"`{resolved}`\n\n"
                f"파일 접근은 `{ALLOWED_PREFIX}*` 만 허용됩니다."
            )

        # 보안 검증 4: 존재 여부 및 파일 타입 확인
        if not _os.path.isfile(resolved):
            return f"❌ 파일을 찾을 수 없습니다: {resolved}"

        # 보안 검증 5: 파일 크기 제한 (100MB)
        try:
            file_size = _os.path.getsize(resolved)
            if file_size > 100 * 1024 * 1024:
                return f"❌ 파일이 너무 큽니다 (100MB 초과): {file_size / 1024 / 1024:.1f}MB"
        except OSError as e:
            return f"❌ 파일 크기 확인 오류: {str(e)[:100]}"

        # 안전한 파일 읽기
        try:
            with open(resolved, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(50000)  # 최대 50KB
            return (
                f"📄 **{_os.path.basename(resolved)}** ({file_size:,} bytes)\n"
                f"```\n{content[:3000]}\n```\n"
                f"*({len(content)}/{file_size} 바이트 표시)*"
            )
        except UnicodeDecodeError:
            return f"⚠️ 바이너리 파일로 추정됨: {_os.path.basename(resolved)}"
        except Exception as e:
            return f"❌ 파일 읽기 오류: {str(e)[:100]}"

    # ── unknown ──────────────────────────────────────────────────────────

    async def _execute_unknown(self, query: str, decision: Dict[str, Any]) -> str:
        action = decision.get("action", "?")
        log.warning(f"[ExecutionAgent] 알 수 없는 액션: {action!r}")
        return f"⚠️ 알 수 없는 액션: {action!r}"


# ═══════════════════════════════════════════════════════════════════════════
#  KnowledgeAgent — 결과 정제 및 컨텍스트 업데이트
# ═══════════════════════════════════════════════════════════════════════════

class KnowledgeAgent:
    """
    실행 결과를 분석하여 컨텍스트를 정제합니다.

    v9.0:
      - 실패 결과를 rejected_buffer에 추가
      - PEMS 점수 업데이트 (성공 시 +0.1, 실패 시 -0.1)
      - hot_context 업데이트

    v9.1+:
      - DreamingV2 증류 연동
      - 야간 배치 학습 데이터 수집
    """

    async def refine(self, ctx: AgentContext, result: Dict[str, Any]) -> AgentContext:
        """
        결과를 바탕으로 새 컨텍스트를 생성 (불변 패턴).
        frozen dataclass이므로 object.__setattr__ 또는 replace() 사용.
        """
        from dataclasses import replace

        success = result.get("success", False)

        # PEMS 점수 업데이트 (0.0 ~ 1.0 범위 클램프)
        new_pems = min(1.0, max(0.0, ctx.pems_score + (0.1 if success else -0.1)))

        # 실패 결과 rejected_buffer에 추가 (최대 5개 유지)
        new_rejected = ctx.rejected_buffer
        if not success and result.get("error"):
            new_rejected = (ctx.rejected_buffer + (result["error"][:100],))[-5:]

        # hot_context 업데이트 (최근 결과 요약)
        result_snippet = result.get("result", "")[:200]
        new_hot_context = f"{ctx.hot_context}\n[{result['action']}] {result_snippet}".strip()[-1000:]

        return replace(
            ctx,
            pems_score=new_pems,
            rejected_buffer=new_rejected,
            hot_context=new_hot_context,
            current_step=ctx.current_step + 1,
            execution_state="DONE" if success else "ERROR",
        )


