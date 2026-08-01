"""
울산항도선사회(ulsanpilot.co.kr) 도선예보 배정표를 주기적으로 확인해,
MJ님 배정 건 중 회피 접안지/출항지(FROM/TO)가 걸리면 Hermes1 텔레그램 봇으로 즉시 알림.

동작 원리:
- 사이트 자체가 2분마다 자동 새로고침되는 페이지라, 이 스크립트도 cron/launchd로 비슷한 주기(2~3분) 실행 권장.
- get_cz_or_assign_s.php(오늘)·get_cz_or_assign_s02.php(내일) 두 엔드포인트를 그대로 호출(홈페이지 JS와 동일 방식).
- 이미 알림 보낸 건은 상태파일(.ulsan_pilot_seen.json)에 저장해 중복 알림 방지.
- 배정이 취소/시간변경되면 다음 폴링에서 자동으로 최신 상태로 갱신됨(같은 키로 다시 매칭되면 재알림 안 함).

사용법: python3 ulsan_pilot_watch.py
설정: 같은 폴더의 ulsan_pilot_config.json에서 PILOT_CODE·AVOID_KEYWORDS 수정
"""
import json
import os
import re
import sys
import time

import requests

FOLDER = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(FOLDER, "ulsan_pilot_config.json")
STATE_PATH = os.path.join(FOLDER, ".ulsan_pilot_seen.json")

BASE_URL = "http://www.ulsanpilot.co.kr/main/"
ENDPOINTS = ["get_cz_or_assign_s.php", "get_cz_or_assign_s02.php"]

HEADERS_ORDER = [
    "no", "status", "cf", "time", "ship", "pilot", "csign",
    "gt", "loa", "dft", "from", "to", "la", "ca", "sa", "bt", "t", "l", "q", "remarks",
]


def load_config():
    if not os.path.exists(CONFIG_PATH):
        default = {
            "PILOT_CODE": "MJ",
            "AVOID_KEYWORDS": [],
            "_설명": "AVOID_KEYWORDS에 FROM/TO(접안지·출항지 코드)에 포함되면 걸리는 키워드를 넣으세요. 예: [\"OTK\", \"YMP\"]",
        }
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(default, f, ensure_ascii=False, indent=2)
        print(f"설정파일이 없어 새로 만들었습니다: {CONFIG_PATH} — AVOID_KEYWORDS를 채운 뒤 다시 실행하세요.")
        sys.exit(0)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def strip_tags(s):
    return re.sub(r"<[^>]+>", "", s).strip()


def parse_rows(html):
    rows = re.findall(r"<tr>(.*?)</tr>", html, re.S)
    tds_pattern = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
    out = []
    for r in rows:
        tds = tds_pattern.findall(r)
        tds = [strip_tags(t) for t in tds]
        if len(tds) < len(HEADERS_ORDER):
            continue
        out.append(dict(zip(HEADERS_ORDER, tds)))
    return out


def fetch_all_rows():
    rows = []
    for ep in ENDPOINTS:
        try:
            resp = requests.get(BASE_URL + ep, timeout=15)
            resp.raise_for_status()
            rows.extend(parse_rows(resp.text))
        except Exception as e:
            print(f"[경고] {ep} 조회 실패: {e}")
    return rows


def matches_pilot(row, pilot_code):
    return bool(re.search(rf"\b{re.escape(pilot_code)}\b", row.get("pilot", "")))


def matches_avoid(row, avoid_keywords):
    hay = (row.get("from", "") + " " + row.get("to", "")).upper()
    for kw in avoid_keywords:
        if kw.upper() in hay:
            return kw
    return None


def row_key(row):
    return f"{row.get('time')}_{row.get('ship')}_{row.get('csign')}_{row.get('from')}_{row.get('to')}"


def send_telegram(text):
    sys.path.insert(0, FOLDER)
    import config as mj_config  # noqa: E402
    from dotenv import load_dotenv

    load_dotenv("/Users/bluesea/.hermes/.env")
    bot_token = os.getenv("HERMES1_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = mj_config.ALLOWED_ID
    if not bot_token or not chat_id:
        print("텔레그램 토큰/chat_id 없음 — 알림 전송 실패")
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)
        if resp.status_code != 200:
            print(f"텔레그램 전송 실패: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"텔레그램 전송 예외: {e}")


def main():
    cfg = load_config()
    pilot_code = cfg.get("PILOT_CODE", "MJ")
    avoid_keywords = cfg.get("AVOID_KEYWORDS", [])
    if not avoid_keywords:
        print("AVOID_KEYWORDS가 비어있습니다 — ulsan_pilot_config.json에 회피 접안지/출항지 코드를 채워주세요.")
        return

    state = load_state()
    rows = fetch_all_rows()

    my_rows = [r for r in rows if matches_pilot(r, pilot_code)]
    new_alerts = 0

    for row in my_rows:
        hit_kw = matches_avoid(row, avoid_keywords)
        if not hit_kw:
            continue
        key = row_key(row)
        if state.get(key) == "alerted":
            continue

        msg = (
            f"⚓️ <b>회피 배정 감지</b>\n"
            f"선명: {row.get('ship')}\n"
            f"시간: {row.get('time')}\n"
            f"FROM→TO: {row.get('from')} → {row.get('to')}\n"
            f"걸린 키워드: {hit_kw}\n"
            f"상태: {row.get('status')}\n"
            f"비고: {row.get('remarks') or '-'}"
        )
        send_telegram(msg)
        state[key] = "alerted"
        new_alerts += 1

    save_state(state)
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 확인 완료 — 내 배정 {len(my_rows)}건 중 신규 알림 {new_alerts}건")


if __name__ == "__main__":
    main()
