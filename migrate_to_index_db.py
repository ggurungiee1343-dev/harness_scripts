"""
migrate_to_index_db.py — 통합 인덱스 DB 마이그레이션 스크립트 v1.0
====================================================================
기존 6개 JSON 파일의 데이터를 hermes_index.db로 이관합니다.
파일이 존재하지 않으면 해당 테이블은 스킵합니다.

사용법:
    python migrate_to_index_db.py          # 마이그레이션 실행
    python migrate_to_index_db.py --dry-run # 어떤 파일이 있을지만 확인
"""

import sqlite3
import json
import os
import sys
import logging
from pathlib import Path
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='[migrate] %(levelname)s: %(message)s')
logger = logging.getLogger('migrate')

# ── DB 경로 ──
HERMES_RUNTIME = Path.home() / '.hermes' / 'runtime'
DB_PATH = HERMES_RUNTIME / 'hermes_index.db'

# ── JSON 파일 검색 경로 ──
SEARCH_PATHS = [
    Path.home() / '.hermes',
    Path.home() / 'Applications' / 'Mjauto' / 'Scripts',
]

# ── JSON 파일명 → 테이블명 매핑 ──
JSON_TABLE_MAP = {
    'system_ontology.json':   'ontology',
    'memory_index.json':      'intents',     # memory_index → intents 테이블에 저장
    'tag_registry.json':      'tags',
    'tag_index.json':         'tags',
    'paper_registry.json':    'papers',
    'intent_patterns.json':   'intents',
}


def get_connection() -> sqlite3.Connection:
    """DB 연결 생성 (테이블이 없으면 생성)"""
    HERMES_RUNTIME.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def create_schema(conn: sqlite3.Connection):
    """테이블이 없으면 생성"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tag TEXT UNIQUE NOT NULL,
            category TEXT DEFAULT 'general',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS papers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            arxiv_id TEXT UNIQUE,
            authors TEXT,
            abstract TEXT,
            url TEXT,
            tags TEXT,
            bundle_id INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS intents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT UNIQUE NOT NULL,
            intent TEXT NOT NULL,
            weight REAL DEFAULT 1.0
        );

        CREATE TABLE IF NOT EXISTS ontology (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            component TEXT UNIQUE NOT NULL,
            status TEXT DEFAULT 'unknown',
            last_checked TEXT,
            notes TEXT
        );
    """)
    conn.commit()


def find_json_files() -> dict:
    """검색 경로에서 JSON 파일을 찾아 {파일명: 절대경로} 반환"""
    found = {}
    for name in JSON_TABLE_MAP:
        for search_path in SEARCH_PATHS:
            full_path = search_path / name
            if full_path.exists():
                found[name] = full_path
                break
    return found


def migrate_ontology(conn: sqlite3.Connection, file_path: Path) -> int:
    """ontology.json → ontology 테이블"""
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    rows = 0
    if isinstance(data, list):
        for item in data:
            component = item.get('component') or item.get('name')
            if not component:
                continue
            status = item.get('status', 'unknown')
            notes = item.get('notes', '')
            conn.execute(
                """INSERT OR REPLACE INTO ontology (component, status, last_checked, notes)
                   VALUES (?, ?, datetime('now'), ?)""",
                (component, status, notes)
            )
            rows += 1
    elif isinstance(data, dict):
        for component, info in data.items():
            status = info.get('status', 'unknown') if isinstance(info, dict) else 'unknown'
            notes = str(info) if not isinstance(info, dict) else info.get('notes', '')
            conn.execute(
                """INSERT OR REPLACE INTO ontology (component, status, last_checked, notes)
                   VALUES (?, ?, datetime('now'), ?)""",
                (component, status, notes)
            )
            rows += 1
    conn.commit()
    return rows


def migrate_memory_index(conn: sqlite3.Connection, file_path: Path) -> int:
    """memory_index.json → intents 테이블"""
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    rows = 0
    if isinstance(data, list):
        for item in data:
            keyword = item.get('keyword') or item.get('tag') or item.get('name')
            intent = item.get('intent') or item.get('category', 'general_chat')
            weight = item.get('weight', 1.0)
            if keyword:
                conn.execute(
                    "INSERT OR IGNORE INTO intents (keyword, intent, weight) VALUES (?, ?, ?)",
                    (keyword, intent, weight)
                )
                rows += 1
    elif isinstance(data, dict):
        for keyword, info in data.items():
            intent = info.get('intent', 'general_chat') if isinstance(info, dict) else str(info)
            weight = info.get('weight', 1.0) if isinstance(info, dict) else 1.0
            conn.execute(
                "INSERT OR IGNORE INTO intents (keyword, intent, weight) VALUES (?, ?, ?)",
                (keyword, intent, weight)
            )
            rows += 1
    conn.commit()
    return rows


def migrate_tags(conn: sqlite3.Connection, file_path: Path) -> int:
    """tag_registry.json / tag_index.json → tags 테이블"""
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    rows = 0
    if isinstance(data, list):
        for item in data:
            tag = item.get('tag') or item.get('name')
            category = item.get('category', 'general')
            if tag:
                conn.execute(
                    """INSERT OR IGNORE INTO tags (tag, category, created_at, updated_at)
                       VALUES (?, ?, datetime('now'), datetime('now'))""",
                    (tag, category)
                )
                rows += 1
    elif isinstance(data, dict):
        for tag, info in data.items():
            category = info.get('category', 'general') if isinstance(info, dict) else 'general'
            conn.execute(
                """INSERT OR IGNORE INTO tags (tag, category, created_at, updated_at)
                   VALUES (?, ?, datetime('now'), datetime('now'))""",
                (tag, category)
            )
            rows += 1
    conn.commit()
    return rows


def migrate_papers(conn: sqlite3.Connection, file_path: Path) -> int:
    """paper_registry.json → papers 테이블"""
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    rows = 0
    if isinstance(data, list):
        for item in data:
            title = item.get('title', 'Untitled')
            arxiv_id = item.get('arxiv_id') or item.get('id')
            authors = item.get('authors', '')
            abstract = item.get('abstract', '')
            url = item.get('url', '')
            tags = item.get('tags', '')
            bundle_id = item.get('bundle_id')
            if arxiv_id:
                conn.execute(
                    """INSERT OR IGNORE INTO papers (title, arxiv_id, authors, abstract, url, tags, bundle_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (title, arxiv_id, authors, abstract, url, tags, bundle_id)
                )
                rows += 1
    elif isinstance(data, dict):
        for arxiv_id, info in data.items():
            title = info.get('title', 'Untitled') if isinstance(info, dict) else str(info)
            authors = info.get('authors', '') if isinstance(info, dict) else ''
            abstract = info.get('abstract', '') if isinstance(info, dict) else ''
            url = info.get('url', '') if isinstance(info, dict) else ''
            tags = info.get('tags', '') if isinstance(info, dict) else ''
            bundle_id = info.get('bundle_id') if isinstance(info, dict) else None
            conn.execute(
                """INSERT OR IGNORE INTO papers (title, arxiv_id, authors, abstract, url, tags, bundle_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (title, arxiv_id, authors, abstract, url, tags, bundle_id)
            )
            rows += 1
    conn.commit()
    return rows


# ── 마이그레이션 디스패치 ──
MIGRATORS = {
    'system_ontology.json':   ('ontology', migrate_ontology),
    'memory_index.json':      ('intents', migrate_memory_index),
    'tag_registry.json':      ('tags', migrate_tags),
    'tag_index.json':         ('tags', migrate_tags),
    'paper_registry.json':    ('papers', migrate_papers),
    'intent_patterns.json':   ('intents', migrate_memory_index),
}


def main():
    dry_run = '--dry-run' in sys.argv

    conn = get_connection()
    create_schema(conn)

    found_files = find_json_files()

    if not found_files:
        logger.info("마이그레이션할 JSON 파일이 없습니다. DB 스키마만 생성했습니다.")
        conn.close()
        return

    logger.info(f"발견된 JSON 파일: {len(found_files)}개")
    for name, path in found_files.items():
        logger.info(f"  📄 {name} → {path}")

    if dry_run:
        logger.info("✅ Dry-run 완료. 실제 마이그레이션을 실행하려면 --dry-run 없이 실행하세요.")
        conn.close()
        return

    total_rows = 0
    legacy_dir = Path.home() / '.hermes' / 'runtime' / 'migrated_legacy'
    legacy_dir.mkdir(parents=True, exist_ok=True)

    for name, path in found_files.items():
        table_name, migrator = MIGRATORS.get(name, (None, None))
        if not migrator:
            logger.warning(f"  ⚠️ {name}: 마이그레이터 없음 (스킵)")
            continue

        try:
            rows = migrator(conn, path)
            logger.info(f"  ✅ {name} → {table_name}: {rows}개 행 이관 완료")
            total_rows += rows

            # legacy로 이동
            dest = legacy_dir / name
            os.rename(str(path), str(dest))
            logger.info(f"     원본 → {dest} (백업)")
        except Exception as e:
            logger.error(f"  ❌ {name} 마이그레이션 실패: {e}")

    conn.close()
    logger.info(f"🎉 마이그레이션 완료: 총 {total_rows}개 행, {len(found_files)}개 파일 처리됨")
    logger.info(f"   DB 위치: {DB_PATH}")


if __name__ == '__main__':
    main()
