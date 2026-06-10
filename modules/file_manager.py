import os
import re
import unicodedata
import fnmatch
from datetime import datetime, timezone, timedelta

HERMESIGNORE_PATH = os.path.expanduser(
    "~/Applications/Mjauto/Scripts/.hermesignore"
)

def _load_hermesignore_patterns() -> list[str]:
    """~/.hermesignore 패턴을 로드합니다. 파일이 없으면 빈 리스트."""
    patterns: list[str] = []
    if os.path.exists(HERMESIGNORE_PATH):
        try:
            with open(HERMESIGNORE_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        patterns.append(line)
        except OSError:
            pass
    return patterns

def _should_ignore(name: str, patterns: list[str]) -> bool:
    """파일/디렉토리명이 .hermesignore 패턴 중 하나와 매칭되면 True."""
    if not patterns:
        return False
    for p in patterns:
        if fnmatch.fnmatch(name, p):
            return True
    return False


class FileManager:
    def __init__(self, vault_path="/Users/bluesea/Applications/Mjobsidian", bot_author="헤르메스봇"):
        self.vault_path = unicodedata.normalize('NFC', vault_path)
        self.bot_author = bot_author

    def _update_bottom_timestamp(self, content):
        """
        마크다운 파일용 하단 최종 업데이트 시간 + 작성자 자동 삽입/갱신
        지원 포맷:
          *최종 업데이트: YYYY-MM-DD HH:MM (note)*
          *최종 업데이트: YYYY-MM-DD*
          **최종 업데이트:** YYYY-MM-DD HH:MM (note)**
          *최종 업데이트: YYYY-MM-DD (note)*
          *작성자: 이름 (note)*
        """
        kst = timezone(timedelta(hours=9))
        timestamp_str = datetime.now(kst).strftime("%Y-%m-%d %H:%M:%S")

        # ---- Timestamp regex (모든 포맷 대응) ----
        # 전체 매치 후 note 보존, 새 타임스탬프로 재구성
        ts_pattern = (
            r"\n"                              # 개행
            r"(?:\*{1,2})?"                    # 볼드 오픈 (선택: * 또는 **)
            r"최종\s+업데이트:"                # 키워드
            r"\*{0,2}\s*"                      # 콜론 뒤 볼드 닫힘 (선택: **최종 업데이트:** 또는 *최종 업데이트:*)
            r"\d{4}-\d{2}-\d{2}"               # 날짜
            r"(?:[\s ]+\d{2}:\d{2}(?::\d{2})?)?" # 시간 (선택, 공백 또는 non-breaking space)
            r"(?:\s*\(([^)]*)\))?"             # 괄호 메모 (선택, 캡처)
            r"\s*"                             # 후행 공백
            r"(?:\*{1,2})?"                    # 볼드 닫힘 (선택)
        )

        ts_match = re.search(ts_pattern, content)
        if ts_match:
            note = ts_match.group(1)  # 괄호 메모 (있으면 문자열, 없으면 None)
            new_ts_line = f"\n*최종 업데이트: {timestamp_str}"
            if note:
                new_ts_line += f" ({note})"
            new_ts_line += "*"
            content = re.sub(ts_pattern, new_ts_line, content)
        else:
            # 기존 푸터가 전혀 없는 경우 → 새로 추가
            content = content.rstrip() + f"\n\n*최종 업데이트: {timestamp_str}*"
            # 작성자가 이미 있으면 추가 안 함 (아래 author 처리에서 다룸)
            if not re.search(r"\n\*작성자:", content):
                content += f"\n*작성자: {self.bot_author}*"
            return content

        # ---- Author regex (괄호 메모 보존) ----
        author_pattern = r"\n\*작성자:\s*([^*]+?)(?:\s*\(([^)]*)\))?\s*\*"
        author_match = re.search(author_pattern, content)
        if author_match:
            old_note = author_match.group(2)  # 괄호 메모
            new_author_line = f"\n*작성자: {self.bot_author}"
            if old_note:
                new_author_line += f" ({old_note})"
            new_author_line += "*"
            content = re.sub(author_pattern, new_author_line, content)
        else:
            # 작성자 라인이 없는 경우 → timestamp 아래에 추가
            ts_end = content.find("\n", content.rfind("*최종 업데이트:"))
            if ts_end != -1:
                content = content[:ts_end] + f"\n*작성자: {self.bot_author}*" + content[ts_end:]
            else:
                content += f"\n*작성자: {self.bot_author}*"

        return content

    def write_file(self, rel_path, content, update_timestamp=True):
        p = os.path.join(self.vault_path, unicodedata.normalize('NFC', rel_path))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        
        # 마크다운 파일일 경우 최종 업데이트 시간 갱신 (선택)
        if p.endswith(".md") and update_timestamp:
            content = self._update_bottom_timestamp(content)
            
        with open(p, "w", encoding="utf-8") as f: 
            f.write(content)
        return "저장 성공"

    def read_file(self, rel_path):
        p = os.path.join(self.vault_path, unicodedata.normalize('NFC', rel_path))
        if os.path.exists(p):
            if os.path.isdir(p): return "오류: 디렉토리입니다. 파일명을 포함한 정확한 경로를 입력하세요."
            # .hermesignore 패턴에 걸리는 파일은 읽기 거부
            patterns = _load_hermesignore_patterns()
            if patterns and _should_ignore(os.path.basename(p), patterns):
                return "오류: .hermesignore에 의해 제외된 파일입니다."
            with open(p, "r", encoding="utf-8") as f: return f.read()
        return "파일 없음"

    def list_files(self, rel_path=""):
        p = os.path.join(self.vault_path, unicodedata.normalize('NFC', rel_path))
        if not os.path.exists(p): return "경로 없음"
        if not os.path.isdir(p): return "디렉토리가 아님"
        patterns = _load_hermesignore_patterns()
        try:
            items = os.listdir(p)
            res = []
            for i in items:
                if i.startswith('.'): continue
                name = unicodedata.normalize('NFC', i)
                if _should_ignore(name, patterns):
                    continue
                is_dir = os.path.isdir(os.path.join(p, i))
                res.append(f"{'[DIR] ' if is_dir else '[FILE] '}{name}")
            return "\n".join(sorted(res))
        except Exception as e: return f"오류: {str(e)}"

    def replace_content(self, rel_path, old_text, new_text):
        p = os.path.join(self.vault_path, unicodedata.normalize('NFC', rel_path))
        if not os.path.exists(p): return "파일 없음"
        with open(p, "r", encoding="utf-8") as f: content = f.read()
        if old_text not in content: return "기본 텍스트를 찾을 수 없음"
        new_content = content.replace(old_text, new_text)
        
        # 마크다운 파일일 경우 최종 업데이트 시간 갱신
        if p.endswith(".md"):
            new_content = self._update_bottom_timestamp(new_content)
            
        with open(p, "w", encoding="utf-8") as f: 
            f.write(new_content)
        return "치환 성공"

    def read_dir(self, rel_path):
        p = os.path.join(self.vault_path, unicodedata.normalize('NFC', rel_path))
        if not os.path.exists(p) or not os.path.isdir(p): return "경로 없음 또는 디렉토리가 아님"
        contents = []
        try:
            for f in sorted(os.listdir(p)):
                if f.endswith('.md') and not f.startswith('.'):
                    f_path = os.path.join(p, f)
                    with open(f_path, "r", encoding="utf-8") as f_obj:
                        name = unicodedata.normalize('NFC', f)
                        contents.append(f"--- [File: {name}] ---\n{f_obj.read()}\n")
            if not contents: return "디렉토리에 읽을 수 있는 마크다운 파일이 없습니다."
            return "\n".join(contents)
        except Exception as e: return f"오류: {str(e)}"