"""
weakness_miner.py — Self-Harness Weakness Mining 구현 (v1.0)
=============================================================
논문 "Self-Harness: Harnesses That Improve Themselves" (Shanghai AI Lab, 2026) 적용.

핵심 개념:
  - 에러를 개별 사건이 아닌 "재발 메커니즘" 단위로 클러스터링
  - 동일 패턴이 threshold 이상 반복 → 텔레그램 알림 + 06_에러보고서 자동 기록
  - "하네스 수정 제안"을 함께 출력 (어떤 핸들러/CLAUDE.md를 수정해야 하는지)

사용법:
    miner = WeaknessMiner()
    await miner.record_failure("cmd_scan", "KeyError: ema200", traceback_str)
    # 3회 이상 반복 시 자동 알림

연동:
    monitoring_engine.py — MetricSnapshot.error_flag=True 시 자동 호출
    handlers/*.py        — except 블록에서 record_failure() 호출
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("weakness_miner")

# ── 경로 설정 ───────────────────────────────────────────────
WEAKNESS_DB   = os.path.expanduser("~/.hermes/runtime/weakness.db")
ERROR_REPORT  = Path.home() / "Applications/Mjobsidian/wiki/00_Meta/06_에이전트_오류_및_재발방지_보고서.md"
SEND_TG       = str(Path.home() / "Applications/Mjauto/Scripts/send_telegram_msg.py")

# ── 핸들러 → 하네스 표면 매핑 (제안 생성용) ────────────────
HANDLER_HARNESS_MAP = {
    "cmd_scan":       "handlers/_stock.py — scan 로직 또는 stock_scanner.py",
    "cmd_stock":      "handlers/_stock.py — stock 분석 로직",
    "cmd_vault":      "handlers/_vault.py 또는 vault_scanner.py",
    "cmd_ingest":     "handlers/_ingest.py",
    "cmd_clip":       "handlers/_clip.py",
    "compute_signals": "modules/stock_signal_engine.py",
    "scan_market":    "modules/stock_scanner.py",
    "get_fundamentals": "modules/stock_fetcher.py — fundamentals 캐시",
}

REPEAT_THRESHOLD = 3   # 이 횟수 이상 반복 시 알림


class WeaknessMiner:
    """
    Weakness Mining 엔진.

    실패 트레이스를 (action, error_signature) 쌍으로 클러스터링하고,
    동일 패턴이 REPEAT_THRESHOLD 이상 반복 시 알림을 발송한다.
    """

    def __init__(self, db_path: str = WEAKNESS_DB):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._init_db()

    # ── DB 초기화 ────────────────────────────────────────────
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS failure_log (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp     REAL    NOT NULL,
                    action        TEXT    NOT NULL,
                    error_sig     TEXT    NOT NULL,
                    error_detail  TEXT,
                    notified      INTEGER DEFAULT 0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_action_sig ON failure_log(action, error_sig)")
            conn.commit()

    # ── 에러 서명 생성 (클러스터링 키) ──────────────────────
    @staticmethod
    def _make_signature(error_message: str) -> str:
        """
        에러 메시지에서 재발 메커니즘을 추출.
        예: "KeyError: 'ema200' in compute_signals" → "KeyError:ema200"
        숫자/경로/구체적 값은 제거하여 동일 유형 그룹화.
        """
        import re
        sig = error_message.split("\n")[0]          # 첫 줄만
        sig = re.sub(r"'[^']{20,}'", "'...'", sig)  # 긴 문자열 치환
        sig = re.sub(r"\d+", "N", sig)              # 숫자 치환
        sig = re.sub(r"/[^\s]+", "/PATH", sig)      # 경로 치환
        return sig[:120]                            # 최대 120자

    # ── 실패 기록 ───────────────────────────────────────────
    async def record_failure(
        self,
        action: str,
        error_message: str,
        detail: Optional[str] = None
    ) -> None:
        """
        실패를 기록하고, 반복 패턴 감지 시 알림 발송.

        Parameters
        ----------
        action        : 실패한 함수/명령어 이름 (예: "cmd_scan")
        error_message : 에러 메시지 첫 줄
        detail        : traceback 또는 추가 컨텍스트 (선택)
        """
        sig = self._make_signature(error_message)
        now = time.time()

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO failure_log (timestamp, action, error_sig, error_detail) VALUES (?,?,?,?)",
                (now, action, sig, detail or "")
            )
            conn.commit()

            # 최근 7일 내 동일 패턴 횟수 확인
            week_ago = now - 7 * 86400
            row = conn.execute(
                "SELECT COUNT(*) FROM failure_log WHERE action=? AND error_sig=? AND timestamp>?",
                (action, sig, week_ago)
            ).fetchone()
            count = row[0] if row else 0

            # 아직 알림 미발송인지 확인 (같은 패턴 반복 알림 방지: 3의 배수마다)
            if count >= REPEAT_THRESHOLD and count % REPEAT_THRESHOLD == 0:
                await self._fire_alert(action, sig, error_message, count, detail)

    # ── 알림 발송 ────────────────────────────────────────────
    async def _fire_alert(
        self,
        action: str,
        sig: str,
        error_message: str,
        count: int,
        detail: Optional[str]
    ) -> None:
        """반복 실패 패턴 감지 → 텔레그램 알림 + 에러보고서 기록."""

        harness_hint = HANDLER_HARNESS_MAP.get(action, f"handlers/ 또는 modules/ 내 {action} 관련 파일")
        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        # ① 텔레그램 알림
        msg = (
            f"⚠️ [Weakness Mining] 반복 실패 감지\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📍 명령어: {action}\n"
            f"🔁 반복 횟수: {count}회 (7일 내)\n"
            f"❌ 패턴: {sig}\n"
            f"🔧 하네스 수정 대상: {harness_hint}\n"
            f"⏰ 감지: {timestamp_str}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"→ /claude 로 수정 제안 요청 가능"
        )
        try:
            import subprocess
            subprocess.run(
                ["python3", SEND_TG, msg],
                timeout=10, capture_output=True
            )
        except Exception as e:
            log.warning(f"텔레그램 알림 실패: {e}")

        # ② 06_에러보고서 자동 append
        await self._append_to_report(action, sig, error_message, count, harness_hint, timestamp_str, detail)

    # ── 에러보고서 자동 기록 ─────────────────────────────────
    async def _append_to_report(
        self,
        action: str,
        sig: str,
        error_message: str,
        count: int,
        harness_hint: str,
        timestamp_str: str,
        detail: Optional[str]
    ) -> None:
        """06_에이전트_오류_및_재발방지_보고서.md에 자동 항목 추가."""
        if not ERROR_REPORT.exists():
            log.warning(f"에러보고서 파일 없음: {ERROR_REPORT}")
            return

        entry = f"""
---
### [{timestamp_str}] 자동 감지 — {action}

| 항목 | 내용 |
|---|---|
| **패턴** | `{sig}` |
| **반복 횟수** | {count}회 (최근 7일) |
| **하네스 수정 대상** | {harness_hint} |
| **원본 에러** | `{error_message[:200]}` |
| **감지 방법** | WeaknessMiner 자동 (Self-Harness 논문 기반) |

**재발 방지 제안**: Claude Code에서 `{harness_hint}` 수정 검토 필요.
"""
        try:
            with open(ERROR_REPORT, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception as e:
            log.warning(f"에러보고서 기록 실패: {e}")

    # ── Skill Coverage 측정 (arXiv 2606.20659) ──────────────
    def record_skill_invocation(self, skill_name: str):
        """스킬 호출 기록 — 커버리지 측정용."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS skill_invocations (
                    skill_name TEXT NOT NULL,
                    invoked_at REAL NOT NULL
                )
            """)
            conn.execute(
                "INSERT INTO skill_invocations (skill_name, invoked_at) VALUES (?, ?)",
                (skill_name, time.time())
            )
            conn.commit()

    def get_skill_coverage(self) -> dict:
        """등록된 스킬 vs 실제 호출된 스킬 커버리지 반환."""
        import glob
        skill_dirs = [
            os.path.expanduser("~/.hermes/skills"),
            os.path.expanduser("~/.claude/skills"),
        ]
        all_skills = set()
        for sd in skill_dirs:
            for path in glob.glob(f"{sd}/*/SKILL.md"):
                skill_name = os.path.basename(os.path.dirname(path))
                all_skills.add(skill_name)

        with sqlite3.connect(self.db_path) as conn:
            try:
                rows = conn.execute(
                    "SELECT DISTINCT skill_name FROM skill_invocations"
                ).fetchall()
                invoked = {r[0] for r in rows}
            except Exception:
                invoked = set()

        covered = all_skills & invoked
        never_called = all_skills - invoked
        ratio = len(covered) / len(all_skills) if all_skills else 0.0
        return {
            "total": len(all_skills),
            "covered": len(covered),
            "never_called": sorted(never_called),
            "ratio": round(ratio, 2),
        }

    # ── SkillHarness 안전성 검증 (arXiv 2606.20636) ─────────
    def validate_skill_safety(self) -> dict:
        """SKILL.md 파일에 안전 제약이 포함됐는지 검사.

        Returns:
            {total, safe, unsafe_skills: [{name, path, missing}]}
        """
        import glob, re
        skill_dirs = [
            os.path.expanduser("~/.hermes/skills"),
            os.path.expanduser("~/.claude/skills"),
        ]
        safety_keywords = re.compile(
            r"(금지|위험|제한|forbidden|unsafe|lock.?stack|do.?not|주의|경고|warning|caution)",
            re.IGNORECASE
        )
        results = {"total": 0, "safe": 0, "unsafe_skills": []}
        for sd in skill_dirs:
            for skill_md in glob.glob(f"{sd}/*/SKILL.md"):
                skill_name = os.path.basename(os.path.dirname(skill_md))
                results["total"] += 1
                try:
                    content = open(skill_md, encoding="utf-8").read()
                    if safety_keywords.search(content):
                        results["safe"] += 1
                    else:
                        results["unsafe_skills"].append({
                            "name": skill_name,
                            "path": skill_md,
                            "missing": "안전 제약 키워드 없음",
                        })
                except Exception as e:
                    results["unsafe_skills"].append({
                        "name": skill_name,
                        "path": skill_md,
                        "missing": f"읽기 실패: {e}",
                    })
        return results

    # ── 현황 조회 ────────────────────────────────────────────
    def get_top_failures(self, days: int = 7, top_n: int = 5) -> list:
        """최근 N일 내 가장 많이 반복된 실패 패턴 반환."""
        cutoff = time.time() - days * 86400
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("""
                SELECT action, error_sig, COUNT(*) as cnt
                FROM failure_log
                WHERE timestamp > ?
                GROUP BY action, error_sig
                ORDER BY cnt DESC
                LIMIT ?
            """, (cutoff, top_n)).fetchall()
        return [{"action": r[0], "signature": r[1], "count": r[2]} for r in rows]


# ── 싱글턴 인스턴스 ──────────────────────────────────────────
_miner: Optional[WeaknessMiner] = None

def get_weakness_miner() -> WeaknessMiner:
    global _miner
    if _miner is None:
        _miner = WeaknessMiner()
    return _miner
