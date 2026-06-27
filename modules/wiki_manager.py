import os
import re
import unicodedata
import datetime
from pathlib import Path

class WikiManager:
    def __init__(self, vault_path="/Users/bluesea/Applications/Mjobsidian"):
        self.vault_path = unicodedata.normalize('NFC', vault_path)

    def get_graph_context(self):
        """Graphify 보고서를 읽어 지식 간의 연결 구조 파악"""
        report_path = os.path.join(self.vault_path, "graphify-out", "GRAPH_REPORT.md")
        if os.path.exists(report_path):
            with open(report_path, "r", encoding="utf-8") as f:
                return f"\n\n[🕸️ Knowledge Graph Context]\n{f.read()[:1000]}" # 핵심만
        return ""

    def read_wiki(self, rel_path):
        full_path = os.path.join(self.vault_path, "wiki", rel_path)
        if os.path.exists(full_path):
            with open(full_path, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def get_recent_context(self):
        """보관소 전체에서 최근 수정/업로드된 파일 목록(최신순 10개) 반환"""
        if not os.path.exists(self.vault_path):
            return "❌ 보관소 경로가 존재하지 않습니다."
        
        ignore_dirs = {".git", ".obsidian", ".stversions", ".trash", ".system_generated", "node_modules"}
        
        files = []
        for root, dirs, filenames in os.walk(self.vault_path):
            dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]
            for f in filenames:
                if f.startswith("."):
                    continue
                path = os.path.join(root, f)
                try:
                    mtime = os.path.getmtime(path)
                    files.append((path, mtime))
                except OSError:
                    continue
        
        if not files:
            return "🕒 최근 갱신된 파일이 없습니다."
            
        files.sort(key=lambda x: x[1], reverse=True)
        recent_files = files[:10]
        
        lines = ["🕒 **최근 업로드 및 갱신된 파일 목록 (최신순 10개)**\n"]
        for idx, (path, mtime) in enumerate(recent_files, 1):
            rel_p = os.path.relpath(path, self.vault_path)
            dt = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            lines.append(f"{idx}. 📄 `{os.path.basename(rel_p)}` (경로: `{os.path.dirname(rel_p) or '/'}`) - *{dt}*")
            
        return "\n".join(lines)

    def get_system_context(self):
        """참고용 메타 문서의 '내용'만 전달하며, 폴더 목록 오해를 방지함"""
        core_files = [
            "00_Meta/나의 비서 가이드.md",
            "00_Meta/guardrails.md",
            "00_Meta/스크립트 정보.md",
            "00_Meta/USER.md",
            "00_Meta/hot.md"
        ]
        
        context = "### [REFERENCE DOCUMENTS - CONTENT ONLY]\n"
        context += "⚠️ WARNING: 아래 리스트는 참고용 지침서의 내용일 뿐, 실제 폴더의 전체 파일 목록이 아닙니다.\n"
        
        for idx, rel_p in enumerate(core_files):
            content = self.read_wiki(rel_p)
            if content:
                context += f"\n---\n[[ SYSTEM GUIDELINE {idx+1} ]]\n{content[:1500]}\n"
        
        context += "\n\n[🚨 ABSOLUTE TRUTH RULE]\n"
        context += "1. 파일 목록 조회 시 위 문서들의 이름을 인용하는 것은 '환각(Hallucination)'입니다.\n"
        context += "2. 반드시 [LIST] 태그를 실행하여 얻은 결과만 '실제 파일 목록'으로 인정하십시오.\n"
        context += "3. 당신은 현재 이 문장들을 읽기 전까지 폴더 내에 어떤 파일이 있는지 전혀 모르는 상태라고 가정하십시오.\n"
        
        graph = self.get_graph_context()
        return f"{context}\n\n{graph}"

    # ================================================================
    # 🆕 write_wiki — 대화 결과를 wiki 페이지로 저장 (Karpathy 패턴)
    # ================================================================
    def write_wiki(self, rel_path: str, content: str, overwrite: bool = False) -> dict:
        """
        wiki/ 하위 경로에 마크다운 페이지를 저장한다.

        Parameters
        ----------
        rel_path : str
            wiki/ 기준 상대 경로. 예: "분석/주식전략_RDW분석.md"
        content  : str
            저장할 마크다운 본문 (헤더 포함)
        overwrite: bool
            True면 기존 파일 덮어쓰기, False면 기존 파일 있으면 에러 반환

        Returns
        -------
        dict: {"ok": bool, "path": str, "msg": str}
        """
        wiki_root = Path(self.vault_path) / "wiki"
        full_path = wiki_root / rel_path

        # 경로 탈출 방어 (../ 등)
        try:
            full_path.resolve().relative_to(wiki_root.resolve())
        except ValueError:
            return {"ok": False, "path": str(full_path), "msg": "❌ 경로 탈출 시도 차단됨"}

        if full_path.exists() and not overwrite:
            return {
                "ok": False,
                "path": str(full_path),
                "msg": f"⚠️ 파일이 이미 존재합니다. 덮어쓰려면 overwrite=True 전달:\n`{rel_path}`"
            }

        full_path.parent.mkdir(parents=True, exist_ok=True)

        # 타임스탬프 헤더 자동 삽입 (이미 있으면 스킵)
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        if "generated:" not in content[:200].lower() and "생성일시:" not in content[:200]:
            stamp = f"<!-- generated: {now_str} | source: /save_wiki -->\n"
            content = stamp + content

        # atomic write (tmp → rename)
        tmp_path = full_path.with_suffix(".tmp")
        try:
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.rename(full_path)
        except Exception as e:
            tmp_path.unlink(missing_ok=True)
            return {"ok": False, "path": str(full_path), "msg": f"❌ 저장 실패: {e}"}

        # index.md 자동 갱신
        self._update_index(rel_path, content)

        size_kb = len(content.encode("utf-8")) / 1024
        return {
            "ok": True,
            "path": str(full_path),
            "msg": f"✅ 저장 완료: `wiki/{rel_path}` ({size_kb:.1f}KB)"
        }

    def _update_index(self, rel_path: str, content: str):
        """
        wiki/index.md 에 새 페이지 항목을 추가한다.
        이미 등록된 경로면 스킵.
        """
        index_path = Path(self.vault_path) / "wiki" / "index.md"
        entry_link = f"[[{Path(rel_path).stem}]]"

        # 첫 줄 H1을 요약으로 사용
        first_heading = ""
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("# "):
                first_heading = line[2:].strip()
                break
        summary = first_heading[:60] if first_heading else Path(rel_path).stem

        # index.md 없으면 생성
        if not index_path.exists():
            index_path.write_text(
                "# Wiki Index\n\n| 페이지 | 요약 | 등록일 |\n|---|---|---|\n",
                encoding="utf-8"
            )

        existing = index_path.read_text(encoding="utf-8")
        if rel_path in existing or entry_link in existing:
            return  # 이미 등록됨

        now_str = datetime.datetime.now().strftime("%Y-%m-%d")
        new_row = f"| {entry_link} | {summary} | {now_str} |\n"

        # 테이블 마지막 줄 뒤에 삽입
        index_path.write_text(existing.rstrip() + "\n" + new_row, encoding="utf-8")

    # ================================================================
    # 🆕 lint_wiki — 고아 페이지 / 오래된 페이지 / 깨진 링크 탐지
    # ================================================================
    def lint_wiki(self, stale_days: int = 30) -> dict:
        """
        wiki/ 디렉터리 건강 상태 점검.

        점검 항목
        ---------
        1. 고아 페이지  — 어느 파일에서도 링크되지 않는 페이지
        2. 오래된 페이지 — stale_days일 이상 수정되지 않은 페이지
        3. 깨진 내부 링크 — [[링크]] 형식인데 대상 파일이 없는 경우
        4. 빈 페이지     — 본문 50자 미만

        Returns
        -------
        dict: {
            "orphans": [...],
            "stale": [...],
            "broken_links": [...],
            "empty": [...],
            "summary": str   ← 텔레그램용 요약 텍스트
        }
        """
        wiki_root = Path(self.vault_path) / "wiki"
        if not wiki_root.exists():
            return {"summary": "❌ wiki/ 디렉터리 없음"}

        # 전체 .md 파일 수집
        all_files = {
            p: p.read_text(encoding="utf-8", errors="replace")
            for p in wiki_root.rglob("*.md")
            if not any(part.startswith(".") for part in p.parts)
        }
        if not all_files:
            return {"summary": "📭 wiki/ 에 마크다운 파일 없음"}

        # 파일명 → 경로 매핑 (대소문자 무시)
        stem_map: dict[str, Path] = {p.stem.lower(): p for p in all_files}

        # 1. 모든 파일에서 [[링크]] 추출
        link_pattern = re.compile(r'\[\[([^\]|#]+?)(?:\|[^\]]+)?\]\]')
        all_linked_stems: set[str] = set()
        broken_links: list[str] = []

        for src_path, content in all_files.items():
            for match in link_pattern.finditer(content):
                target = match.group(1).strip()
                target_stem = Path(target).stem.lower()
                all_linked_stems.add(target_stem)
                if target_stem not in stem_map:
                    rel_src = src_path.relative_to(wiki_root)
                    broken_links.append(f"{rel_src} → [[{target}]]")

        # 2. 고아 페이지 (index.md / 00_Meta 제외)
        orphans: list[str] = []
        for p in all_files:
            if p.name in ("index.md",) or "00_Meta" in str(p):
                continue
            if p.stem.lower() not in all_linked_stems:
                orphans.append(str(p.relative_to(wiki_root)))

        # 3. 오래된 페이지
        cutoff = datetime.datetime.now() - datetime.timedelta(days=stale_days)
        stale: list[str] = []
        for p in all_files:
            mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime)
            if mtime < cutoff:
                days_ago = (datetime.datetime.now() - mtime).days
                stale.append(f"{p.relative_to(wiki_root)} ({days_ago}일 전)")

        # 4. 빈 페이지 (50자 미만)
        empty: list[str] = []
        for p, content in all_files.items():
            body = re.sub(r'^<!--.*?-->', '', content, flags=re.DOTALL).strip()
            if len(body) < 50:
                empty.append(str(p.relative_to(wiki_root)))

        # 요약 텍스트 (텔레그램용)
        total = len(all_files)
        lines = [
            f"🔬 *Wiki Lint 결과* (전체 {total}개 파일)\n",
            f"🔴 고아 페이지: {len(orphans)}개",
            f"🟡 오래된 페이지 ({stale_days}일+): {len(stale)}개",
            f"🟠 깨진 내부 링크: {len(broken_links)}개",
            f"⚪ 빈 페이지: {len(empty)}개",
        ]

        if orphans:
            lines.append("\n*고아 페이지 (상위 5개)*")
            lines.extend(f"  • `{o}`" for o in orphans[:5])
        if broken_links:
            lines.append("\n*깨진 링크 (상위 5개)*")
            lines.extend(f"  • `{b}`" for b in broken_links[:5])
        if stale:
            lines.append("\n*오래된 페이지 (상위 5개)*")
            lines.extend(f"  • `{s}`" for s in stale[:5])

        if not orphans and not broken_links and not empty:
            lines.append("\n✅ 심각한 이슈 없음")

        return {
            "orphans": orphans,
            "stale": stale,
            "broken_links": broken_links,
            "empty": empty,
            "summary": "\n".join(lines),
        }
