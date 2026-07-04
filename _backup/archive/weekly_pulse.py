#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
weekly_pulse.py — 주간 시스템 펄스 리포트 생성기
=================================================
매주 일요일 23:00 실행. 칸반 완료율, 로그 에러/경고, Git 커밋 수,
시스템 리소스를 수집하여 wiki/00_Meta/pulse/pulse_YYYY-WW.md 저장.
"""
import os
import sys
import re
import json
import subprocess
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ── 경로 설정 ──
SCRIPTS_DIR = "/Users/bluesea/Applications/Mjauto/Scripts"
VAULT_PATH = "/Users/bluesea/Applications/Mjobsidian"
PULSE_DIR = f"{VAULT_PATH}/wiki/00_Meta/pulse"
LOG_FILE = "/Users/bluesea/.hermes/weekly_pulse.log"
HOT_FILE = f"{VAULT_PATH}/wiki/00_Meta/hot.md"
KANBAN_DB = os.path.expanduser("~/.hermes/kanban.db")
DREAMER_LOG = "/Users/bluesea/.hermes/dreamer.log"

sys.path.insert(0, SCRIPTS_DIR)
sys.path.insert(0, f"{SCRIPTS_DIR}/modules")

# ── 로깅 ──
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("WeeklyPulse")

os.makedirs(PULSE_DIR, exist_ok=True)


# ── 헬퍼 ──

def _run_cmd(cmd: list[str], timeout: int = 30) -> str:
    """Shell 명령어 실행, stdout 반환."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception as e:
        return f"<error: {e}>"


def _iso_now() -> str:
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")


def _iso_week() -> str:
    """현재 ISO 주차 (YYYY-WW)."""
    now = datetime.now()
    iso = now.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _read_file(path: str) -> str:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def _write_file(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _update_hot_file(section_header: str, content: str) -> None:
    """hot.md의 특정 섹션을 업데이트하거나 추가."""
    hot = _read_file(HOT_FILE)
    if not hot:
        logger.warning(f"⚠️ hot.md 없음: {HOT_FILE}")
        return

    insert = f"{section_header}\n{content}\n\n"
    if section_header in hot:
        hot = re.sub(
            rf'{re.escape(section_header)}\n.*?(?=## |\Z)',
            insert, hot, flags=re.DOTALL
        )
    else:
        if hot.startswith("---"):
            parts = hot.split("---\n", 2)
            if len(parts) >= 3:
                hot = parts[0] + "---\n" + parts[1] + "---\n" + insert + parts[2]
            else:
                hot = insert + hot
        else:
            hot = insert + hot

    _write_file(HOT_FILE, hot)


# ── 수집 함수 ──

def collect_kanban_stats() -> dict:
    """칸반 완료율 조회."""
    if not os.path.exists(KANBAN_DB):
        return {"status": "⚠️ 칸반 DB 없음", "total": 0, "done": 0, "ratio": 0}

    try:
        import sqlite3
        conn = sqlite3.connect(KANBAN_DB)
        cur = conn.execute("SELECT column, COUNT(*) FROM cards GROUP BY column")
        cols = dict(cur.fetchall())
        conn.close()

        total = sum(cols.values())
        done = cols.get("DONE", 0)
        todo = cols.get("TODO", 0)
        in_progress = cols.get("IN_PROGRESS", 0)
        ratio = round(done / total * 100, 1) if total > 0 else 0

        return {
            "status": "✅ 정상",
            "total": total,
            "todo": todo,
            "in_progress": in_progress,
            "done": done,
            "ratio": ratio,
        }
    except Exception as e:
        logger.warning(f"칸반 조회 실패: {e}")
        return {"status": f"⚠️ 오류: {e}", "total": 0, "done": 0, "ratio": 0}


def collect_log_stats() -> dict:
    """Dreamer + Bot 로그에서 에러/경고 카운트 (최근 7일)."""
    now = datetime.now()
    week_ago = now - timedelta(days=7)
    result = {"errors": 0, "warnings": 0, "files_checked": []}

    log_paths = [
        DREAMER_LOG,
        "/Users/bluesea/.hermes/bot.log",
        "/Users/bluesea/.hermes/dreamer_stderr.log",
    ]

    for path in log_paths:
        if not os.path.exists(path):
            continue
        result["files_checked"].append(os.path.basename(path))
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    # 타임스탬프 파싱 (dreamer.log 형식)
                    ts_match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                    if ts_match:
                        try:
                            ts = datetime.strptime(ts_match.group(1), "%Y-%m-%d %H:%M:%S")
                            if ts < week_ago:
                                continue
                        except ValueError:
                            pass
                    if "[ERROR]" in line or " ERROR " in line:
                        result["errors"] += 1
                    elif "[WARNING]" in line or "[WARN]" in line or " WARNING " in line:
                        result["warnings"] += 1
        except Exception as e:
            logger.warning(f"로그 읽기 실패 ({path}): {e}")

    return result


def collect_git_stats() -> dict:
    """최근 7일간 Git 커밋 수."""
    try:
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        commits = _run_cmd(
            ["git", "log", f"--since={week_ago}", "--oneline"],
            timeout=15,
        )
        count = len([l for l in commits.split("\n") if l.strip()]) if commits else 0
        return {"commits": count}
    except Exception as e:
        return {"commits": 0, "error": str(e)}


def collect_system_stats() -> dict:
    """디스크/메모리 사용량."""
    disk = _run_cmd(["df", "-h", "/"])
    mem = _run_cmd(["vm_stat"])
    uptime = _run_cmd(["uptime"])
    return {
        "disk": disk.split("\n")[1] if disk else "N/A",
        "uptime": uptime,
    }


def send_telegram(msg: str) -> None:
    """텔레그램 알림 발송."""
    try:
        subprocess.Popen(
            ["python3", f"{SCRIPTS_DIR}/send_telegram_msg.py", msg]
        )
    except Exception as e:
        logger.warning(f"텔레그램 발송 실패: {e}")


# ── 메인 ──

def generate_pulse() -> str:
    """펄스 리포트 생성 및 저장."""
    week = _iso_week()
    now_str = _iso_now()

    logger.info(f"📊 주간 펄스 리포트 생성 중 — {week}")

    kanban = collect_kanban_stats()
    logs = collect_log_stats()
    git = collect_git_stats()
    system = collect_system_stats()

    # 리포트 본문
    lines = [
        f"# 📊 시스템 펄스 리포트 — {week}",
        f"",
        f"> 생성: {now_str}",
        f"",
        f"---",
        f"",
        f"## 칸반 진행률",
        f"",
        f"- **완료율**: {kanban.get('ratio', 0)}% ({kanban.get('done', 0)}/{kanban.get('total', 0)})",
        f"- TODO: {kanban.get('todo', 0)}개",
        f"- 진행중: {kanban.get('in_progress', 0)}개",
        f"- 완료: {kanban.get('done', 0)}개",
        f"- 상태: {kanban.get('status', 'N/A')}",
        f"",
        f"## 로그 분석 (최근 7일)",
        f"",
        f"- **오류**: {logs['errors']}건",
        f"- **경고**: {logs['warnings']}건",
        f"- 체크한 로그: {', '.join(logs['files_checked']) if logs['files_checked'] else '없음'}",
        f"",
        f"## Git 활동",
        f"",
        f"- **커밋 수**: {git.get('commits', 0)}개 (최근 7일)",
        f"",
        f"## 시스템 상태",
        f"",
        f"- **디스크**: {system.get('disk', 'N/A')}",
        f"- **Uptime**: {system.get('uptime', 'N/A')}",
        f"",
    ]
    report = "\n".join(lines)

    # 저장
    pulse_path = f"{PULSE_DIR}/pulse_{week}.md"
    _write_file(pulse_path, report)
    logger.info(f"✅ 펄스 리포트 저장: {pulse_path}")

    # hot.md 섹션 업데이트
    hot_entry = (
        f"**주간 펄스 {week}** — 칸반 {kanban.get('ratio', 0)}% "
        f"({kanban.get('done', 0)}/{kanban.get('total', 0)}), "
        f"로그 오류 {logs['errors']}건 / 경고 {logs['warnings']}건, "
        f"Git 커밋 {git.get('commits', 0)}개 | [{pulse_path}]({pulse_path})"
    )
    _update_hot_file("## 📊 주간 펄스 리포트", hot_entry)
    logger.info(f"✅ hot.md 업데이트 완료")

    # 텔레그램 알림 (간결)
    tg_msg = (
        f"📊 <b>주간 펄스 {week}</b>\n"
        f"칸반: {kanban.get('ratio', 0)}% 완료 ({kanban.get('done', 0)}/{kanban.get('total', 0)})\n"
        f"로그: 🔴{logs['errors']} ⚠️{logs['warnings']}\n"
        f"Git: {git.get('commits', 0)} 커밋"
    )
    send_telegram(tg_msg)

    return pulse_path


if __name__ == "__main__":
    path = generate_pulse()
    print(f"\n✅ 리포트 저장: {path}")
