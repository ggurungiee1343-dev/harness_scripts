"""
indexer_text_utils.py — HybridKnowledgeIndexer의 순수 텍스트/벡터 유틸 (2026-06-11 분할)

knowledge_indexer.py에서 추출된 self-free 함수들:
마크다운 스마트 청킹, 토크나이저, TF 계산, 코사인 유사도.
HybridKnowledgeIndexer는 별칭으로 바인딩하므로 기존 호출부는 무변경.
"""
from __future__ import annotations

import re
import math
from typing import List, Dict

MAX_CHUNK_CHARS = 2000       # 청크 최대 글자 수 (knowledge_indexer와 동일)
MIN_CHUNK_CHARS = 50         # 너무 짧은 청크 제외


def smart_chunk_markdown(md_text: str) -> List[Dict[str, str]]:
    """
    llm_wiki의 핵심: 마크다운 구조를 보존하며 청크 분할

    원칙:
    - H2(##) 헤더 경계 존중 (새 청크 시작)
    - YAML Frontmatter는 첫 번째 청크에 포함 (메타데이터 보존)
    - 코드블록(```) 완전성 유지 (중간에 자르지 않음)
    - 테이블 단위 분할 금지
    - MAX_CHUNK_CHARS 초과 시 단락(\n\n) 경계에서 분할
    """
    chunks: List[Dict[str, str]] = []
    lines = md_text.split("\n")

    current_heading = ""
    current_lines: List[str] = []
    in_code_block = False
    in_table = False

    def flush_chunk():
        nonlocal current_lines, current_heading
        text = "\n".join(current_lines).strip()
        if len(text) >= MIN_CHUNK_CHARS:
            # MAX_CHUNK_CHARS 초과 시 단락 단위 분할
            if len(text) > MAX_CHUNK_CHARS:
                sub_chunks = split_by_paragraph(text, current_heading)
                chunks.extend(sub_chunks)
            else:
                chunks.append({"heading": current_heading, "content": text})
        current_lines = []

    for line in lines:
        stripped = line.strip()

        # 코드 블록 토글
        if stripped.startswith("```"):
            in_code_block = not in_code_block

        # 테이블 감지
        if stripped.startswith("|") and not in_code_block:
            in_table = True
        elif in_table and not stripped.startswith("|"):
            in_table = False

        # H2 헤더 감지 → 새 청크 시작 (코드블록/테이블 안에서는 무시)
        if stripped.startswith("## ") and not in_code_block and not in_table:
            flush_chunk()
            current_heading = stripped[3:].strip()
            current_lines = [line]
        else:
            current_lines.append(line)

    flush_chunk()  # 마지막 청크 저장

    return chunks


def split_by_paragraph(text: str, heading: str) -> List[Dict[str, str]]:
    """큰 청크를 단락 경계에서 분할."""
    paragraphs = re.split(r"\n\n+", text)
    sub_chunks: List[Dict[str, str]] = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) > MAX_CHUNK_CHARS and current:
            if len(current.strip()) >= MIN_CHUNK_CHARS:
                sub_chunks.append({"heading": heading, "content": current.strip()})
            current = para
        else:
            current = (current + "\n\n" + para).strip() if current else para

    if len(current.strip()) >= MIN_CHUNK_CHARS:
        sub_chunks.append({"heading": heading, "content": current.strip()})

    return sub_chunks


def tokenize(text: str) -> List[str]:
    """한국어 + 영어 혼합 토크나이저 (공백/특수문자 분리)."""
    # 영어: 소문자, 한국어: 음절 단위, 숫자 포함
    tokens = re.findall(r"[가-힣]+|[a-z0-9]+", text.lower())
    # 1글자 영어 토큰 제외 (한국어는 유지)
    return [t for t in tokens if len(t) > 1 or re.match(r"[가-힣]", t)]


def compute_tf(tokens: List[str]) -> Dict[str, float]:
    """TF(Term Frequency) 계산."""
    if not tokens:
        return {}
    tf: Dict[str, float] = {}
    for t in tokens:
        tf[t] = tf.get(t, 0) + 1
    total = len(tokens)
    return {t: count / total for t, count in tf.items()}


def cosine_sim(v1: Dict[str, float], v2: Dict[str, float]) -> float:
    """희소 벡터 코사인 유사도."""
    common = set(v1) & set(v2)
    if not common:
        return 0.0
    dot = sum(v1[t] * v2[t] for t in common)
    norm1 = math.sqrt(sum(x * x for x in v1.values()))
    norm2 = math.sqrt(sum(x * x for x in v2.values()))
    return dot / (norm1 * norm2) if norm1 and norm2 else 0.0
