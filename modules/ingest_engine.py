import os
import shutil
import json
import re
from pathlib import Path
from datetime import datetime
from tag_linker import TagLinker
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

    # ------------------------------------------------------------------ #
    #  parse_frontmatter / merge_tags / update_frontmatter
    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_frontmatter(content):
        """YAML frontmatter 파싱: tags, description, questions, brief 반환"""
        tags = []
        description = ""
        questions = []
        brief = ""
        m = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
        if not m:
            return tags, description, questions, brief
        block = m.group(1)
        # tags 라인 파싱
        tm = re.search(r"tags:\s*\[([^\]]*)\]", block)
        if tm:
            tags = [t.strip() for t in tm.group(1).split(",") if t.strip()]
        # description 라인 파싱 (여러 줄 가능)
        dm = re.search(r"^description:\s*(.+)$", block, re.MULTILINE)
        if dm:
            description = dm.group(1).strip().strip('"').strip("'")
        # questions 라인 파싱 (YAML 리스트: - 질문1\n- 질문2)
        _qm_match = re.search(r"^questions:\s*$\n([\s\S]*?)^\w", block, re.MULTILINE)
        qm = re.findall(r"^\s*-\s*(.+)$", _qm_match.group(1) if _qm_match else "")
        if not qm:
            # inline 형식: questions: ["질문1", "질문2"]
            qm_inline = re.search(r"questions:\s*\[([^\]]*)\]", block)
            if qm_inline:
                qm = [q.strip().strip('"').strip("'") for q in qm_inline.group(1).split(",") if q.strip()]
        questions = [q.strip() for q in qm if q.strip()][:5]
        # brief 라인 파싱
        bm = re.search(r"^brief:\s*(.+)$", block, re.MULTILINE)
        if bm:
            brief = bm.group(1).strip().strip('"').strip("'")
        return tags, description, questions, brief

    @staticmethod
    def _read_file_content(file_path: Path) -> str:
        """파일 내용 읽기 — .pdf는 PyMuPDF(fitz)로 추출, 나머지는 텍스트 읽기"""
        suffix = file_path.suffix.lower()
        if suffix == ".pdf":
            try:
                import fitz
                doc = fitz.open(str(file_path))
                text = "".join(page.get_text() for page in doc)
                doc.close()
                return text.strip()
            except Exception as e:
                print(f"⚠️ [PDF] 추출 실패: {file_path.name} — {e}")
                return ""
        # 일반 텍스트 파일
        return file_path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _merge_tags(existing_tags, new_tags):
        """기존 태그와 새 태그 병합 (중복 제거)"""
        combined = existing_tags + [t for t in new_tags if t not in existing_tags]
        return combined

    @staticmethod
    def _update_frontmatter(content, new_tags, new_description="", new_questions=None, new_brief=""):
        """기존 frontmatter의 tags 병합 + description/questions/brief 추가.
           frontmatter가 없으면 새로 생성. 있으면 tags/description/questions/brief 병합."""
        tags_str = ", ".join(new_tags)
        desc_line = f"\ndescription: \"{new_description}\"" if new_description else ""
        # questions는 YAML 리스트 형태 (block 리스트 사용)
        questions_line = ""
        if new_questions:
            q_lines = "\n".join(f"  - \"{q}\"" for q in new_questions if q.strip())
            if q_lines:
                questions_line = f"\nquestions:\n{q_lines}"
        brief_line = f"\nbrief: \"{new_brief}\"" if new_brief else ""

        m = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
        if m:
            block = m.group(1)
            # 기존 tags 라인 교체
            if re.search(r"^tags:", block, re.MULTILINE):
                block = re.sub(r"^tags:.*$", f"tags: [{tags_str}]", block, flags=re.MULTILINE)
            else:
                block += f"\ntags: [{tags_str}]"
            # description이 있으면 교체/추가
            if new_description:
                if re.search(r"^description:", block, re.MULTILINE):
                    block = re.sub(r"^description:.*$", desc_line.strip(), block, flags=re.MULTILINE)
                else:
                    block += desc_line
            # questions가 있으면 교체/추가 (block YAML 리스트)
            if questions_line:
                if re.search(r"^questions:", block, re.MULTILINE):
                    block = re.sub(r"^questions:.*(\n\s+- .*)*", "", block, flags=re.MULTILINE)
                    block += f"\n{questions_line.strip()}"
                else:
                    block += f"\n{questions_line.strip()}"
            # brief가 있으면 교체/추가
            if new_brief:
                if re.search(r"^brief:", block, re.MULTILINE):
                    block = re.sub(r"^brief:.*$", brief_line.strip(), block, flags=re.MULTILINE)
                else:
                    block += brief_line
            return f"---\n{block}\n---\n" + content[m.end():]
        else:
            # frontmatter가 없으면 새로 생성
            extra = f"{desc_line}\n{questions_line.strip()}" if questions_line else desc_line
            extra += brief_line
            fm = f"---\ntags: [{tags_str}]{extra}\n---\n\n"
            return fm + content

    # ------------------------------------------------------------------ #
    #  LLM 프롬프트 (one-shot 예시 포함, Gemma4 호환)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_classify_prompt(content_preview):
        """Clips 분류용 LLM 프롬프트 — one-shot 예시 + 엄격한 JSON 출력"""
        return (
            "You are a wiki classifier. Analyze the file content and output ONLY a JSON object.\n\n"
            "=== EXAMPLES ===\n"
            "Input: [Claude Code를 활용한 AI 기반 개발 워크플로우 설명. 자동 계획-작업-검토 사이클로 고품질 개발을 달성하는 방법.]\n"
            'Output: {"category": "10_AI_Automation", "title": "Claude Code 개발 워크플로우", "description": "Claude Code를 활용한 AI 기반 개발 워크플로우로, 자동 계획-작업-검토 사이클을 통해 고품질 개발을 달성한다. 반복적인 코딩 작업을 자동화하여 생산성을 극대화한다.", "keywords": ["claude-code", "ai-development", "automation", "workflow"]}\n\n'
            "Input: [협수로 내 도선 과실 및 인적 오류 관련 해상 충돌 사고 분석. 선박 운항 안전 및 법적 책임에 관한 연구.]\n"
            'Output: {"category": "20_Research", "title": "해상 충돌 사고 분석", "description": "협수로 내 도선 과실 및 인적 오류로 인한 해상 충돌 사고를 분석한다. 선박 운항 안전 대책과 법적 책임 관계를 종합적으로 검토한다.", "keywords": ["maritime", "collision", "human-error", "safety", "legal-liability"]}\n'
            "=== END EXAMPLES ===\n\n"
            "Rules:\n"
            "- category: MUST be exactly one of: 10_AI_Automation, 20_Research, 30_Journal, 40_Thesis, 50_Invest, Unsorted\n"
            "- title: clean Korean, no special chars except spaces/hyphens\n"
            "- description: EXACTLY three sentences in Korean (3-line summary)\n"
            "- keywords: 5-8 relevant keywords as string array — mix of general categories and specific terms\n\n"
            f"File content:\n{content_preview[:800]}\n\n"
            "Output ONLY the JSON object, nothing else."
        )

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
    @staticmethod
    def _parse_json_response(res):
        """LLM 응답에서 JSON 객체 추출 — Gemma4 대응"""
        if not res:
            return None
        text = res.strip()
        # ```json ... ``` 블록 제거
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if m:
            text = m.group(1).strip()
        # 첫 {부터 마지막 }까지
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end+1]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _parse_json_array(res):
        """LLM 응답에서 JSON 배열 추출"""
        if not res:
            return None
        text = res.strip()
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if m:
            text = m.group(1).strip()
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            text = text[start:end+1]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    # ------------------------------------------------------------------ #
    #  Feature 1: 사전 질문 생성 (Interrogate)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_interrogate_prompt(content_preview: str) -> str:
        """문서 내용에서 탐구 질문 3-5개 생성"""
        return (
            "You are a Socratic question generator. Based on the content below, "
            "generate 3-5 deep, probing questions that would help a researcher "
            "explore this topic further. Output ONLY a JSON array of strings.\n\n"
            "Rules:\n"
            "- Each question must be in Korean\n"
            "- Questions should challenge assumptions, find gaps, or suggest connections\n"
            "- Keep each question under 100 chars\n"
            "- Output format: [\"질문1\", \"질문2\", \"질문3\"]\n\n"
            f"Content:\n{content_preview[:1500]}\n\n"
            "Output ONLY the JSON array, nothing else."
        )

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
    @staticmethod
    def _build_brief_prompt(content_preview: str) -> str:
        """문서 내용에서 2줄 브리프 생성"""
        return (
            "You are a brief generator. Compress the following content into "
            "exactly 2 concise Korean sentences. Capture the single most important "
            "insight and its context. Output ONLY a JSON string, no other text.\n\n"
            f"Content:\n{content_preview[:1200]}\n\n"
            'Output format: "2줄 브리프 텍스트 여기에"'
        )

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
