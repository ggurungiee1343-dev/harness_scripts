"""
ingest_text_utils.py — IngestEngine의 순수 텍스트 처리 유틸 (2026-06-11 분할)

ingest_engine.py에서 추출된 self-free 함수들:
frontmatter 파싱/병합, 파일 읽기(PDF 포함), LLM 프롬프트 빌더, JSON 응답 파서.
IngestEngine은 staticmethod 별칭으로 바인딩하므로 기존 호출부는 무변경.
"""
import json
import re
from pathlib import Path


def parse_frontmatter(content):
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


def read_file_content(file_path: Path) -> str:
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


def merge_tags(existing_tags, new_tags):
    """기존 태그와 새 태그 병합 (중복 제거)"""
    combined = existing_tags + [t for t in new_tags if t not in existing_tags]
    return combined


def update_frontmatter(content, new_tags, new_description="", new_questions=None, new_brief=""):
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


def build_classify_prompt(content_preview):
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


def parse_json_response(res):
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


def parse_json_array(res):
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


def build_interrogate_prompt(content_preview: str) -> str:
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


def build_brief_prompt(content_preview: str) -> str:
    """문서 내용에서 2줄 브리프 생성"""
    return (
        "You are a brief generator. Compress the following content into "
        "exactly 2 concise Korean sentences. Capture the single most important "
        "insight and its context. Output ONLY a JSON string, no other text.\n\n"
        f"Content:\n{content_preview[:1200]}\n\n"
        'Output format: "2줄 브리프 텍스트 여기에"'
    )
