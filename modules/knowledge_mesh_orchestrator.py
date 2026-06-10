"""
knowledge_mesh_orchestrator.py — PKM 중앙 제어기 (v1.0)
=====================================================
DecisionAgent(클라우드 LLM)의 JSON 레시피를 받아 로컬 프리미티브를
순차/병렬 실행하고, 결과를 통합 타임라인 + 교차 분석으로 반환.

PKM_2 설계 기준:
- web_search_multi: arXiv/Semantic Scholar 병렬 검색 (캐시 우선)
- local_semantic_search: 기존 HybridKnowledgeIndexer 벡터 검색 활용
- merge_timeline: timeline_builder 위임
- cross_reference: cross_reference_analyzer 위임
- summarize_insights: LLM 요약

의존성:
- modules/knowledge_indexer.py (HybridKnowledgeIndexer)
- modules/timeline_builder.py (merge_timeline)
- modules/cross_reference_analyzer.py (cross_reference)
- handlers/_base.py (_call_llm)
"""

import asyncio
import json
import hashlib
import logging
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

log = logging.getLogger("knowledge_mesh_orchestrator")

# ── 지연 임포트 (순환참조 방지) ─────────────────────────────────────────
_hybrid_indexer = None
_timeline_builder = None
_cross_ref = None

def _get_indexer():
    global _hybrid_indexer
    if _hybrid_indexer is None:
        from modules.knowledge_indexer import HybridKnowledgeIndexer
        _hybrid_indexer = HybridKnowledgeIndexer()
    return _hybrid_indexer

def _get_timeline():
    global _timeline_builder
    if _timeline_builder is None:
        from modules.timeline_builder import merge_timeline
        _timeline_builder = merge_timeline
    return _timeline_builder

def _get_cross_ref():
    global _cross_ref
    if _cross_ref is None:
        from modules.cross_reference_analyzer import cross_reference
        _cross_ref = cross_reference
    return _cross_ref

async def _get_llm():
    """지연 LLM 호출 (handlers._base._call_llm)"""
    from handlers._base import _call_llm
    return _call_llm


class KnowledgeMeshOrchestrator:
    """
    PKM 중앙 제어기 — JSON 레시피 기반 DAG 실행

    레시피 예시:
    [
      {"id":"R1","op":"web_search_multi","sources":["arxiv"],"query":"Transformer efficient attention 2026"},
      {"id":"R2","op":"local_semantic_search","query":"Transformer attention", "scope":"notes"},
      {"id":"R3","op":"merge_timeline","inputs":["R1","R2"]},
      {"id":"R4","op":"cross_reference","user_note_input":"R2","web_paper_input":"R1"},
      {"id":"R5","op":"summarize_insights","input":"R4","recipe_summary":true}
    ]
    """

    def __init__(self):
        self.cache: Dict[str, Tuple[Any, datetime]] = {}  # cache_key → (data, timestamp)
        self.cache_ttl = 3600  # 1시간

    async def execute_recipe(self, recipe: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        레시피 DAG 실행. 각 step의 결과는 step["id"] 키로 누적.
        Returns: {step_id: result, ...}  — 단, 마지막 summarize_insights 결과는 "result" 키로 반환.
        """
        results: Dict[str, Any] = {}

        for step in recipe:
            op = step.get("op", "")
            step_id = step.get("id", op)

            try:
                if op == "web_search_multi":
                    results[step_id] = await self._web_search_multi(step)
                elif op == "local_semantic_search":
                    results[step_id] = await self._local_semantic_search(step)
                elif op == "merge_timeline":
                    results[step_id] = await self._merge_timeline(step, results)
                elif op == "cross_reference":
                    results[step_id] = await self._cross_reference(step, results)
                elif op == "summarize_insights":
                    results[step_id] = await self._summarize_insights(step, results)
                else:
                    log.warning(f"[Orch] 알 수 없는 op: {op}")
            except Exception as e:
                log.error(f"[Orch] step {step_id}({op}) 실패: {e}")
                results[step_id] = {"error": str(e)}

        # 마지막 summarize_insights 결과를 "result" 키로도 노출
        last_summary = None
        for sid, data in reversed(list(results.items())):
            if isinstance(data, dict) and "summary_text" in data:
                last_summary = data
                break
        if last_summary:
            results["result"] = last_summary
        else:
            # summarize가 없으면 merge_timeline 결과를 result로
            for sid, data in reversed(list(results.items())):
                if isinstance(data, dict) and "timeline" in data:
                    results["result"] = data
                    break

        return results

    # ── web_search_multi ─────────────────────────────────────────────

    async def _web_search_multi(self, step: Dict) -> Dict[str, Any]:
        """arXiv / Semantic Scholar 병렬 검색 (캐시 우선)"""
        cache_key = self._cache_key(step)
        cached = self._check_cache(cache_key)
        if cached is not None:
            return cached

        sources = step.get("sources", ["arxiv"])
        query = step["query"]
        top_k = step.get("top_k", 5)

        results = {"papers": [], "query": query, "sources": sources}

        for source in sources:
            try:
                papers = await self._search_source(source, query, top_k)
                results["papers"].extend(papers)
            except Exception as e:
                log.warning(f"[Orch] {source} 검색 실패: {e}")

        self._set_cache(cache_key, results)
        return results

    async def _search_source(self, source: str, query: str, top_k: int) -> List[Dict]:
        """개별 소스 검색 — 로컬 구현"""
        papers = []

        if source == "arxiv":
            try:
                import xml.etree.ElementTree as ET
                # arXiv API v2
                encoded = urllib.parse.quote(query)
                url = f"http://export.arxiv.org/api/query?search_query=all:{encoded}&max_results={top_k}&sortBy=relevance"
                req = urllib.request.Request(url, headers={"User-Agent": "Hermes3PKM/1.0"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    xml_data = resp.read().decode("utf-8")
                root = ET.fromstring(xml_data)
                ns = {"a": "http://www.w3.org/2005/Atom"}
                for entry in root.findall("a:entry", ns):
                    title = entry.find("a:title", ns)
                    summary = entry.find("a:summary", ns)
                    published = entry.find("a:published", ns)
                    arxiv_id = entry.find("a:id", ns)
                    author_names = []
                    for au in entry.findall("a:author", ns):
                        name_el = au.find("a:name", ns)
                        if name_el is not None and name_el.text:
                            author_names.append(name_el.text)
                    papers.append({
                        "title": (title.text or "").strip().replace("\n", " ") if title is not None else "",
                        "abstract": (summary.text or "").strip().replace("\n", " ") if summary is not None else "",
                        "published": (published.text or "").strip()[:10] if published is not None else "",
                        "arxiv_id": (arxiv_id.text or "").strip().split("/")[-1] if arxiv_id is not None else "",
                        "authors": ", ".join(author_names),
                        "source": "arxiv",
                        "type": "web_paper",
                    })
            except Exception as e:
                log.warning(f"[Orch] arXiv API 오류: {e}")

        elif source == "semantic_scholar":
            try:
                encoded = urllib.parse.quote(query)
                url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={encoded}&limit={top_k}&fields=title,abstract,publicationDate,authors,externalIds"
                req = urllib.request.Request(url, headers={"User-Agent": "Hermes3PKM/1.0"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                for paper in data.get("data", []):
                    authors = [au.get("name", "") for au in paper.get("authors", []) if au.get("name")]
                    papers.append({
                        "title": paper.get("title", ""),
                        "abstract": paper.get("abstract", "") or "",
                        "published": (paper.get("publicationDate") or "")[:10],
                        "arxiv_id": paper.get("externalIds", {}).get("ArXiv", ""),
                        "authors": ", ".join(authors[:10]),
                        "source": "semantic_scholar",
                        "type": "web_paper",
                    })
            except Exception as e:
                log.warning(f"[Orch] Semantic Scholar API 오류: {e}")

        return papers

    # ── local_semantic_search ────────────────────────────────────────

    async def _local_semantic_search(self, step: Dict) -> Dict[str, Any]:
        """기존 HybridKnowledgeIndexer의 FTS5 + 벡터 검색 활용"""
        query = step["query"]
        top_k = step.get("top_k", 5)
        scope = step.get("scope", "notes")  # notes / all

        indexer = _get_indexer()

        # 기존 하이브리드 검색 활용
        fts5_results = indexer._fts5_search(query, top_k=top_k)
        vec_results = indexer._vec_search(query, top_k=top_k)
        merged = indexer._rrf_merge(fts5_results, vec_results)

        notes = []
        for r in merged[:top_k]:
            doc_path = r.get("doc_path", "")
            title_val = r.get("heading")
            if not title_val:
                title_val = Path(doc_path).stem if doc_path else ""
                
            file_date = ""
            if doc_path and Path(doc_path).exists():
                import os
                from datetime import datetime
                file_date = datetime.fromtimestamp(os.path.getmtime(doc_path)).strftime("%Y-%m-%d")

            notes.append({
                "title": title_val,
                "doc_path": doc_path,
                "content": r.get("content", "")[:500],
                "source": "local_note",
                "type": "local_note",
                "score": r.get("rrf_score", r.get("score", 0)),
                "date": file_date,
            })

        return {"notes": notes, "query": query, "scope": scope}

    # ── merge_timeline ───────────────────────────────────────────────

    async def _merge_timeline(self, step: Dict, all_results: Dict) -> Dict[str, Any]:
        """여러 step 결과를 시간순 타임라인으로 병합"""
        input_ids = step.get("inputs", [])
        items = []
        for rid in input_ids:
            data = all_results.get(rid, {})
            if isinstance(data, dict):
                # web_search_multi 결과
                papers = data.get("papers", [])
                items.extend(papers)
                # local_semantic_search 결과
                notes = data.get("notes", [])
                items.extend(notes)

        merge_fn = _get_timeline()
        timeline = merge_fn(items)

        return {
            "timeline": timeline,
            "total_items": len(timeline),
            "sources": list(set(item.get("source", "unknown") for item in timeline)),
        }

    # ── cross_reference ──────────────────────────────────────────────

    async def _cross_reference(self, step: Dict, all_results: Dict) -> Dict[str, Any]:
        """노트-논문 간 교차 분석"""
        user_note_input = step.get("user_note_input", "")
        web_paper_input = step.get("web_paper_input", "")

        user_notes = all_results.get(user_note_input, {}).get("notes", [])
        web_papers = all_results.get(web_paper_input, {}).get("papers", [])

        ref_fn = _get_cross_ref()
        insights = ref_fn(user_notes, web_papers)

        return {
            "insights": insights,
            "note_count": len(user_notes),
            "paper_count": len(web_papers),
            "high_confidence": len([i for i in insights if i.get("confidence", 0) > 0.7]),
        }

    # ── summarize_insights ───────────────────────────────────────────

    async def _summarize_insights(self, step: Dict, all_results: Dict) -> Dict[str, Any]:
        """교차 분석 결과를 LLM으로 요약"""
        input_id = step.get("input", "")
        data = all_results.get(input_id, {})
        insights = data.get("insights", [])

        # recipe_summary 모드: 전체 레시피 실행 문맥도 포함
        recipe_summary = step.get("recipe_summary", False)

        if not insights:
            return {"summary_text": "특별한 교차 인사이트가 발견되지 않았습니다.", "insight_count": 0}

        # 간단한 규칙 기반 요약 (LLM 호출 없이)
        lines = []
        for ins in insights:
            align = ins.get("alignment_type", "match")
            conf = ins.get("confidence", 0) * 100
            note = ins.get("note_title", "내 노트")
            paper = ins.get("paper_title", "웹 논문")
            if align == "predict_and_realize":
                lines.append(f"🔮 내 노트가 {paper}(을)를 {conf:.0f}% 예측했습니다.")
            elif align == "retrospective_match":
                lines.append(f"🔗 내 노트({note})가 {paper}와 {conf:.0f}% 일치합니다.")
            else:
                lines.append(f"🔗 {note} ↔ {paper}: 유사도 {conf:.0f}%")

        summary_text = "\n".join(lines) if lines else "교차 분석 결과 의미 있는 연결이 없습니다."

        # LLM 요약 (선택적)
        if recipe_summary and lines:
            try:
                call_llm = await _get_llm()
                prompt = (
                    "You are a research insight generator. Given the following cross-reference findings "
                    "between user's local notes and web papers, write a concise 2-3 sentence summary "
                    "in Korean that highlights the most important connections.\n\n"
                    f"Findings:\n{summary_text}"
                )
                llm_summary = await call_llm(prompt)
                summary_text = llm_summary
            except Exception as e:
                log.debug(f"[Orch] LLM 요약 실패 (규칙 기반 폴백): {e}")

        return {
            "summary_text": summary_text,
            "insight_count": len(insights),
            "rule_based_lines": lines,
        }

    # ── 캐시 유틸리티 ────────────────────────────────────────────────

    def _cache_key(self, step: Dict) -> str:
        return hashlib.md5(json.dumps(step, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def _check_cache(self, key: str) -> Optional[Any]:
        if key in self.cache:
            data, ts = self.cache[key]
            if (datetime.now() - ts).total_seconds() < self.cache_ttl:
                return data
        return None

    def _set_cache(self, key: str, data: Any):
        self.cache[key] = (data, datetime.now())

        # LRU: 128개 초과 시 오래된 항목 제거
        if len(self.cache) > 128:
            oldest = min(self.cache.keys(), key=lambda k: self.cache[k][1])
            del self.cache[oldest]


# 싱글톤
orchestrator = KnowledgeMeshOrchestrator()
