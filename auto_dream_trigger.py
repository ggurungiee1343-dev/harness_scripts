#!/usr/bin/env python3
"""
auto_dream_trigger.py — L2→L3 Dreaming 자동 트리거 (로드맵 P1-①)

L2 episodic 미처리 항목이 임계값(20개) 이상이면 Dreaming(증류)을 실행한다.
Lock Stack(bio_memory_engine 등)은 수정하지 않고 호출만 한다.

사용법:
  python3 auto_dream_trigger.py            # 임계 충족 시 실행
  python3 auto_dream_trigger.py --dry-run  # 미처리 개수만 확인
  python3 auto_dream_trigger.py --force    # 임계 무관 강제 실행

launchd: ~/Library/LaunchAgents/com.hermes.autodream.plist (일요일 03:30)
"""
import sys
import os
import json
import asyncio
import subprocess
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR / "modules"))

MEM_DIR    = Path.home() / ".hermes" / "runtime" / "memory"
L2_PATH    = MEM_DIR / "episodic_memory.json"
STATE_PATH = MEM_DIR / "consolidator_state.json"
LOG_PATH   = Path.home() / ".hermes" / "runtime" / "auto_dream.log"
THRESHOLD  = 20


def log(msg: str):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def pending_count() -> tuple:
    """(L2 항목 수, 마지막 처리 인덱스, 미처리 수) 반환"""
    try:
        l2 = json.loads(L2_PATH.read_text())
        episodes = l2.get("episodes", []) if isinstance(l2, dict) else l2
        total = len(episodes)
    except Exception as e:
        log(f"L2 로드 실패: {e}")
        return 0, 0, 0
    try:
        state = json.loads(STATE_PATH.read_text())
        last = state.get("last_processed_index", 0)
    except Exception:
        last = 0
    return total, last, max(0, total - last)


def notify(msg: str):
    try:
        subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "send_telegram_msg.py"), msg],
            timeout=30, capture_output=True,
        )
    except Exception as e:
        log(f"텔레그램 알림 실패: {e}")


async def run_dream():
    from memory_engine import MemoryEngine
    from dotenv import load_dotenv
    load_dotenv(SCRIPTS_DIR.parent / ".env")  # DEEPSEEK_API_KEY (config.py와 동일 경로)

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY 없음 — Mjauto/.env 확인")

    async def llm_func(prompt):
        # 봇 스택(hybrid_router는 harness_agent 기동 전 Dummy) 의존 없이 DeepSeek 직접 호출
        import urllib.request
        body = json.dumps({
            "model": "deepseek-chat",
            "messages": prompt if isinstance(prompt, list) else [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        }).encode()
        req = urllib.request.Request(
            "https://api.deepseek.com/chat/completions",
            data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
        )
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(
            None, lambda: urllib.request.urlopen(req, timeout=120).read())
        content = json.loads(resp)["choices"][0]["message"]["content"]
        return content, "deepseek-chat"

    # 단기 히스토리: harness_memory.json 최근 20개 (handlers/_memory.py와 동일 패턴)
    history_data = []
    try:
        hm = json.loads((SCRIPTS_DIR / "harness_memory.json").read_text())
        msgs = hm if isinstance(hm, list) else hm.get("messages", [])
        history_data = [m for m in msgs if isinstance(m, dict) and "role" in m][-20:]
    except Exception as e:
        log(f"히스토리 로드 실패(무시 가능): {e}")

    engine = MemoryEngine()
    result = await engine.dream(history_data=history_data, llm_func=llm_func)

    # 처리 인덱스 동기화 — dream()이 실패 문자열(❌)을 반환하면 동기화하지 않음
    if not result.lstrip().startswith("❌"):
        total, _, _ = pending_count()
        STATE_PATH.write_text(json.dumps({"last_processed_index": total}))
    return result


def main():
    dry   = "--dry-run" in sys.argv
    force = "--force" in sys.argv

    total, last, pending = pending_count()
    log(f"L2 {total}항목 | 처리됨 {last} | 미처리 {pending} (임계 {THRESHOLD})")

    if dry:
        log("dry-run 종료")
        return

    if pending < THRESHOLD and not force:
        log("임계 미달 — 실행 안 함")
        return

    log("Dreaming 시작...")
    try:
        result = asyncio.run(run_dream())
        log(f"완료: {result[:200]}")
        notify(f"🌙 자동 Dreaming 완료 — L2 미처리 {pending}건 증류\n{result[:300]}")
    except Exception as e:
        log(f"실패: {e}")
        notify(f"❌ 자동 Dreaming 실패: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
