"""
Harness V2.5 — 자율 에러 복구형 Bash 실행기 (v2: Trajectory Regulation Layer)
=============================================================================
- 에러 발생 시 Gemma4 LLM이 원인을 분석하여 대체 명령어를 자동 수립
- 최대 3회까지 자율 수정 후 재실행 (회복 탄력성)
- **Trajectory Regulation**: 반복 패턴 감지, 정체 감지, 타임아웃 에스컬레이션
- 에러 복구 성공 시 skill_evolver.py가 SKILL.md를 자동 생성하여 학습
"""
import subprocess
import asyncio
import sys
import os
import time
import json
import sqlite3
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

sys.path.append("/Users/bluesea/.hermes/plugins")
sys.path.append("/Users/bluesea/Applications/Mjauto/Scripts/modules")

log = logging.getLogger("executor")


# ── Trajectory Regulation ───────────────────────────────────
class TrajectoryRegulator:
    """
    Trajectory Regulation Layer (arXiv 2605.22166 기반)

    - LOOP 감지: 동일한 명령어가 반복되는 패턴 차단
    - STAGNATION 감지: 연속 실패 추세 분석
    - ESCALATION: 타임아웃/심각도 기반 자동 에스컬레이션
    """

    def __init__(self, max_loops: int = 2, stagnation_window: int = 5):
        self._history = []           # (timestamp, cmd, exit_code)
        self._loop_counter = defaultdict(int)
        self.max_loops = max_loops
        self.stagnation_window = stagnation_window

    def record(self, cmd: str, exit_code: int):
        """명령어 실행 결과 기록."""
        now = time.time()
        self._history.append((now, cmd, exit_code))
        self._loop_counter[cmd] += 1

    def detect_loop(self, cmd: str) -> bool:
        """동일 명령어 반복 감지."""
        return self._loop_counter.get(cmd, 0) > self.max_loops

    def detect_stagnation(self) -> bool:
        """연속 실패 추세 감지."""
        if len(self._history) < self.stagnation_window:
            return False
        recent = self._history[-self.stagnation_window:]
        failures = sum(1 for _, _, code in recent if code != 0)
        # 5회 연속 중 4회 이상 실패 = 정체
        return failures >= 4

    def get_trajectory_summary(self) -> dict:
        """현재 궤적 요약."""
        return {
            "total_attempts": len(self._history),
            "unique_commands": len(set(c for _, c, _ in self._history)),
            "last_command": self._history[-1][1] if self._history else None,
            "loop_detected": any(
                c for c, n in self._loop_counter.items() if n > self.max_loops
            ),
            "stagnation_detected": self.detect_stagnation(),
        }

    def reset(self):
        self._history.clear()
        self._loop_counter.clear()


# ── 액션 로그 (SQLite) ─────────────────────────────────────
def _init_action_log():
    """SQLite 액션 로그 초기화."""
    import sqlite3
    log_dir = Path("/Users/bluesea/.hermes/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    db_path = log_dir / "action_log.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS action_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            action TEXT NOT NULL,
            command TEXT,
            exit_code INTEGER,
            error_msg TEXT,
            trajectory JSON,
            duration_ms INTEGER
        )
    """)
    conn.commit()
    return conn


def _log_action(conn, action: str, cmd: str, exit_code: int, error_msg: str,
                trajectory: dict, duration_ms: int):
    """액션 로그 기록."""
    if conn is None:
        return
    try:
        conn.execute(
            "INSERT INTO action_log (timestamp, action, command, exit_code, "
            "error_msg, trajectory, duration_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (datetime.now().isoformat(), action, cmd, exit_code,
             error_msg[:500] if error_msg else "",
             json.dumps(trajectory, ensure_ascii=False), duration_ms)
        )
        conn.commit()
    except Exception as e:
        log.debug(f"액션 로그 기록 실패: {e}")


def _get_action_stats(conn, hours: int = 24) -> dict:
    """최근 N시간 액션 통계 조회."""
    if conn is None:
        return {"error": "로그 DB 없음"}
    try:
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        cursor = conn.execute(
            "SELECT action, COUNT(*), SUM(CASE WHEN exit_code=0 THEN 1 ELSE 0 END) "
            "FROM action_log WHERE timestamp > ? GROUP BY action",
            (cutoff,)
        )
        stats = {}
        for action, total, success in cursor.fetchall():
            stats[action] = {"total": total, "success": success}
        return stats
    except Exception as e:
        return {"error": str(e)}


# ── 메인 실행기 ────────────────────────────────────────────

async def execute_bash_command(cmd: str, llm_router=None, max_retries: int = 3) -> str:
    """
    Bash 명령어 실행. 에러 발생 시 LLM으로 대체 명령어를 생성하여 재시도.
    Trajectory Regulation 레이어가 루프/정체를 감지하고 자동 중단합니다.

    Args:
        cmd:        실행할 명령어
        llm_router: hybrid_router 인스턴스 (None이면 단순 실행)
        max_retries: 최대 재시도 횟수

    Returns:
        실행 결과 문자열
    """
    original_cmd = cmd
    attempt = 0
    regulator = TrajectoryRegulator(max_loops=2, stagnation_window=5)
    conn = _init_action_log()
    start_time = time.time()

    while attempt <= max_retries:
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd="/Users/bluesea"
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=30)
            except asyncio.TimeoutError:
                proc.kill()
                raise subprocess.TimeoutExpired(cmd, 30)
            stdout = stdout_b.decode().strip()
            stderr = stderr_b.decode().strip()
            result = type('R', (), {'returncode': proc.returncode, 'stdout': stdout, 'stderr': stderr})()
            duration_ms = int((time.time() - start_time) * 1000)

            # Trajectory 기록
            regulator.record(cmd, result.returncode)

            # ── LOOP 감지 ──────────────────────────────────
            if regulator.detect_loop(cmd):
                _log_action(conn, "loop_blocked", cmd, result.returncode,
                           "LOOP 감지: 동일 명령어 반복 차단",
                           regulator.get_trajectory_summary(), duration_ms)
                return (
                    f"⛔ **Trajectory Regulation: 반복 명령어 차단됨**\n"
                    f"명령어 `{cmd}` 가 {regulator.max_loops}회 이상 반복되었습니다.\n"
                    f"마지막 에러: {stderr[:200]}\n"
                    f"💡 다른 접근 방식으로 다시 시도해 주세요."
                )

            # ── STAGNATION 감지 ─────────────────────────────
            if regulator.detect_stagnation():
                _log_action(conn, "stagnation_detected", cmd, result.returncode,
                           "STAGNATION 감지: 연속 실패 추세",
                           regulator.get_trajectory_summary(), duration_ms)
                return (
                    f"⛔ **Trajectory Regulation: 정체 상태 감지됨**\n"
                    f"최근 {regulator.stagnation_window}회 실행 중 대부분 실패했습니다.\n"
                    f"마지막 에러: {stderr[:200]}\n"
                    f"💡 시스템 상태를 확인하거나 다른 접근 방식으로 시도해 주세요."
                )

            # 성공 (stderr 없거나 returncode=0)
            if result.returncode == 0 or not stderr:
                if attempt > 0:
                    # 에러 복구 성공 → SKILL.md 및 절차 기억 등록
                    _try_record_skill(
                        original_cmd=original_cmd,
                        error_msg=_last_error,
                        fixed_cmd=cmd,
                        retries=attempt,
                    )
                    _try_record_procedural_memory(
                        action_name=f"Fix command: {original_cmd}",
                        command_sequence=[original_cmd, cmd],
                        success=True
                    )
                    _log_action(conn, "auto_recovery_success", cmd, 0,
                               f"복구 성공 after {attempt}회",
                               regulator.get_trajectory_summary(), duration_ms)
                    return (
                        f"✅ **에러 자율 복구 성공** ({attempt}회 수정)\n"
                        f"🔧 복구 명령어: `{cmd}`\n\n"
                        f"STDOUT:\n{stdout or '(출력 없음)'}\n"
                        f"💡 SKILL.md와 절차 기억(Procedural Memory)이 학습 DB에 자동 저장되었습니다."
                    )

                _log_action(conn, "exec_success", cmd, 0, "",
                           regulator.get_trajectory_summary(), duration_ms)
                output = stdout or "(출력 없음)"
                return f"✅ 실행 완료\nSTDOUT:\n{output}"

            # 에러 발생
            _last_error = stderr
            log.warning(f"[Attempt {attempt+1}] 에러 발생: {stderr[:100]}")

            _log_action(conn, "exec_failure", cmd, result.returncode,
                       stderr[:200], regulator.get_trajectory_summary(), duration_ms)

            if not llm_router or attempt >= max_retries:
                break

            # 절차 기억(Procedural Memory)에서 기존 성공 패턴이 있는지 복기
            cached_cmds = _try_recall_procedural_memory(f"Fix command: {cmd}")
            if cached_cmds and len(cached_cmds) > 1:
                new_cmd = cached_cmds[-1]
                log.info(f"[Attempt {attempt+1}] 🧠 절차 기억(Procedural Memory) 연상 복구 적용: {new_cmd}")
                cmd = new_cmd
                attempt += 1
                start_time = time.time()
                regulator.reset()  # 새 명령어로 리셋
                continue

            # LLM에게 대체 명령어 요청
            new_cmd = _ask_llm_for_fix(cmd, stderr, llm_router)
            if new_cmd and new_cmd != cmd:
                log.info(f"[Attempt {attempt+1}] LLM 대체 명령어: {new_cmd}")
                cmd = new_cmd
                regulator.reset()  # 새 명령어로 리셋
            else:
                break

        except subprocess.TimeoutExpired:
            duration_ms = int((time.time() - start_time) * 1000)
            _log_action(conn, "timeout", cmd, -1, "30초 타임아웃",
                       regulator.get_trajectory_summary(), duration_ms)
            return "❌ 명령어 실행 시간 초과 (30초)"
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            _log_action(conn, "exception", cmd, -1, str(e)[:200],
                       regulator.get_trajectory_summary(), duration_ms)
            return f"❌ 실행 중 예외 발생: {e}"

        attempt += 1
        start_time = time.time()

    # 최대 재시도 초과 실패
    duration_ms = int((time.time() - start_time) * 1000)
    _log_action(conn, "max_retries_exceeded", original_cmd, -1,
               f"{max_retries}회 재시도 후 실패",
               regulator.get_trajectory_summary(), duration_ms)
    return (
        f"❌ **자율 복구 실패** (최대 {max_retries}회 시도)\n"
        f"마지막 에러:\n{_last_error[:500] if '_last_error' in dir() else '알 수 없음'}"
    )


def _ask_llm_for_fix(cmd: str, error_msg: str, llm_router) -> Optional[str]:
    """LLM에게 대체 명령어 요청."""
    try:
        prompt = [
            {
                "role": "user",
                "content": (
                    f"Bash 명령어 실행 중 에러가 발생했습니다.\n"
                    f"원본 명령어: `{cmd}`\n"
                    f"에러 메시지:\n{error_msg[:500]}\n\n"
                    f"에러를 수정한 새로운 Bash 명령어만 한 줄로 출력하세요. "
                    f"설명이나 마크다운 없이 명령어 텍스트만 출력하세요."
                )
            }
        ]
        new_cmd, _ = llm_router.send_completion(prompt)
        new_cmd = new_cmd.strip().strip("`").strip()
        return new_cmd if new_cmd and new_cmd != cmd else None
    except Exception:
        return None


def _try_record_skill(original_cmd, error_msg, fixed_cmd, retries):
    """에러 복구 성공 시 skill_evolver에 SKILL.md 생성 요청."""
    try:
        from skill_evolver import record_exec_recovery
        skill_path = record_exec_recovery(
            original_cmd=original_cmd,
            error_msg=error_msg,
            fixed_cmd=fixed_cmd,
            retries=retries,
        )
        if skill_path:
            log.info(f"✨ SKILL.md 자동 생성: {skill_path}")
    except Exception as e:
        log.debug(f"SKILL.md 생성 스킵: {e}")


def _try_record_procedural_memory(action_name, command_sequence, success=True):
    """에러 복구 성공 시 Bio-Memory에 절차 기억으로 저장."""
    try:
        from bio_memory_engine import BioMemoryEngine
        mem = BioMemoryEngine()
        mem.save_procedural_memory(action_name, command_sequence, success)
    except Exception as e:
        log.debug(f"절차 기억 저장 스킵: {e}")


def _try_recall_procedural_memory(action_query):
    """에러 발생 시 Bio-Memory에서 기존 복구 경험 복기."""
    try:
        from bio_memory_engine import BioMemoryEngine
        mem = BioMemoryEngine()
        match = mem.recall_procedural_memory(action_query)
        if match and match.get("success", True):
            return match.get("commands", [])
    except Exception as e:
        log.debug(f"절차 기억 인출 스킵: {e}")
    return []


# ── 액션 로그 조회 API ─────────────────────────────────────
def get_action_log_stats(hours: int = 24) -> dict:
    """최근 N시간 액션 로그 통계."""
    conn = _init_action_log()
    return _get_action_stats(conn, hours)


# ── 하위 호환용 ────────────────────────────────────────────
_last_error = ""
