#!/usr/bin/env python3
# scripts/migrate_json_to_db.py
# 목적: memory_index.json → hermes_index.db (memories 테이블) 이전
# 실행: python scripts/migrate_json_to_db.py

import json
import os
import sqlite3


JSON_PATH = os.path.expanduser("~/.hermes/memory_index.json")
DB_PATH   = os.path.expanduser("~/.hermes/runtime/hermes_index.db")


def migrate_memory_index():
    if not os.path.exists(JSON_PATH):
        print(f"[SKIP] {JSON_PATH} 파일 없음 — 마이그레이션 불필요")
        return

    with open(JSON_PATH, "r", encoding="utf-8") as f:
        memories = json.load(f)

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id        TEXT PRIMARY KEY,
            content   TEXT,
            timestamp TEXT
        )
    """)

    inserted = 0
    for mem in memories:
        conn.execute(
            "INSERT OR REPLACE INTO memories VALUES (?, ?, ?)",
            (
                mem.get("id", ""),
                mem.get("content", ""),
                mem.get("timestamp", "")
            )
        )
        inserted += 1

    conn.commit()
    conn.close()

    # 원본 백업 후 삭제 (선택 — 안전을 위해 기본 백업만)
    backup_path = JSON_PATH + ".bak"
    os.rename(JSON_PATH, backup_path)
    print(f"[OK] {inserted}개 memory 마이그레이션 완료")
    print(f"     원본 백업: {backup_path}")
    print(f"     DB 경로:   {DB_PATH}")


if __name__ == "__main__":
    migrate_memory_index()
