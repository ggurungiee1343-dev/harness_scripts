import os
import re
from pathlib import Path

class WikiLinker:
    def __init__(self, vault_path="/Users/bluesea/Applications/Mjobsidian"):
        self.vault_path = Path(vault_path)
        self.wiki_path = self.vault_path / "wiki"
        self.ignore_files = {"memory.md", "hot.md"}

    def get_existing_note_titles(self):
        """위키 내의 모든 md 파일 제목(확장자 제외) 수집"""
        if not self.wiki_path.exists():
            return []
        
        titles = set()
        # 10_AI_Automation, 20_Research, 30_Journal, Unsorted 등 하위 폴더 순회
        for root, _, files in os.walk(self.wiki_path):
            for file in files:
                if file.endswith(".md") and not file.startswith("."):
                    if file.lower() in self.ignore_files:
                        continue
                    stem = Path(file).stem
                    # 글자 수 3자 미만 단어는 매칭 노이즈가 크므로 스킵 (단, 공백 포함은 예외)
                    if len(stem) >= 3 or " " in stem or "_" in stem:
                        titles.add(stem)
        
        # 매칭 우선순위를 위해 글자 수 역순 정렬
        return sorted(list(titles), key=len, reverse=True)

    def link_text(self, text):
        """텍스트 내의 키워드를 분석하여 [[노트제목]]으로 링크화"""
        keywords = self.get_existing_note_titles()
        if not keywords:
            return text

        # 1. 치환에서 제외할 블록들 마스킹 처리 (코드 블록, frontmatter, 기존 링크)
        code_blocks = []
        def mask_code_block(match):
            code_blocks.append(match.group(0))
            return f"__CODE_BLOCK_PLACEHOLDER_{len(code_blocks)-1}__"

        # 3중 백틱 코드블록 마스킹
        text = re.sub(r"```.*?```", mask_code_block, text, flags=re.DOTALL)
        # 단일 백틱 인라인 코드 마스킹
        text = re.sub(r"`[^`\n]+`", mask_code_block, text)

        # 기존 위키 링크 마스킹 (중복 치환 방지)
        wiki_links = []
        def mask_wiki_link(match):
            wiki_links.append(match.group(0))
            return f"__WIKI_LINK_PLACEHOLDER_{len(wiki_links)-1}__"
        
        text = re.sub(r"\[\[.*?\]\]", mask_wiki_link, text)

        # frontmatter 마스킹
        frontmatter = []
        def mask_frontmatter(match):
            frontmatter.append(match.group(0))
            return f"__FRONTMATTER_PLACEHOLDER_{len(frontmatter)-1}__"
        
        # 문서 시작 지점의 --- ... --- 마스킹
        text = re.sub(r"^---.*?---", mask_frontmatter, text, flags=re.DOTALL)

        # 2. 키워드 치환
        for kw in keywords:
            # 공백이나 언더바가 있으면 둘 다 매칭되도록 정규식 패턴 생성 (예: Graphify_사용_가이드 -> Graphify[ _]사용[ _]가이드)
            normalized_pattern = "".join('[ _]' if c in ('_', ' ') else re.escape(c) for c in kw)
            
            # 영어 및 한글 단어 경계 패턴: 단어가 알파벳/숫자/한글이 아닌 문자에 둘러싸여 있을 때만 매칭
            pattern = re.compile(rf"(?<![a-zA-Z0-9가-힣_\-]){normalized_pattern}(?![a-zA-Z0-9가-힣_\-])")
            
            def replacer(match):
                matched_word = match.group(0)
                # 원본 단어가 언더바/띄어쓰기 등 파일명(kw)과 다르면 alias 기법 적용
                wiki_links.append(f"[[{kw}|{matched_word}]]" if kw != matched_word else f"[[{kw}]]")
                return f"__WIKI_LINK_PLACEHOLDER_{len(wiki_links)-1}__"

            text = pattern.sub(replacer, text)

        # 3. 플레이스홀더 복원 (역순으로 언마스킹)
        # WIKI LINK 언마스킹
        for idx, val in enumerate(wiki_links):
            text = text.replace(f"__WIKI_LINK_PLACEHOLDER_{idx}__", val)
        
        # CODE BLOCK 언마스킹
        for idx, val in enumerate(code_blocks):
            text = text.replace(f"__CODE_BLOCK_PLACEHOLDER_{idx}__", val)

        # FRONTMATTER 언마스킹
        for idx, val in enumerate(frontmatter):
            text = text.replace(f"__FRONTMATTER_PLACEHOLDER_{idx}__", val)

        return text
