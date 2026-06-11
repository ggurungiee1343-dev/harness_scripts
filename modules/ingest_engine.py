import os
import shutil
import json
import re
from pathlib import Path
from datetime import datetime
from tag_linker import TagLinker
from modules import ingest_text_utils as _itu
from index_db import add_paper

class IngestEngine:
    def __init__(self, source_dir, dest_dir, llm_func=None):
        self.source_dir = Path(source_dir)
        self.dest_dir = Path(dest_dir)
        self.llm_func = llm_func
        # Inbox deferral: 저신뢰(Unsorted/JSON 파싱 실패) 파일 보관소
        self.inbox_dir = self.source_dir / "Inbox"
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        # 태그 링커: 문서 처리 후 DB에 태그 동기화
        self.linker = TagLinker()  # 기본 DB_PATH(~/.hermes/runtime/hermes_index.db) 사용

    def _index_research_paper(self, target_path: Path, category: str):
        """Research 파일을 papers 테이블에 인덱싱"""
        if category != "20_Research":
            return
        try:
            text = target_path.read_text(encoding="utf-8", errors="replace")
            # frontmatter에서 제목 추출
            title = target_path.stem  # 기본값: 파일명
            authors = ""
            tags = ""
            abstract = ""
            if text.startswith("---"):
                parts = text.split("---", 2)
                if len(parts) >= 3:
                    fm = parts[1]
                    for line in fm.strip().split("\n"):
                        if ":" in line:
                            key, val = line.split(":", 1)
                            key = key.strip().lower()
                            val = val.strip()
                            if key == "title":
                                title = val
                            elif key == "author":
                                authors = val
                            elif key in ("tags", "tag"):
                                tags = val
                            elif key == "abstract":
                                abstract = val
            res = add_paper(title=title.strip('"\' '), authors=authors.strip('"\''),
                           tags=tags.strip('"\''), abstract=abstract.strip('"\''))
            if res:
                print(f"📄 [PaperIndex] 논문 인덱싱 완료: {title[:50]} (id={res})")
        except Exception as e:
            print(f"⚠️ [PaperIndex] 인덱싱 실패: {e}")

    # 허용된 카테고리 목록 (폴더명 오염 방지)
    ALLOWED_CATEGORIES = {"10_AI_Automation", "20_Research", "30_Journal", "40_Thesis", "50_Invest", "Unsorted"}

    # 루트 vault 경로 (루트 방치 파일 스캔용)
    ROOT_VAULT = Path.home() / "Applications" / "Mjobsidian"

    # ── 순수 함수 유틸 (modules/ingest_text_utils.py로 분할, 2026-06-11) ──
    # 기존 내부/외부 호출(self._xxx) 호환을 위한 staticmethod 별칭
    _parse_frontmatter       = staticmethod(_itu.parse_frontmatter)
    _read_file_content       = staticmethod(_itu.read_file_content)
    _merge_tags              = staticmethod(_itu.merge_tags)
    _update_frontmatter      = staticmethod(_itu.update_frontmatter)
    _build_classify_prompt   = staticmethod(_itu.build_classify_prompt)
    _parse_json_response     = staticmethod(_itu.parse_json_response)
    _parse_json_array        = staticmethod(_itu.parse_json_array)
    _build_interrogate_prompt = staticmethod(_itu.build_interrogate_prompt)
    _build_brief_prompt      = staticmethod(_itu.build_brief_prompt)


    # ------------------------------------------------------------------ #
    #  Main entry point — handles Clippings + Root files in one call
    # ------------------------------------------------------------------ #
    async def process_all(self, interrogate: bool = False):
        """텔레그램 ingest 버튼 진입점: Clippings 분류 이동 + 루트 파일 분류 이동 + Inbox 재분류"""
        parts = []
        # 1. Clippings 처리
        parts.append(await self._process_clippings(interrogate=interrogate))
        # 2. 루트 방치 파일 처리
        parts.append(await self._process_root_files())
        # 3. Inbox 재분류
        parts.append(await self._process_inbox(interrogate=interrogate))
        return "\n\n".join(p for p in parts if p)

    async def scan_root_only(self):
        """/ingest scan 전용: 루트 방치 파일에 태그+요약만 추가 (폴더 이동 없음, mtime 보존)"""
        root = self.ROOT_VAULT
        if not root.exists():
            return ""

        files = [f for f in root.iterdir()
                 if f.is_file() and f.suffix == ".md" and not f.name.startswith(".")]

        if not files:
            return "✅ 루트에 방치된 .md 파일이 없습니다. 모두 정리된 상태입니다."

        report = f"📂 **루트 파일 스캔 ({len(files)}개 파일)**\n"

        for f in files:
            try:
                raw_text = f.read_text(encoding="utf-8")
                content = raw_text[:2000]

                description = ""
                keywords = []

                if self.llm_func:
                    prompt = self._build_classify_prompt(content)
                    res = await self.llm_func(prompt)
                    parsed = self._parse_json_response(res)
                    if parsed:
                        description = parsed.get("description", "")
                        keywords = parsed.get("keywords", [])

                # 기존 frontmatter 병합 (이동 없이 태그+설명만 업데이트)
                existing_tags, existing_desc, _, _ = self._parse_frontmatter(raw_text)
                scan_tags = ["scanned"] + keywords
                merged_tags = self._merge_tags(existing_tags, scan_tags)
                final_desc = description or existing_desc

                updated_text = self._update_frontmatter(raw_text, merged_tags, final_desc)

                # 원본 mtime 보존 후 덮어쓰기
                orig_mtime = os.path.getmtime(f)
                f.write_text(updated_text, encoding="utf-8")
                os.utime(f, (orig_mtime, orig_mtime))

                title = parsed.get("title", f.stem) if parsed else f.stem
                report += f"- ✅ `{f.name}` → 태그/설명 업데이트 완료 (제자리)\n"
            except Exception as e:
                report += f"- ❌ `{f.name}` 처리 실패: {e}\n"

        return report

    async def process_gardening(self):
        """하위 호환성 유지 — process_all() 호출"""
        return await self.process_all()

    # ------------------------------------------------------------------ #
    #  Clippings 처리
    # ------------------------------------------------------------------ #
    async def _process_clippings(self, interrogate: bool = False):
        if not self.source_dir.exists():
            return f"❌ Clippings 소스 디렉토리가 없습니다: {self.source_dir}"

        files = [f for f in self.source_dir.iterdir() if f.is_file() and not f.name.startswith(".")]
        # Archive 내부 파일은 제외
        files = [f for f in files if "Archive" not in f.parts]

        if not files:
            return ""

        report = f"📂 **Clippings 정리 ({len(files)}개 파일)**\n"

        for f in files:
            try:
                raw_text = self._read_file_content(f)
                if not raw_text:
                    continue
                content = raw_text[:2000]

                category = "Unsorted"
                new_title = f.stem
                description = ""
                keywords = []
                questions = []
                brief = ""

                if self.llm_func:
                    prompt = self._build_classify_prompt(content)
                    res = await self.llm_func(prompt)
                    parsed = self._parse_json_response(res)
                    if parsed and "category" in parsed:
                        raw_cat = parsed["category"]
                        category = raw_cat if raw_cat in self.ALLOWED_CATEGORIES else "Unsorted"
                        new_title = parsed.get("title", f.stem)
                        description = parsed.get("description", "")
                        keywords = parsed.get("keywords", [])

                    # [Inbox Deferral] 저신뢰 분류 — Inbox로 보내고 skip
                    if category == "Unsorted" or not parsed:
                        pending_name = f"{f.stem}_pending.md"
                        pending_path = self.inbox_dir / pending_name
                        shutil.copy2(str(f), str(pending_path))
                        archive_dir = self.source_dir / "Archive"
                        archive_dir.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(f), str(archive_dir / f.name))
                        report += f"- 📥 `{f.name}` → `Inbox/{pending_name}` (저신뢰, 재분류 대기)\n"
                        continue

                    # [Feature 1] 사전 질문 생성 (interrogate mode)
                    if interrogate:
                        questions = await self.generate_questions(raw_text)

                    # [Feature 4] Diarization — 2줄 브리프 생성
                    brief = await self.generate_brief(raw_text)

                target_folder = self.dest_dir / category
                target_folder.mkdir(parents=True, exist_ok=True)
                target_path = target_folder / f"{new_title}.md"

                mtime = os.path.getmtime(f)
                mtime_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")

                # 기존 frontmatter 병합
                existing_tags, existing_desc, existing_questions, _ = self._parse_frontmatter(raw_text)
                ingest_tags = ["ingested", category] + keywords
                merged_tags = self._merge_tags(existing_tags, ingest_tags)
                final_desc = description or existing_desc
                merged_questions = questions or existing_questions
                merged_brief = brief

                # frontmatter 업데이트 (링크 비활성화되어 있음)
                updated_text = self._update_frontmatter(raw_text, merged_tags, final_desc, merged_questions, merged_brief)

                with open(target_path, "w", encoding="utf-8") as nf:
                    nf.write(updated_text)

                # 원본은 Archive로 백업
                archive_dir = self.source_dir / "Archive"
                archive_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(f), str(archive_dir / f.name))

                report += f"- ✅ `{f.name}` → `{category}/{new_title}.md` 완료\n"

                # 태그 DB 동기화
                try:
                    self.linker.sync_document(str(target_path))
                except Exception:
                    pass  # 태그 동기화 실패는 ingest 자체에 영향 없음

                # 논문 인덱싱 (20_Research 전용)
                self._index_research_paper(target_path, category)
            except Exception as e:
                report += f"- ❌ `{f.name}` 처리 실패: {e}\n"

        return report

    # ------------------------------------------------------------------ #
    #  루트 방치 파일 처리
    # ------------------------------------------------------------------ #
    async def _process_root_files(self):
        root = self.ROOT_VAULT
        if not root.exists():
            return ""

        # 루트의 .md/.pdf 파일 수집 (디렉토리 제외, dotfile 제외)
        files = [f for f in root.iterdir()
                 if f.is_file() and f.suffix.lower() in (".md", ".pdf") and not f.name.startswith(".")]

        if not files:
            return ""

        report = f"📂 **루트 파일 정리 ({len(files)}개 파일)**\n"

        for f in files:
            try:
                raw_text = self._read_file_content(f)
                if not raw_text:
                    continue
                content = raw_text[:2000]

                category = "Unsorted"
                new_title = f.stem
                description = ""
                keywords = []

                if self.llm_func:
                    prompt = self._build_classify_prompt(content)
                    res = await self.llm_func(prompt)
                    parsed = self._parse_json_response(res)
                    if parsed and "category" in parsed:
                        raw_cat = parsed["category"]
                        category = raw_cat if raw_cat in self.ALLOWED_CATEGORIES else "Unsorted"
                        new_title = parsed.get("title", f.stem)
                        description = parsed.get("description", "")
                        keywords = parsed.get("keywords", [])

                # [Inbox Deferral] 저신뢰 분류 — Inbox로 보내고 skip
                if category == "Unsorted" or not parsed:
                    pending_name = f"{f.stem}_pending.md"
                    pending_path = self.inbox_dir / pending_name
                    shutil.copy2(str(f), str(pending_path))
                    f.unlink()  # 루트 파일 제거 (원본도 남기지 않음)
                    report += f"- 📥 `{f.name}` → `Inbox/{pending_name}` (저신뢰, 재분류 대기)\n"
                    continue

                target_folder = self.dest_dir / category
                target_folder.mkdir(parents=True, exist_ok=True)
                target_path = target_folder / f"{new_title}.md"

                # 원본 mtime 보존
                orig_mtime = os.path.getmtime(f)

                # 기존 frontmatter 병합
                existing_tags, existing_desc, _, _ = self._parse_frontmatter(raw_text)
                scan_tags = ["scanned", category] + keywords
                merged_tags = self._merge_tags(existing_tags, scan_tags)
                final_desc = description or existing_desc

                # frontmatter 업데이트
                updated_text = self._update_frontmatter(raw_text, merged_tags, final_desc)

                # 원본에 업데이트된 내용 쓰기 → 대상으로 이동
                f.write_text(updated_text, encoding="utf-8")
                shutil.move(str(f), str(target_path))

                # 타임스탬프 복원
                os.utime(target_path, (orig_mtime, orig_mtime))

                report += f"- ✅ `{f.name}` → `{category}/{new_title}.md` 완료\n"

                # 태그 DB 동기화
                try:
                    self.linker.sync_document(str(target_path))
                except Exception:
                    pass  # 태그 동기화 실패는 ingest 자체에 영향 없음

                # 논문 인덱싱 (20_Research 전용)
                self._index_research_paper(target_path, category)
            except Exception as e:
                report += f"- ❌ `{f.name}` 처리 실패: {e}\n"

        return report

    # ------------------------------------------------------------------ #
    #  Inbox 재분류 — 이전 deferral 파일 재처리
    # ------------------------------------------------------------------ #
    async def _process_inbox(self, interrogate: bool = False):
        """Inbox에 쌓인 _pending 파일 재분류 시도"""
        if not self.inbox_dir.exists():
            return ""

        pending_files = [f for f in self.inbox_dir.iterdir()
                         if f.is_file() and f.suffix == ".md"
                         and f.stem.endswith("_pending")]

        if not pending_files:
            return ""

        if not self.llm_func:
            return f"- ⏸️ Inbox에 {len(pending_files)}개 대기 중 (llm_func 없음, 재분류 불가)"

        report = f"📂 **Inbox 재분류 ({len(pending_files)}개 파일)**\n"

        for f in pending_files:
            try:
                raw_text = self._read_file_content(f)
                if not raw_text:
                    continue
                content = raw_text[:2000]

                category = "Unsorted"
                new_title = f.stem.replace("_pending", "")
                description = ""
                keywords = []

                prompt = self._build_classify_prompt(content)
                res = await self.llm_func(prompt)
                parsed = self._parse_json_response(res)

                if parsed and "category" in parsed:
                    raw_cat = parsed["category"]
                    category = raw_cat if raw_cat in self.ALLOWED_CATEGORIES else "Unsorted"

                # 2차 시도에서도 Unsorted면 Inbox 잔류 (재분류 실패 명시)
                if category == "Unsorted" or not parsed:
                    # _pending 접미사 유지 — 다음 라운드에서 재시도
                    report += f"- ⏸️ `{f.name}` → 재분류 실패, Inbox 잔류 (다음 라운드 재시도)\n"
                    continue

                # 성공: 정식 분류 경로로 이동
                new_title = parsed.get("title", new_title)
                description = parsed.get("description", "")
                keywords = parsed.get("keywords", [])

                questions = []
                brief = ""
                if interrogate:
                    questions = await self.generate_questions(raw_text)
                brief = await self.generate_brief(raw_text)

                target_folder = self.dest_dir / category
                target_folder.mkdir(parents=True, exist_ok=True)
                target_path = target_folder / f"{new_title}.md"

                existing_tags, existing_desc, existing_questions, _ = self._parse_frontmatter(raw_text)
                ingest_tags = ["ingested", category] + keywords
                merged_tags = self._merge_tags(existing_tags, ingest_tags)
                final_desc = description or existing_desc
                merged_questions = questions or existing_questions

                updated_text = self._update_frontmatter(
                    raw_text, merged_tags, final_desc, merged_questions, brief
                )

                with open(target_path, "w", encoding="utf-8") as nf:
                    nf.write(updated_text)

                f.unlink()  # Inbox에서 제거
                report += f"- ✅ `{f.name}` → `{category}/{new_title}.md` 재분류 완료\n"

                try:
                    self.linker.sync_document(str(target_path))
                except Exception:
                    pass
                self._index_research_paper(target_path, category)

            except Exception as e:
                report += f"- ❌ `{f.name}` 재분류 실패: {e}\n"

        return report

    # ------------------------------------------------------------------ #
    #  JSON 응답 파서 (Gemma4 대응: ```json 블록, 불완전 JSON 등)
    # ------------------------------------------------------------------ #
    # ------------------------------------------------------------------ #
    #  Feature 1: 사전 질문 생성 (Interrogate)
    # ------------------------------------------------------------------ #
    async def generate_questions(self, content: str) -> list[str]:
        """문서 내용에서 사전 질문 생성 — LLM 호출 후 JSON 파싱"""
        if not self.llm_func or not content.strip():
            return []
        try:
            prompt = self._build_interrogate_prompt(content[:1500])
            res = await self.llm_func(prompt)
            parsed = self._parse_json_array(res)
            if parsed and isinstance(parsed, list):
                return [str(q).strip() for q in parsed if str(q).strip()][:5]
        except Exception as e:
            print(f"[Interrogate] 질문 생성 오류: {e}")
        return []

    # ------------------------------------------------------------------ #
    #  Feature 4: Diarization — 브리프 압축 (2줄 요약 생성)
    # ------------------------------------------------------------------ #
    async def generate_brief(self, content: str) -> str:
        """문서 내용에서 2줄 브리프 생성"""
        if not self.llm_func or not content.strip():
            return ""
        try:
            prompt = self._build_brief_prompt(content[:1200])
            res = await self.llm_func(prompt)
            # JSON 문자열 추출 시도
            text = res.strip()
            m = re.search(r'"([^"]+)"', text)
            if m:
                return m.group(1)[:200]
            # JSON 형식이 아니면 첫 2줄 추출
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            return " ".join(lines[:2])[:200]
        except Exception as e:
            print(f"[Diarization] 브리프 생성 오류: {e}")
        return ""
