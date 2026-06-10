"""
Harness V2.5 — Subconscious Layer (헬스체크/백그라운드 진단)
============================================================
Life-Harness Layer 5: 주기적 상태 진단 및 자가 치유

실행 주기: Launchd로 3회/일 (08:00, 14:00, 21:00)
또는 Telegram /hstatus 명령어로 수동 호출 가능

기능:
- CPU/메모리/디스크 상태 진단
- Launchd 서비스 동작 확인
- 로그 파일 사이즈 모니터링
- 오래된 스킬 자동 정리 (30일 이상 미사용)
- Dialectic 자동 연결 실행
"""

import os
import subprocess
import json
import logging
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

log = logging.getLogger("subconscious")

# ── 경로 ──────────────────────────────────────────────────
HERMES_HOME = Path("/Users/bluesea/.hermes")
SCRIPTS_DIR  = Path("/Users/bluesea/Applications/Mjauto/Scripts")
VAULT_DIR    = Path("/Users/bluesea/Applications/Mjobsidian")


def check_system_health() -> dict:
    """시스템 리소스 상태 진단."""
    result = {}

    # CPU
    try:
        cpu = subprocess.run(
            ["ps", "-A", "-o", "%cpu", "--sort=-%cpu"],
            capture_output=True, text=True, timeout=5
        )
        lines = cpu.stdout.strip().split("\n")[1:]
        cpu_vals = [float(l.strip()) for l in lines if l.strip().replace(".", "").isdigit()]
        result["cpu"] = {
            "top_pct": round(cpu_vals[0], 1) if cpu_vals else 0,
            "avg_pct": round(sum(cpu_vals) / len(cpu_vals), 1) if cpu_vals else 0,
            "status": "🟢" if (cpu_vals and cpu_vals[0] < 80) else "🟡" if cpu_vals else "⚪",
        }
    except Exception as e:
        result["cpu"] = {"error": str(e)}

    # Memory
    try:
        vm = subprocess.run(
            ["vm_stat"], capture_output=True, text=True, timeout=5
        )
        # Parse vm_stat output
        for line in vm.stdout.split("\n"):
            if "free" in line.lower() and "Pages" in line:
                free_pages = int(line.split(":")[1].strip().rstrip("."))
                break
        page_size = 16384  # macOS default
        free_mb = (free_pages * page_size) / (1024 * 1024)
        # Total memory
        total = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True, text=True, timeout=5
        )
        total_mb = int(total.stdout.strip()) / (1024 * 1024)
        used_pct = round((1 - free_mb / total_mb) * 100, 1)
        result["memory"] = {
            "total_mb": round(total_mb, 0),
            "free_mb": round(free_mb, 0),
            "used_pct": used_pct,
            "status": "🟢" if used_pct < 80 else "🟡" if used_pct < 90 else "🔴",
        }
    except Exception as e:
        result["memory"] = {"error": str(e)}

    # Disk
    try:
        df = subprocess.run(
            ["df", "-h", "/"], capture_output=True, text=True, timeout=5
        )
        parts = df.stdout.strip().split("\n")[1].split()
        if len(parts) >= 5:
            result["disk"] = {
                "total": parts[1],
                "used": parts[2],
                "available": parts[3],
                "use_pct": parts[4],
                "status": "🟢" if int(parts[4].rstrip("%")) < 80 else "🟡",
            }
        else:
            result["disk"] = {"error": "df parse failed"}
    except Exception as e:
        result["disk"] = {"error": str(e)}

    return result


def check_services() -> dict:
    """Launchd 서비스 상태 진단."""
    services = {"hermes_bot": "com.hermes.bot", "hermes_webui": "com.hermes.webui"}
    result = {}
    try:
        out = subprocess.run(
            ["launchctl", "list"],
            capture_output=True, text=True, timeout=5
        )
        for name, label in services.items():
            if label in out.stdout:
                result[name] = "🟢" if "PID" in out.stdout.split(label)[1][:3] else "🟡"
            else:
                result[name] = "🔴"
    except Exception as e:
        for name in services:
            result[name] = f"⚠️ {str(e)}"
    return result


def check_log_sizes() -> list:
    """로그 파일 사이즈 확인."""
    logs = [
        SCRIPTS_DIR / "hermes_launchd.log",
        SCRIPTS_DIR / "hermes_launchd.error.log",
    ]
    result = []
    for log_path in logs:
        if log_path.exists():
            size_kb = log_path.stat().st_size / 1024
            status = "🟢" if size_kb < 1024 else "🟡" if size_kb < 10240 else "🔴"
            result.append({"path": log_path.name, "size_kb": round(size_kb, 1), "status": status})
        else:
            result.append({"path": log_path.name, "size_kb": 0, "status": "⚪"})
    return result


def run_auto_maintenance() -> dict:
    """자동 유지보수 실행 (Subconscious 일일 태스크)."""
    actions = {}

    # 1. 오래된 스킬 정리 (30일 이상)
    try:
        from modules.skill_evolver import prune_old_skills
        targets = prune_old_skills(days=30, dry_run=False)
        actions["pruned_skills"] = len(targets)
    except Exception as e:
        actions["prune_error"] = str(e)

    # 2. Dialectic 자동 연결
    try:
        from modules.dialectic_layer import auto_link_from_session_logs
        links = auto_link_from_session_logs()
        actions["dialectic_links"] = links
    except Exception as e:
        actions["dialectic_error"] = str(e)

    # 3. Action Log 정리 (30일 이상)
    try:
        log_path = Path("/Users/bluesea/.hermes/logs/action_log.db")
        if log_path.exists():
            conn = sqlite3.connect(str(log_path))
            cutoff = (datetime.now() - timedelta(days=30)).isoformat()
            cursor = conn.execute("DELETE FROM action_log WHERE timestamp < ?", (cutoff,))
            actions["action_log_cleaned"] = cursor.rowcount
            conn.commit()
            conn.close()
    except Exception as e:
        actions["action_log_error"] = str(e)

    # 4. 로그 회전 (10MB 이상)
    try:
        for log_path in [SCRIPTS_DIR / "hermes_launchd.log",
                         SCRIPTS_DIR / "hermes_launchd.error.log"]:
            if log_path.exists() and log_path.stat().st_size > 10 * 1024 * 1024:
                rotated = log_path.with_suffix(".log.old")
                if rotated.exists():
                    rotated.unlink()
                log_path.rename(rotated)
                log_path.touch()
                actions["rotated_logs"] = actions.get("rotated_logs", []) + [log_path.name]
    except Exception as e:
        actions["rotation_error"] = str(e)

    return actions


def generate_health_report() -> str:
    """
    종합 헬스 리포트 생성.
    Telegram /hstatus 명령어에서 호출 가능.
    """
    health = check_system_health()
    services = check_services()
    logs = check_log_sizes()

    lines = []
    lines.append("📊 **헤르메스 서브컨셔스 헬스 리포트**")
    lines.append(f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

    # CPU
    cpu = health.get("cpu", {})
    lines.append(f"**💻 CPU** {cpu.get('status', '⚪')}")
    if "top_pct" in cpu:
        lines.append(f"  최고 점유: {cpu['top_pct']}% | 평균: {cpu['avg_pct']}%")

    # Memory
    mem = health.get("memory", {})
    lines.append(f"**🧠 메모리** {mem.get('status', '⚪')}")
    if "total_mb" in mem:
        lines.append(f"  사용률: {mem['used_pct']}% | 여유: {mem['free_mb']:.0f}MB / {mem['total_mb']:.0f}MB")

    # Disk
    disk = health.get("disk", {})
    lines.append(f"**💾 디스크** {disk.get('status', '⚪')}")
    if "use_pct" in disk:
        lines.append(f"  사용률: {disk['use_pct']} | 여유: {disk.get('available', '?')}")

    # Services
    lines.append("\n**🛡️ 서비스 상태**")
    for name, status in services.items():
        lines.append(f"  {status} {name}")

    # Logs
    lines.append("\n**📋 로그 파일**")
    for log_info in logs:
        lines.append(f"  {log_info['status']} {log_info['path']} ({log_info['size_kb']}KB)")

    lines.append("\n---")
    lines.append("💡 `/hstatus` — 상세 진단")

    return "\n".join(lines)


# ── CLI 엔트리 포인트 (Launchd 용) ────────────────────────
if __name__ == "__main__":
    print(generate_health_report())
    print()
    actions = run_auto_maintenance()
    print(f"🔄 Auto-maintenance: {json.dumps(actions, ensure_ascii=False)}")
