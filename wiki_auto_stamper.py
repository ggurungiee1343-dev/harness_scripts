#!/usr/bin/env python3
"""
wiki_auto_stamper.py — Hermes Wiki 자동 타임스탬프 유지 데몬
─────────────────────────────────────────────────────────────
용도: wiki 이하 .md 파일이 수정될 때 하단의 '최종 업데이트' 타임스탬프를
      자동으로 갱신한다. fswatch 또는 cron과 연계하여 사용.

사용법 1 (단발 실행 - 특정 파일):
    python3 wiki_auto_stamper.py /path/to/file.md

사용법 2 (전체 wiki 스캔 모드 - 누락된 파일만 처리):
    python3 wiki_auto_stamper.py --scan

사용법 3 (fswatch 연동 - stdin에서 파일 경로 읽기):
    fswatch -0 /Users/bluesea/Applications/Mjobsidian/wiki | python3 wiki_auto_stamper.py --watch

작성일: 2026-06-03
작성자: Hermes 시스템 아키텍트 (Antigravity AI)
"""

import sys
import re
import os
import yaml
from pathlib import Path
from datetime import datetime

WIKI_ROOT = Path('/Users/bluesea/Applications/Mjobsidian/wiki')
TS_RE = re.compile(r'(\*최종\s*업데이트:?\s*)[\d\-: ]+([^\n]*)', re.IGNORECASE)
SKIP_PATTERNS = ['99_Archive/Backup_', '.obsidian', '_fswatch_test_']

def should_skip(p: Path) -> bool:
    s = str(p)
    return any(pat in s for pat in SKIP_PATTERNS)

def clean_wiki_links(text: str) -> str:
    """본문의 [[링크]]를 대괄호 없는 단순 텍스트로 풀어줌. 이미지 ![[이미지]]는 유지."""
    def repl(match):
        full = match.group(0)
        if full.startswith('!'):
            return full
        link_content = match.group(2)
        display_text = match.group(3)
        return display_text if display_text else link_content
    return re.sub(r'(!)?\[\[([^\]|]+)(?:\|([^\]]+))?\]\]', repl, text)

def process_frontmatter_tags(content: str) -> str:
    """YAML frontmatter의 tags를 본문 해시태그와 병합하여 최대 8개로 가동"""
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    
    existing_fm = {}
    remaining_content = content
    has_fm = False
    
    if fm_match:
        has_fm = True
        fm_text = fm_match.group(1)
        remaining_content = content[fm_match.end():]
        try:
            existing_fm = yaml.safe_load(fm_text) or {}
        except Exception:
            existing_fm = {}
            
    # 본문에서 #태그 파싱
    body_tags = set()
    for m in re.finditer(r'(?:^|\s)(#[A-Za-z가-힣][A-Za-z가-힣0-9_\-/]*)(?=\s|$|[.,;:!?])', remaining_content):
        tag = m.group(1).strip()
        if not tag.startswith("##") and not tag.startswith("# "):
            body_tags.add(tag.lstrip('#'))
            
    # Frontmatter 태그 파싱
    fm_tags = existing_fm.get('tags', [])
    if isinstance(fm_tags, str):
        fm_tags = [t.strip() for t in fm_tags.split(',')]
    elif not isinstance(fm_tags, list):
        fm_tags = []
        
    cleaned_fm_tags = []
    for t in fm_tags:
        if isinstance(t, str):
            cleaned_fm_tags.append(t.lstrip('#').strip())
            
    merged_tags = sorted(list(set(cleaned_fm_tags) | body_tags))[:8]
    existing_fm['tags'] = merged_tags
    
    try:
        new_fm_text = yaml.safe_dump(existing_fm, allow_unicode=True, default_flow_style=False).strip()
    except Exception:
        tags_str = ", ".join(merged_tags)
        new_fm_text = f"tags: [{tags_str}]"
        
    return f"---\n{new_fm_text}\n---\n{remaining_content}"

def stamp_file(p: Path, reason: str = '') -> bool:
    """파일 내용 정돈(링크/태그) 및 타임스탬프 갱신."""
    if not p.exists() or p.suffix != '.md':
        return False
    if should_skip(p):
        return False

    try:
        content = p.read_text(encoding='utf-8')
    except Exception as e:
        print(f'[AUTO-STAMP] ❌ {p.name} 읽기 실패: {e}')
        return False
    
    # 1. [[링크]] 텍스트화 정돈
    content = clean_wiki_links(content)
    
    # 2. 태그 자동화 (최대 8개 Frontmatter 처리)
    content = process_frontmatter_tags(content)

    # 3. 타임스탬프 처리
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    tag = f'*최종 업데이트: {now}{(" — " + reason) if reason else ""}*'

    if TS_RE.search(content):
        new_content = TS_RE.sub(tag, content)
    else:
        new_content = content.rstrip('\n') + f'\n\n---\n{tag}\n'

    if new_content != content:
        p.write_text(new_content, encoding='utf-8')
        print(f'[AUTO-STAMP] ✅ {p.name}  → {now} (링크정리, 태그최대8개 동기화)')
        return True
    return False

def scan_missing():
    """wiki 이하 타임스탬프 없는 파일 전수 처리"""
    count = 0
    for p in WIKI_ROOT.rglob('*.md'):
        if should_skip(p):
            continue
        content = p.read_text(encoding='utf-8', errors='ignore')
        if not TS_RE.search(content) and '최종 작성일' not in content:
            stamp_file(p, '누락 타임스탬프 자동 복구')
            count += 1
    print(f'[SCAN] 총 {count}개 파일에 타임스탬프 삽입 완료')

def watch_mode():
    """stdin(null-separated)에서 파일 경로를 읽어 순서대로 처리"""
    import select
    print('[WATCH] 파일 변경 감지 대기 중...')
    buf = b''
    while True:
        chunk = sys.stdin.buffer.read(4096)
        if not chunk:
            break
        buf += chunk
        while b'\x00' in buf:
            path_b, buf = buf.split(b'\x00', 1)
            p = Path(path_b.decode('utf-8', errors='ignore'))
            stamp_file(p)

if __name__ == '__main__':
    args = sys.argv[1:]
    if '--scan' in args:
        scan_missing()
    elif '--watch' in args:
        watch_mode()
    elif args:
        for a in args:
            stamp_file(Path(a))
    else:
        print(__doc__)
