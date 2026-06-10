"""
hermes_context_builder.py — 코드베이스 컨텍스트 하네스
========================================================
Claude Code의 대규모 코드베이스 탐색 아키텍처를 헤르메스에 이식합니다.

주요 기능:
  1. load_hierarchical_context(directory)
     → 폴더에서 루트 방향으로 HERMES.md를 계층적으로 수집
  2. build_directory_map(directory)
     → .hermesignore 기반 노이즈 필터링된 디렉토리 요약 맵 생성
  3. should_ignore(file_path, directory)
     → 패턴 기반 파일 제외

[비대화 방지 전략 — 2026-05-21]
HERMES.md가 비대화되어 속도 저하가 발생하지 않도록 두 가지 안전장치를 내장합니다:

  A) 컨텍스트 주입 시 MAX_CONTEXT_CHARS(기본 4000자) 이상이면 자동 요약(헤더+규칙만 추출)
  B) load_hierarchical_context()는 파일을 통째로 주입하지 않고
     섹션 헤더 + 핵심 규칙행만 선별하여 주입합니다.

결론: HERMES.md가 아무리 커져도 LLM에 주입되는 양은 항상 일정 수준 이하입니다.
"""
from __future__ import annotations

import os
import re
import fnmatch

# 컨텍스트로 주입할 최대 문자 수 (이 이상이면 자동 요약)
MAX_CONTEXT_CHARS = 4000
# 디렉토리 맵에 보여줄 최대 아이템 수
MAX_MAP_ITEMS = 50
# 글로벌 .hermesignore 경로 (워크스페이스 밖에 위치해도 항상 로드)
SCRIPTS_HERMESIGNORE = os.path.expanduser(
    "~/Applications/Mjauto/Scripts/.hermesignore"
)


class ContextBuilder:
    def __init__(self, workspace_root: str):
        self.workspace_root = os.path.abspath(workspace_root)
        self.ignore_files = [".hermesignore"]
        self.context_files = ["HERMES.md", "README.md"]
        # 글로벌 .hermesignore 패턴 캐시 (최초 호출 시 로드)
        self._global_patterns: list[str] | None = None

    # ──────────────────────────────────────────────────────────────────────────
    # 내부 유틸
    # ──────────────────────────────────────────────────────────────────────────

    def _get_ignore_patterns(self, directory: str) -> list[str]:
        """현재 폴더에서 루트 방향으로 .hermesignore를 수집하여 패턴 목록 반환."""
        patterns: list[str] = []
        current = os.path.abspath(directory)
        while current.startswith(self.workspace_root):
            for ignore_file in self.ignore_files:
                ignore_path = os.path.join(current, ignore_file)
                if os.path.exists(ignore_path):
                    with open(ignore_path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line and not line.startswith("#"):
                                patterns.append(line)
            if current == self.workspace_root:
                break
            current = os.path.dirname(current)

        # 글로벌 .hermesignore 로드 (워크스페이스 밖에 있어도 항상 적용)
        if self._global_patterns is None:
            self._global_patterns = []
            if os.path.exists(SCRIPTS_HERMESIGNORE):
                try:
                    with open(SCRIPTS_HERMESIGNORE, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line and not line.startswith("#"):
                                self._global_patterns.append(line)
                except OSError:
                    pass
        patterns.extend(self._global_patterns)

        return patterns

    def _slim_context(self, raw_content: str) -> str:
        """
        [비대화 방지] HERMES.md 내용이 MAX_CONTEXT_CHARS를 초과할 경우
        마크다운 헤더(#)로 시작하는 줄과 리스트(-/번호) 첫 줄만 선별하여 슬림화합니다.
        이렇게 하면 파일이 커져도 LLM 주입량은 항상 일정합니다.
        """
        if len(raw_content) <= MAX_CONTEXT_CHARS:
            return raw_content

        lines = raw_content.split("\n")
        slim_lines: list[str] = []
        char_count = 0

        for line in lines:
            stripped = line.strip()
            # 헤더, 테이블 헤더, 리스트 항목, 코드블록 첫 줄만 포함
            if (
                stripped.startswith("#")
                or stripped.startswith("|")
                or stripped.startswith("- ")
                or stripped.startswith("* ")
                or re.match(r"^\d+\.", stripped)
                or stripped.startswith("```")
            ):
                slim_lines.append(line)
                char_count += len(line) + 1
                if char_count >= MAX_CONTEXT_CHARS:
                    slim_lines.append("... (이하 생략 — HERMES.md 직접 참조)")
                    break

        return "\n".join(slim_lines) if slim_lines else raw_content[:MAX_CONTEXT_CHARS]

    # ──────────────────────────────────────────────────────────────────────────
    # 공개 API
    # ──────────────────────────────────────────────────────────────────────────

    def should_ignore(self, file_path: str, directory: str) -> bool:
        """
        file_path를 무시할지 여부를 반환합니다.
        .hermesignore 패턴 + 기본 노이즈 패턴을 결합합니다.
        """
        patterns = self._get_ignore_patterns(directory)
        # 기본 노이즈 패턴
        patterns.extend([
            "__pycache__", ".git", "*.pyc", ".DS_Store", "*.log",
            "*.lock", "*.tmp", "*.json",  # json은 대용량 메모리 파일 제외
        ])

        rel_path = os.path.relpath(file_path, self.workspace_root)
        base_name = os.path.basename(file_path)

        for p in patterns:
            if fnmatch.fnmatch(base_name, p) or fnmatch.fnmatch(rel_path, p):
                return True
        return False

    def load_hierarchical_context(self, directory: str) -> str:
        """
        directory → 루트 방향으로 HERMES.md / README.md를 수집합니다.
        각 파일은 _slim_context()를 통해 크기가 제어됩니다.
        루트 컨텍스트가 먼저, 구체적(하위) 컨텍스트가 나중에 배치됩니다.
        """
        contexts: list[str] = []
        current = os.path.abspath(directory)

        while current.startswith(self.workspace_root) and os.path.isdir(current):
            for ctx_file in self.context_files:
                ctx_path = os.path.join(current, ctx_file)
                if os.path.exists(ctx_path):
                    try:
                        with open(ctx_path, "r", encoding="utf-8") as f:
                            raw = f.read().strip()
                        if raw:
                            slimmed = self._slim_context(raw)
                            contexts.append(
                                f"--- [{ctx_file} @ {os.path.relpath(current, self.workspace_root)}] ---\n"
                                f"{slimmed}\n"
                            )
                    except Exception:
                        pass
            if current == self.workspace_root:
                break
            current = os.path.dirname(current)

        # 루트 → 하위 순으로 정렬
        contexts.reverse()
        return "\n".join(contexts) if contexts else "(컨텍스트 파일 없음)"

    def build_directory_map(self, directory: str) -> str:
        """
        디렉토리 구조를 AI가 이해하기 쉬운 요약 맵으로 변환합니다.
        .hermesignore로 필터링 후 MAX_MAP_ITEMS 초과 시 생략합니다.
        """
        if not os.path.exists(directory):
            return f"❌ 디렉토리가 존재하지 않습니다: {directory}"

        map_lines = [f"📂 Codebase Map: {directory}"]
        item_count = 0

        try:
            for item in sorted(os.listdir(directory)):
                if item_count >= MAX_MAP_ITEMS:
                    map_lines.append(f"  ... (이하 {len(os.listdir(directory)) - item_count}개 생략)")
                    break

                item_path = os.path.join(directory, item)
                if self.should_ignore(item_path, directory):
                    continue

                if os.path.isdir(item_path):
                    try:
                        sub_count = len([
                            x for x in os.listdir(item_path)
                            if not self.should_ignore(os.path.join(item_path, x), item_path)
                        ])
                        map_lines.append(f"  📁 {item}/  ({sub_count}개)")
                    except PermissionError:
                        map_lines.append(f"  📁 {item}/  (접근 불가)")
                else:
                    size_kb = os.path.getsize(item_path) // 1024
                    size_str = f"{size_kb}KB" if size_kb > 0 else "<1KB"
                    map_lines.append(f"  📄 {item}  [{size_str}]")

                item_count += 1

        except Exception as e:
            return f"❌ 맵 생성 오류: {e}"

        return "\n".join(map_lines)


# ════════════════════════════════════════════════════════════════════════
#  Feature 3: TaskTypeResolver — 작업 유형 기반 컨텍스트 라우팅
# ════════════════════════════════════════════════════════════════════════

TASK_FILE_OPS = "FILE_OPS"
TASK_RESEARCH = "RESEARCH"
TASK_CODE = "CODE"
TASK_WIKI = "WIKI_QUERY"
TASK_GENERAL = "GENERAL_CHAT"

TASK_KEYWORDS: dict[str, list[str]] = {
    TASK_FILE_OPS: [
        "/read", "/write", "/create", "/delete", "/rename", "/move",
        "/list", "/search", "파일", "읽기", "저장", "만들기",
    ],
    TASK_RESEARCH: [
        "연구", "논문", "arxiv", "페이퍼", "survey", "literature",
        "리뷰", "분석", "조사", "research", "paper", "article",
        "찾아", "알아봐", "검색", "웹", "web",
    ],
    TASK_CODE: [
        "코드", "코딩", "개발", "버그", "디버깅", "테스트",
        "python", "javascript", "함수", "클래스", "리팩토링",
        "code", "coding", "debug", "test", "build", "deploy",
        "컴파일", "실행", "수정",
    ],
    TASK_WIKI: [
        "/recent", "/hot", "위키", "노트", "메모", "wiki",
        "옵시디언", "obsidian", "메타", "documentation",
        "기록", "문서",
    ],
}


def resolve_task_type(user_text: str) -> str:
    """사용자 입력을 분석하여 작업 유형을 반환합니다.
    
    Returns:
        TASK_FILE_OPS | TASK_RESEARCH | TASK_CODE | TASK_WIKI | TASK_GENERAL
    """
    text_lower = user_text.lower().strip()
    scores: dict[str, int] = {t: 0 for t in TASK_KEYWORDS}

    for task_type, keywords in TASK_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower or kw in user_text:
                scores[task_type] += 1

    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return TASK_GENERAL
    return best


def get_task_context(task_type: str, workspace_root: str) -> str:
    """작업 유형에 따라 최적의 컨텍스트 로드"""
    builder = ContextBuilder(workspace_root)

    if task_type == TASK_FILE_OPS:
        # 파일 작업 시: Scripts/ 디렉토리 맵 + 주요 모듈 컨텍스트
        ctx = builder.load_hierarchical_context(workspace_root)
        return f"[작업 컨텍스트 — 파일 작업]\n{ctx}\n"

    elif task_type == TASK_RESEARCH:
        # 연구 작업 시: 위키 메타 문서 + Clippings 구조
        vault = os.path.expanduser("~/Applications/Mjobsidian")
        meta_ctx = builder.load_hierarchical_context(os.path.join(vault, "wiki", "00_Meta"))
        return f"[작업 컨텍스트 — 연구/분석]\n{meta_ctx}\n"

    elif task_type == TASK_CODE:
        # 코드 작업 시: Scripts/ 모듈 구조 + 컨텍스트
        ctx = builder.load_hierarchical_context(workspace_root)
        return f"[작업 컨텍스트 — 코드/개발]\n{ctx}\n"

    elif task_type == TASK_WIKI:
        # 위키 탐색 시: vault 구조 + 메타
        vault = os.path.expanduser("~/Applications/Mjobsidian")
        builder2 = ContextBuilder(vault)
        ctx = builder2.load_hierarchical_context(os.path.join(vault, "wiki"))
        return f"[작업 컨텍스트 — 위키 탐색]\n{ctx}\n"

    else:  # TASK_GENERAL
        return ""

TASK_EMOJI = {
    TASK_FILE_OPS: "📁",
    TASK_RESEARCH: "🔬",
    TASK_CODE: "💻",
    TASK_WIKI: "📖",
    TASK_GENERAL: "💬",
}
