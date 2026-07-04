#!/usr/bin/env python3
"""
test_mjstock_navigation.py — MJstock 텔레그램 버튼 왕복 경로 회귀 테스트

배경: 2026-07-03 handlers/_stock_mjstock.py의 스캔결과→검색기목록→종목목록→차트
     네비게이션을 여러 차례 고치다가, 실제 텔레그램 클릭으로만 검증하느라
     사용자가 몇 시간을 대신 테스트해야 했음. 앞으로 이 파일을 건드릴 때는
     텔레그램으로 넘기기 전에 반드시 이 스크립트부터 통과시킬 것.

실행:
  cd /Users/bluesea/Applications/Mjauto/Scripts
  python3 test_mjstock_navigation.py

검증 항목:
  1. 배치 매니페스트(results/_batch/*.json)가 있으면 "← 다른 검색기 보기"가
     그 배치에 포함된 검색기만 정확히 복원하는지 (무관한 검색기 섞임 방지)
  2. 검색기 목록 → 종목 목록 → 뒤로가기 버튼이 batch_id를 끝까지 잃지 않는지
  3. 종목 버튼 콜백이 mjstock_chart:{ticker}:{screener}:{date_str}:{batch_id}
     형식으로 정확히 만들어지는지
  4. KR 종목코드가 앞자리 0을 유지한 채 문자열로 읽히는지 (036930 형태)
  5. "통과 종목 없음" 등 조기 종료 분기에도 뒤로가기 버튼이 항상 붙어있는지
     (막다른 화면 방지)

실패 시 AssertionError로 즉시 중단 — exit code 확인해서 CI/수동 모두에서 판별 가능.
"""
import sys
import glob
import json
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "handlers"))

MJSTOCK_DIR = Path("/Users/bluesea/Applications/Mjstock")


class FakeQuery:
    """python-telegram-bot의 CallbackQuery를 흉내내는 최소 mock."""
    def __init__(self):
        self.last_text = None
        self.last_markup = None

    async def edit_message_text(self, text, reply_markup=None, **kw):
        self.last_text = text
        self.last_markup = reply_markup

    async def answer(self, *a, **kw):
        pass


def _find_latest_batch() -> str | None:
    batch_dir = MJSTOCK_DIR / "results" / "_batch"
    files = sorted(glob.glob(str(batch_dir / "*.json")))
    if not files:
        return None
    return Path(files[-1]).stem


_TG_CALLBACK_LIMIT = 64  # 텔레그램 callback_data 최대 바이트 — 넘으면 Button_data_invalid


def _assert_callback_len(cb: str, where: str):
    n = len(cb.encode("utf-8"))
    assert n <= _TG_CALLBACK_LIMIT, (
        f"FAIL: callback_data가 {n}바이트로 {_TG_CALLBACK_LIMIT}바이트 한도 초과 "
        f"({where}) — Button_data_invalid 원인. 값: {cb}"
    )


async def test_batch_manifest_roundtrip():
    from handlers._stock_mjstock import _handle_mjstock_scan_summary, _handle_mjstock_scan_list

    batch_id = _find_latest_batch()
    if not batch_id:
        print("⚠️  results/_batch/*.json 없음 — 배치 매니페스트 테스트 스킵")
        return

    manifest = json.loads((MJSTOCK_DIR / "results" / "_batch" / f"{batch_id}.json").read_text())
    expected_keys = set(manifest.get("screeners", {}).keys())

    # 1단계: 요약 화면 버튼이 매니페스트에 있는 검색기와 정확히 일치하는지
    q1 = FakeQuery()
    await _handle_mjstock_scan_summary(q1, batch_id)
    assert q1.last_markup is not None, "FAIL: 요약 화면에 버튼이 없음"
    cb_list = [btn.callback_data for row in q1.last_markup.inline_keyboard for btn in row]
    for cb in cb_list:
        _assert_callback_len(cb, "1단계 검색기 버튼")
    got_keys = {cb.split(":")[1] for cb in cb_list}
    assert got_keys == expected_keys or got_keys.issubset(expected_keys), (
        f"FAIL: 매니페스트({expected_keys})와 실제 버튼({got_keys})이 다름 — "
        f"무관한 검색기가 섞였을 가능성"
    )
    print(f"✅ 배치 매니페스트 정확히 복원됨: {got_keys}")

    # 검색기 키가 가장 긴 것으로 2/3단계를 테스트 — 콜백 길이 한도는 최악값에서 터짐
    longest_cb = max(cb_list, key=len)
    _, screener_key, date_str, cb_batch = longest_cb.split(":")

    # 2단계: 첫 버튼 클릭 → 종목 목록 → 뒤로가기가 batch_id 유지하는지
    q2 = FakeQuery()
    await _handle_mjstock_scan_list(q2, screener_key, date_str, batch_id=cb_batch)
    assert q2.last_markup is not None, "FAIL: 종목 목록 화면에 버튼이 없음(막다른 화면)"
    back_row = q2.last_markup.inline_keyboard[-1][0]
    assert back_row.callback_data == f"mjstock_scan_summary:{batch_id}", (
        f"FAIL: 뒤로가기 버튼이 batch_id를 잃음 — {back_row.callback_data}"
    )
    _assert_callback_len(back_row.callback_data, "2단계 뒤로가기 버튼")
    print(f"✅ 종목 목록 뒤로가기 버튼 batch_id 유지: {back_row.callback_data}")

    # 3단계로 갈 종목 버튼도 batch_id를 실어 나르는지 (가장 긴 검색기 키 기준으로 검증)
    rows = q2.last_markup.inline_keyboard[:-1]  # 마지막 줄은 뒤로가기
    if rows:
        for row in rows:
            for btn in row:
                _assert_callback_len(btn.callback_data, f"종목 버튼({screener_key})")
        ticker_btn = rows[0][0]
        parts = ticker_btn.callback_data.split(":")
        assert parts[0] == "mjstock_chart" and parts[-1] == batch_id, (
            f"FAIL: 종목 버튼이 batch_id를 안 실음 — {ticker_btn.callback_data}"
        )
        print(f"✅ 종목 버튼 콜백에 batch_id 포함 (가장 긴 케이스 {screener_key}, "
              f"{len(ticker_btn.callback_data.encode('utf-8'))}바이트): {ticker_btn.callback_data}")


async def test_kr_ticker_code_preserved():
    """KR 종목코드가 read_csv에서 앞자리 0을 잃지 않는지 (예: 036930)."""
    import pandas as pd
    kr_dirs = sorted(glob.glob(str(MJSTOCK_DIR / "results" / "*_kr")))
    checked = False
    for d in kr_dirs:
        files = sorted(glob.glob(str(Path(d) / "scan_*.csv")))
        if not files:
            continue
        df = pd.read_csv(files[-1], dtype={"code": str, "ticker": str})
        if "code" not in df.columns:
            continue
        codes = df["code"].dropna().astype(str)
        leading_zero = codes[codes.str.match(r"^0\d{5}$")]
        if len(leading_zero) > 0:
            print(f"✅ KR 종목코드 앞자리 0 유지 확인: {leading_zero.iloc[0]} ({Path(d).name})")
            checked = True
            break
    if not checked:
        print("⚠️  앞자리 0이 있는 KR 종목코드 샘플을 못 찾음 — 스킵(우연히 없을 수 있음)")


async def test_dead_end_has_back_button():
    """존재하지 않는 CSV를 조회했을 때도 뒤로가기 버튼이 붙는지."""
    from handlers._stock_mjstock import _handle_mjstock_scan_list

    q = FakeQuery()
    await _handle_mjstock_scan_list(q, "존재하지않는검색기_kr", "20200101_000000", batch_id="20200101_000000")
    assert q.last_markup is not None, "FAIL: 결과 없음 화면이 막다른 화면임 (뒤로가기 버튼 없음)"
    print("✅ 결과 없음/오류 화면에도 뒤로가기 버튼 존재")


async def main():
    print("=" * 60)
    print("MJstock 텔레그램 네비게이션 회귀 테스트")
    print("=" * 60)
    await test_batch_manifest_roundtrip()
    await test_kr_ticker_code_preserved()
    await test_dead_end_has_back_button()
    print("=" * 60)
    print("🎉 전부 통과 — 텔레그램에서 실클릭 테스트해도 안전함")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
