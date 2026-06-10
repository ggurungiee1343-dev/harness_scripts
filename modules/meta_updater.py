#!/usr/bin/env python3
"""
meta_updater.py
────────────────────────────────────
* 메타 폴더 MD 파일 사용 정의·업데이트 가이드를 자동 생성.
* [2026-05-31] 박사님의 요청으로 현재 기능은 비활성화(정지) 상태입니다.
  나중에 필요 시 하단의 주석을 해제하여 다시 살릴 수 있습니다.
"""

import os
from pathlib import Path

def generate_usage_guide() -> None:
    guide_path = Path.cwd() / "Meta 폴더 파일 저장 방법.md"
    guide_path.write_text(
        "# 📚 메타 폴더 파일 저장 방법\n\n"
        "## 1️⃣ 파일 위치·역할 정의\n"
        "- `스크립트 정보.md` : 전체 시스템 스크립트 구조와 위치\n"
        "- `HERMES3_ENCYCLOPEDIA.md` : 기능 백과사전\n"
        "- `Meta 폴더 파일 저장 방법.md` (본 파일) : 메타 폴더 내 MD 파일 사용·업데이트 가이드\n\n"
        "## 2️⃣ 파일 업데이트 흐름\n"
        "1. 에이전트에게 메타 폴더 파일 업데이트 지시\n"
        "2. 에이전트가 본 가이드를 읽고 규정에 따라 수정/삭제 진행\n"
        "\n> *최종 업데이트: 2026‑05‑31*"
    )
    print(f"✅ {guide_path} 생성 완료")

if __name__ == "__main__":
    print("⚠️ meta_updater.py 는 현재 비활성화(정지) 상태입니다.")
    print("기능을 되살리려면 스크립트 하단의 주석을 해제해주세요.")
    
    # --- 아래 코드를 주석 해제하면 기능이 다시 작동합니다 ---
    # generate_usage_guide()
