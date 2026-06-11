"""
proposal_validator.py — Self-Harness Proposal Validation 구현 (v1.0)
=====================================================================
논문 "Self-Harness" 3단계: Proposal Validation
핸들러/모듈 수정 전 "regression test" — held-in + held-out 케이스로 검증.

핵심 원칙 (논문):
  - 수정 후 held-in(알려진 케이스) AND held-out(미知 케이스) 둘 다 개선돼야 승인
  - 하나라도 나빠지면 거부 (trade-off 방지)
  - 이력 로그로 모든 수정 감사 가능

MJ 시스템 적용:
  - "held-in"  = weakness_miner.db에 기록된 반복 실패 패턴 (이미 알려진 버그)
  - "held-out" = 최근 24시간 내 정상 동작한 명령어 (regression 기준)
  - 수정 제안 시 이 두 케이스로 체크리스트 자동 생성
  - Claude Code 세션에서 수정 전 자동 실행

사용법:
    validator = ProposalValidator()

    # 수정 전 체크리스트 생성
    checklist = validator.pre_check("handlers/_stock.py", "cmd_scan KeyError 수정")
    print(checklist)

    # 수정 후 결과 기록
    validator.record_result("handlers/_stock.py", passed=True, detail="KeyError 해결, 기존 5개 케이스 통과")
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("proposal_validator")

VALIDATOR_DB  = os.path.expanduser("~/.hermes/runtime/proposal_validation.db")
WEAKNESS_DB   = os.path.expanduser("~/.hermes/runtime/weakness.db")
REPORT_PATH   = Path.home() / "Applications/Mjobsidian/wiki/00_Meta/06_에이전트_오류_및_재발방지_보고서.md"

# 핸들러 → 기능 설명 (held-out 케이스 힌트용)
HANDLER_FUNCTIONS = {
    "handlers/_stock.py":    ["cmd_stock(TICKER)", "cmd_scan()", "차트 분석"],
    "handlers/_vault.py":    ["cmd_vault check", "cmd_vault duplicates", "cmd_vault graph"],
    "handlers/_file.py":     ["cmd_web(url)", "cmd_ingest()", "cmd_recent()"],
    "handlers/_system.py":   ["cmd_exec(bash)", "cmd_status()", "cmd_audit()"],
    "handlers/_grill.py":    ["cmd_grill(doc, question)"],
    "handlers/_research.py": ["cmd_research local", "cmd_research timeline"],
    "modules/stock_signal_engine.py": ["compute_signals(df)"],
    "modules/stock_scanner.py":       ["scan_market(symbols)"],
    "modules/stock_fetcher.py":       ["get_fundamentals(symbol)", "update_trailing_stop()"],
    "modules/weakness_miner.py":      ["record_failure()", "get_top_failures()"],
}


class ProposalValidator:
    """
    수정 제안 검증 엔진.
    수정 전: 체크리스트 생성 (held-in 실패 케이스 + held-out 정상 케이스)
    수정 후: 결과 기록 및 승인/거부 판단
    """

    def __init__(self, db_path: str = VALIDATOR_DB):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS validation_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   REAL    NOT NULL,
                    target_file TEXT    NOT NULL,
                    description TEXT,
                    phase       TEXT    NOT NULL,  -- 'pre_check' | 'result'
                    passed      INTEGER,           -- NULL=미완료, 1=승인, 0=거부
                    detail      TEXT,
                    held_in_ok  INTEGER,
                    held_out_ok INTEGER
                )
            """)
            conn.commit()

    def _get_known_failures(self, target_file: str) -> list[dict]:
        """weakness_miner DB에서 이 파일 관련 반복 실패 패턴 조회."""
        if not os.path.exists(WEAKNESS_DB):
            return []

        # 파일명에서 action 이름 추정
        base = os.path.basename(target_file).replace(".py", "")
        handler_name = base.lstrip("_")

        week_ago = time.time() - 7 * 86400
        try:
            with sqlite3.connect(WEAKNESS_DB) as conn:
                rows = conn.execute("""
                    SELECT action, error_sig, COUNT(*) as cnt
                    FROM failure_log
                    WHERE timestamp > ? AND (
                        action LIKE ? OR action LIKE ?
                    )
                    GROUP BY action, error_sig
                    ORDER BY cnt DESC
                    LIMIT 5
                """, (week_ago, f"%{handler_name}%", f"cmd_{handler_name}%")).fetchall()
            return [{"action": r[0], "signature": r[1], "count": r[2]} for r in rows]
        except Exception:
            return []

    def pre_check(self, target_file: str, description: str = "") -> str:
        """
        수정 전 검증 체크리스트 생성.
        Returns: 사람이 읽을 수 있는 체크리스트 문자열
        """
        known_failures = self._get_known_failures(target_file)
        held_out_cases = HANDLER_FUNCTIONS.get(target_file, ["기본 동작"])

        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        # DB에 pre_check 기록
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO validation_log (timestamp, target_file, description, phase) VALUES (?,?,?,?)",
                (time.time(), target_file, description, "pre_check")
            )
            conn.commit()

        lines = [
            f"🔍 Proposal Validation 체크리스트",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📁 수정 대상: {target_file}",
            f"📝 수정 내용: {description}",
            f"⏰ 시각: {timestamp_str}",
            "",
            "【Held-in: 알려진 실패 케이스 — 수정 후 반드시 해결돼야 함】",
        ]

        if known_failures:
            for f in known_failures:
                lines.append(f"  ❌ {f['action']}: {f['signature']} ({f['count']}회)")
        else:
            lines.append("  ℹ️ 최근 7일 반복 실패 없음 (weakness DB 기준)")

        lines += [
            "",
            "【Held-out: 정상 동작 케이스 — 수정 후 깨지면 안 됨】",
        ]
        for case in held_out_cases:
            lines.append(f"  ✓ {case}")

        lines += [
            "",
            "【승인 기준 (Self-Harness 논문)】",
            "  → held-in 케이스 개선 AND held-out 케이스 유지 시 승인",
            "  → 하나라도 나빠지면 거부 (trade-off 불허)",
            "",
            "수정 완료 후: validator.record_result(file, passed, detail) 호출",
        ]

        return "\n".join(lines)

    def record_result(
        self,
        target_file: str,
        passed: bool,
        detail: str = "",
        held_in_ok: bool = True,
        held_out_ok: bool = True,
    ) -> None:
        """수정 후 검증 결과 기록."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO validation_log
                (timestamp, target_file, description, phase, passed, detail, held_in_ok, held_out_ok)
                VALUES (?,?,?,?,?,?,?,?)
            """, (time.time(), target_file, detail, "result",
                  1 if passed else 0, detail,
                  1 if held_in_ok else 0, 1 if held_out_ok else 0))
            conn.commit()

        status = "✅ 승인" if passed else "❌ 거부"
        log.info(f"Proposal Validation [{status}] {target_file}: {detail}")

        # 거부 시 에러보고서 자동 기록
        if not passed:
            self._append_rejection(target_file, detail, held_in_ok, held_out_ok)

    def _append_rejection(self, target_file: str, detail: str, held_in_ok: bool, held_out_ok: bool):
        if not REPORT_PATH.exists():
            return
        entry = f"""
---
### [{datetime.now().strftime('%Y-%m-%d %H:%M')}] Proposal 거부 — {target_file}

| 항목 | 결과 |
|---|---|
| **수정 대상** | `{target_file}` |
| **Held-in 통과** | {'✅' if held_in_ok else '❌'} |
| **Held-out 통과** | {'✅' if held_out_ok else '❌'} |
| **거부 이유** | {detail} |
| **판단 기준** | Self-Harness: 둘 다 개선돼야 승인 |
"""
        try:
            with open(REPORT_PATH, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception as e:
            log.warning(f"에러보고서 기록 실패: {e}")

    def get_history(self, target_file: Optional[str] = None, last_n: int = 10) -> list[dict]:
        """검증 이력 조회."""
        with sqlite3.connect(self.db_path) as conn:
            if target_file:
                rows = conn.execute("""
                    SELECT timestamp, target_file, phase, passed, detail
                    FROM validation_log WHERE target_file=?
                    ORDER BY timestamp DESC LIMIT ?
                """, (target_file, last_n)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT timestamp, target_file, phase, passed, detail
                    FROM validation_log ORDER BY timestamp DESC LIMIT ?
                """, (last_n,)).fetchall()
        return [
            {
                "time": datetime.fromtimestamp(r[0]).strftime("%m-%d %H:%M"),
                "file": r[1], "phase": r[2],
                "passed": "✅" if r[3] == 1 else ("❌" if r[3] == 0 else "⏳"),
                "detail": r[4] or ""
            }
            for r in rows
        ]


# ── 싱글턴 ──────────────────────────────────────────────────
_validator: Optional[ProposalValidator] = None

def get_validator() -> ProposalValidator:
    global _validator
    if _validator is None:
        _validator = ProposalValidator()
    return _validator
