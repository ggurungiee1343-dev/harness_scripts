"""wiki_index.db 상태 진단"""
import sqlite3
from pathlib import Path

db = Path.home() / '.hermes' / 'runtime' / 'wiki_index.db'
print(f'=== wiki_index.db 상태 ===')
print(f'파일 존재: {db.exists()}')
if db.exists():
    print(f'파일 크기: {db.stat().st_size} bytes')
    conn = sqlite3.connect(str(db))
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cur.fetchall()
    print(f'테이블: {[t[0] for t in tables]}')
    for t in tables:
        try:
            cnt = cur.execute(f'SELECT COUNT(*) FROM {t[0]}').fetchone()[0]
            print(f'  {t[0]}: {cnt} rows')
        except:
            pass
    print()
    print('=== chunks 샘플 ===')
    for row in cur.execute('SELECT doc_path, chunk_idx, heading, substr(content,1,80) FROM chunks LIMIT 3'):
        print(f'  doc_path={row[0]} | chunk#{row[1]} | heading={row[2]} | content={row[3]}...')
    print()
    print('=== FTS5 MATCH 테스트 ===')
    for q in ['헌법', '연구', '법률', 'AI']:
        try:
            rows = cur.execute('SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH ?', (q,)).fetchone()[0]
            print(f'  FTS5 MATCH "{q}": {rows} hits')
        except Exception as e:
            print(f'  FTS5 MATCH "{q}": ERROR {e}')
    print()
    print('=== search_hybrid 직접 호출 테스트 ===')
    conn.close()
else:
    print('DB 없음 - 인덱싱 필요')
