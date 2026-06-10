"""
knowledge_indexer.py — HybridKnowledgeIndexer (v9.1.2)
=======================================================
llm_wiki 청킹 개념 기반 하이브리드 지식 검색 엔진

특징:
  1. H2(##) 헤더 경계 기반 마크다운 스마트 청킹
  2. SQLite FTS5 키워드 검색 (빠름, 낮은 정확도)
  3. sqlite-vec 없이 파이썬 기반 코사인 유사도 벡터 검색 (정확함)
  4. RRF (Reciprocal Rank Fusion) 병합
  5. Mjobsidian 위키 자동 인덱싱 지원

메모리 영향: < 50MB (캐시 포함)
부팅 영향: 0초 (지연 초기화)
"""

from __future__ import annotations

import os
import re
import json
import math
import sqlite3
import hashlib
import logging
import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

log = logging.getLogger("knowledge_indexer")

# ── 상수 ─────────────────────────────────────────────────────────────────
INDEX_DB_PATH = Path.home() / ".hermes" / "runtime" / "wiki_index.db"
WIKI_VAULT    = Path.home() / "Applications" / "Mjobsidian" / "wiki"
MAX_CHUNK_CHARS = 2000       # 청크 최대 글자 수
MIN_CHUNK_CHARS = 50         # 너무 짧은 청크 제외
TOP_K          = 5           # 각 검색에서 상위 N개
EMBED_DIM      = 64          # 경량 TF-IDF 벡터 차원 (외부 모델 없이 동작)
INDEX_TTL_SECS = 60 * 60 * 6  # 6시간마다 재인덱싱


class HybridKnowledgeIndexer:
    """
    llm_wiki 개념 기반 하이브리드 지식 검색
    SQLite FTS5(키워드) + TF-IDF 벡터(의미론) + RRF 병합
    """

    def __init__(self, db_path: Optional[str] = None, vault_path: Optional[str] = None):
        self.db_path   = Path(db_path) if db_path else INDEX_DB_PATH
        self.vault_path = Path(vault_path) if vault_path else WIKI_VAULT
        self._conn: Optional[sqlite3.Connection] = None
        self._idf_cache: Dict[str, float] = {}

    # ── DB 초기화 ─────────────────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """지연 초기화: 최초 호출 시 DB 연결 및 스키마 생성."""
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._create_schema()
        return self._conn

    def _create_schema(self):
        conn = self._conn
        conn.executescript("""
            -- 메인 청크 테이블
            CREATE TABLE IF NOT EXISTS chunks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_path    TEXT NOT NULL,
                chunk_idx   INTEGER NOT NULL,
                heading     TEXT,
                content     TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                word_count  INTEGER,
                indexed_at  REAL NOT NULL,
                UNIQUE(doc_path, chunk_idx)
            );

            -- FTS5 전문 검색 가상 테이블 (한국어/영어 혼합)
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
                USING fts5(content, heading, content=chunks, content_rowid=id);

            -- TF-IDF 경량 벡터 저장 (JSON 직렬화)
            CREATE TABLE IF NOT EXISTS chunk_vectors (
                chunk_id    INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
                vector_json TEXT NOT NULL
            );

            -- 인덱싱된 문서 메타데이터
            CREATE TABLE IF NOT EXISTS indexed_docs (
                doc_path    TEXT PRIMARY KEY,
                mtime       REAL NOT NULL,
                chunk_count INTEGER NOT NULL,
                indexed_at  REAL NOT NULL
            );

            -- FTS5 트리거 (INSERT/DELETE 동기화)
            CREATE TRIGGER IF NOT EXISTS chunks_fts_insert
                AFTER INSERT ON chunks BEGIN
                    INSERT INTO chunks_fts(rowid, content, heading)
                        VALUES (new.id, new.content, new.heading);
                END;

            CREATE TRIGGER IF NOT EXISTS chunks_fts_delete
                BEFORE DELETE ON chunks BEGIN
                    INSERT INTO chunks_fts(chunks_fts, rowid, content, heading)
                        VALUES ('delete', old.id, old.content, old.heading);
                END;
        """)
        conn.commit()

    # ── 마크다운 스마트 청킹 ──────────────────────────────────────────────

    def _smart_chunk_markdown(self, md_text: str) -> List[Dict[str, str]]:
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
                    sub_chunks = self._split_by_paragraph(text, current_heading)
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

    def _split_by_paragraph(self, text: str, heading: str) -> List[Dict[str, str]]:
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

    # ── 경량 TF-IDF 벡터 ─────────────────────────────────────────────────

    def _tokenize(self, text: str) -> List[str]:
        """한국어 + 영어 혼합 토크나이저 (공백/특수문자 분리)."""
        # 영어: 소문자, 한국어: 음절 단위, 숫자 포함
        tokens = re.findall(r"[가-힣]+|[a-z0-9]+", text.lower())
        # 1글자 영어 토큰 제외 (한국어는 유지)
        return [t for t in tokens if len(t) > 1 or re.match(r"[가-힣]", t)]

    def _compute_tf(self, tokens: List[str]) -> Dict[str, float]:
        """TF(Term Frequency) 계산."""
        if not tokens:
            return {}
        tf: Dict[str, float] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1
        total = len(tokens)
        return {t: count / total for t, count in tf.items()}

    def _update_idf_cache(self, conn: sqlite3.Connection):
        """전체 코퍼스 IDF 캐시 갱신 (배치 처리)."""
        try:
            rows = conn.execute("SELECT content FROM chunks").fetchall()
            if not rows:
                return

            doc_count = len(rows)
            df: Dict[str, int] = {}
            for row in rows:
                tokens = set(self._tokenize(row["content"]))
                for t in tokens:
                    df[t] = df.get(t, 0) + 1

            self._idf_cache = {
                t: math.log((doc_count + 1) / (count + 1)) + 1
                for t, count in df.items()
            }
        except Exception as e:
            log.debug(f"IDF 캐시 갱신 실패: {e}")

    def _tfidf_vector(self, text: str) -> Dict[str, float]:
        """TF-IDF 벡터 계산."""
        tokens = self._tokenize(text)
        tf = self._compute_tf(tokens)
        if not self._idf_cache:
            self.get_stats()
        if not self._idf_cache:
            return tf  # IDF 없으면 TF만 사용

        return {
            t: tf_val * self._idf_cache.get(t, 1.0)
            for t, tf_val in tf.items()
        }

    def _cosine_sim(self, v1: Dict[str, float], v2: Dict[str, float]) -> float:
        """희소 벡터 코사인 유사도."""
        common = set(v1) & set(v2)
        if not common:
            return 0.0
        dot = sum(v1[t] * v2[t] for t in common)
        norm1 = math.sqrt(sum(x * x for x in v1.values()))
        norm2 = math.sqrt(sum(x * x for x in v2.values()))
        return dot / (norm1 * norm2) if norm1 and norm2 else 0.0

    # ── 인덱싱 ───────────────────────────────────────────────────────────

    def index_document(self, doc_path: str, content: Optional[str] = None) -> int:
        """
        단일 마크다운 문서를 인덱싱.
        content가 None이면 파일에서 직접 읽음.
        Returns: 인덱싱된 청크 수
        """
        conn = self._get_conn()
        path_obj = Path(doc_path)

        # mtime 기반 변경 감지
        try:
            mtime = path_obj.stat().st_mtime
        except FileNotFoundError:
            return 0

        row = conn.execute(
            "SELECT mtime FROM indexed_docs WHERE doc_path = ?", (doc_path,)
        ).fetchone()
        if row and abs(row["mtime"] - mtime) < 1.0:
            # 변경 없음, 스킵
            return 0

        # 기존 청크 삭제
        conn.execute("DELETE FROM chunk_vectors WHERE chunk_id IN "
                     "(SELECT id FROM chunks WHERE doc_path = ?)", (doc_path,))
        conn.execute("DELETE FROM chunks WHERE doc_path = ?", (doc_path,))

        # 콘텐츠 로드
        if content is None:
            try:
                content = path_obj.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                log.warning(f"문서 읽기 실패: {doc_path} — {e}")
                return 0

        # 청킹
        raw_chunks = self._smart_chunk_markdown(content)
        now = time.time()
        inserted = 0

        for idx, chunk in enumerate(raw_chunks):
            text = chunk["content"]
            heading = chunk.get("heading", "")
            h = hashlib.md5(text.encode()).hexdigest()
            word_count = len(self._tokenize(text))

            try:
                cursor = conn.execute(
                    "INSERT OR REPLACE INTO chunks "
                    "(doc_path, chunk_idx, heading, content, content_hash, word_count, indexed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (doc_path, idx, heading, text, h, word_count, now)
                )
                chunk_id = cursor.lastrowid

                # TF-IDF 벡터 저장
                vec = self._tfidf_vector(f"{heading} {text}")
                conn.execute(
                    "INSERT OR REPLACE INTO chunk_vectors (chunk_id, vector_json) VALUES (?, ?)",
                    (chunk_id, json.dumps(vec, ensure_ascii=False))
                )
                inserted += 1
            except Exception as e:
                log.debug(f"청크 인덱싱 실패 [{doc_path}:{idx}]: {e}")

        # 문서 메타데이터 업데이트
        conn.execute(
            "INSERT OR REPLACE INTO indexed_docs (doc_path, mtime, chunk_count, indexed_at) "
            "VALUES (?, ?, ?, ?)",
            (doc_path, mtime, inserted, now)
        )
        conn.commit()

        # IDF 캐시 갱신
        self._update_idf_cache(conn)
        log.info(f"[Indexer] 인덱싱 완료: {path_obj.name} ({inserted}청크)")
        return inserted

    def index_vault(self, vault_path: Optional[str] = None, force: bool = False) -> Dict[str, int]:
        """
        Mjobsidian 위키 전체 인덱싱 (mtime 기반 증분 업데이트).
        Returns: {파일경로: 청크수} 딕셔너리
        """
        vault = Path(vault_path) if vault_path else self.vault_path
        results: Dict[str, int] = {}

        if not vault.exists():
            log.warning(f"[Indexer] 위키 경로 없음: {vault}")
            return results

        md_files = list(vault.rglob("*.md"))
        log.info(f"[Indexer] 위키 인덱싱 시작: {len(md_files)}개 파일")

        for md_file in md_files:
            # 숨김 파일, 보관 폴더 제외
            if any(p.startswith(".") for p in md_file.parts):
                continue
            if "Archive" in md_file.parts or "_archive" in md_file.parts:
                continue

            count = self.index_document(str(md_file))
            if count > 0:
                results[str(md_file)] = count

        log.info(f"[Indexer] 위키 인덱싱 완료: {len(results)}개 파일 업데이트")
        return results

    # ── FTS5 검색 ────────────────────────────────────────────────────────

    def _fts5_search(self, query: str, top_k: int = TOP_K) -> List[Dict[str, Any]]:
        """1단계: SQLite FTS5 전문 검색 (빠름, BM25 랭킹)."""
        conn = self._get_conn()

        # FTS5 특수문자 이스케이프
        safe_query = re.sub(r'[^\w\s가-힣]', ' ', query).strip()
        if not safe_query:
            return []

        try:
            rows = conn.execute(
                """
                SELECT c.id, c.doc_path, c.heading, c.content,
                       bm25(chunks_fts) AS score
                FROM chunks_fts
                JOIN chunks c ON chunks_fts.rowid = c.id
                WHERE chunks_fts MATCH ?
                ORDER BY score
                LIMIT ?
                """,
                (safe_query, top_k)
            ).fetchall()

            return [
                {
                    "id": r["id"],
                    "doc_path": r["doc_path"],
                    "heading": r["heading"],
                    "content": r["content"],
                    "score": abs(r["score"]),  # BM25는 음수
                    "source": "fts5"
                }
                for r in rows
            ]
        except Exception as e:
            log.debug(f"FTS5 검색 오류: {e}")
            return []

    # ── 벡터 검색 ────────────────────────────────────────────────────────

    def _vec_search(self, query: str, top_k: int = TOP_K) -> List[Dict[str, Any]]:
        """2단계: TF-IDF 코사인 유사도 검색 (의미론적)."""
        conn = self._get_conn()

        query_vec = self._tfidf_vector(query)
        if not query_vec:
            return []

        try:
            rows = conn.execute(
                "SELECT c.id, c.doc_path, c.heading, c.content, cv.vector_json "
                "FROM chunk_vectors cv JOIN chunks c ON cv.chunk_id = c.id"
            ).fetchall()
        except Exception as e:
            log.debug(f"벡터 검색 오류: {e}")
            return []

        scored: List[Tuple[float, Dict[str, Any]]] = []
        for row in rows:
            try:
                vec = json.loads(row["vector_json"])
                sim = self._cosine_sim(query_vec, vec)
                if sim > 0.0:
                    scored.append((sim, {
                        "id": row["id"],
                        "doc_path": row["doc_path"],
                        "heading": row["heading"],
                        "content": row["content"],
                        "score": sim,
                        "source": "vector"
                    }))
            except Exception:
                continue

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:top_k]]

    # ── RRF 병합 ────────────────────────────────────────────────────────

    def _rrf_merge(
        self,
        list1: List[Dict[str, Any]],
        list2: List[Dict[str, Any]],
        k: int = 60
    ) -> List[Dict[str, Any]]:
        """
        RRF (Reciprocal Rank Fusion) 알고리즘
        score = Σ 1 / (k + rank_i)
        중복(동일 chunk id)은 점수 합산.
        """
        rrf_scores: Dict[int, float] = {}
        items: Dict[int, Dict[str, Any]] = {}

        for rank, item in enumerate(list1, start=1):
            chunk_id = item["id"]
            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0) + 1 / (k + rank)
            items[chunk_id] = item

        for rank, item in enumerate(list2, start=1):
            chunk_id = item["id"]
            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0) + 1 / (k + rank)
            if chunk_id not in items:
                items[chunk_id] = item

        sorted_ids = sorted(rrf_scores, key=lambda x: rrf_scores[x], reverse=True)
        return [
            {**items[cid], "rrf_score": rrf_scores[cid]}
            for cid in sorted_ids
        ]

    # ── 결과 포맷팅 ──────────────────────────────────────────────────────

    def _format_results(self, chunks: List[Dict[str, Any]], max_chars: int = 3000) -> str:
        """검색 결과를 LLM 친화적 텍스트로 포맷팅."""
        if not chunks:
            return "(검색 결과 없음)"

        parts: List[str] = []
        total_chars = 0

        for i, chunk in enumerate(chunks, start=1):
            doc_name = Path(chunk["doc_path"]).stem
            heading = chunk.get("heading", "")
            content = chunk["content"]

            # 너무 긴 청크는 잘라서 표시
            if len(content) > 800:
                content = content[:800] + "..."

            header = f"[{i}] 📄 {doc_name}"
            if heading:
                header += f" / {heading}"

            entry = f"{header}\n{content}"
            entry_len = len(entry)

            if total_chars + entry_len > max_chars and parts:
                break

            parts.append(entry)
            total_chars += entry_len

        return "\n\n---\n\n".join(parts)

    # ── 메인 공개 API ─────────────────────────────────────────────────────

    async def search_hybrid(self, query: str, auto_index: bool = True) -> str:
        """
        하이브리드 검색: FTS5 키워드 + TF-IDF 벡터 → RRF 병합
        
        Args:
            query: 검색 쿼리 (한국어/영어 혼합 가능)
            auto_index: True이면 위키가 아직 인덱싱되지 않았을 때 자동 인덱싱
        
        Returns:
            LLM 친화적 텍스트 형태의 검색 결과
        """
        conn = self._get_conn()

        # 인덱스가 비어있으면 자동 인덱싱
        chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        if chunk_count == 0 and auto_index:
            log.info("[Indexer] 인덱스 비어있음. 위키 자동 인덱싱 시작...")
            self.index_vault()
            chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

        if chunk_count == 0:
            return "⚠️ 인덱싱된 문서가 없습니다. index_vault()를 먼저 실행하세요."

        # 1단계: FTS5 검색
        fts_results = self._fts5_search(query, top_k=TOP_K)

        # 2단계: 벡터 검색
        vec_results = self._vec_search(query, top_k=TOP_K)

        # 3단계: RRF 병합
        merged = self._rrf_merge(fts_results, vec_results)

        log.info(
            f"[Search] '{query[:30]}' — FTS5: {len(fts_results)}, "
            f"Vec: {len(vec_results)}, Merged: {len(merged)}"
        )

        return self._format_results(merged)

    def search_sync(self, query: str, auto_index: bool = True) -> str:
        """동기 버전 search_hybrid (비동기 환경이 아닐 때 사용)."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 이미 실행 중인 이벤트 루프에서는 직접 호출
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(
                        lambda: asyncio.run(self.search_hybrid(query, auto_index))
                    )
                    return future.result(timeout=30)
            else:
                return loop.run_until_complete(self.search_hybrid(query, auto_index))
        except Exception:
            return asyncio.run(self.search_hybrid(query, auto_index))

    # ── PKM_2: search_similar ────────────────────────────────────────────

    def search_similar(self, query: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """
        구조화된 검색 결과 반환 (PKM Orchestrator용).
        내부적으로 FTS5 + 벡터 + RRF를 수행하고 dict 리스트로 반환.
        """
        conn = self._get_conn()
        chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        if chunk_count == 0:
            return []

        fts_results = self._fts5_search(query, top_k=top_k)
        vec_results = self._vec_search(query, top_k=top_k)
        merged = self._rrf_merge(fts_results, vec_results)

        results = []
        for r in merged[:top_k]:
            doc_path = r.get("doc_path", "")
            results.append({
                "title": r.get("heading", Path(doc_path).stem) if doc_path else r.get("heading", ""),
                "doc_path": doc_path,
                "content": r.get("content", ""),
                "score": r.get("rrf_score", r.get("score", 0)),
                "source": "local_note",
                "type": "local_note",
            })
        return results

    def get_stats(self) -> Dict[str, Any]:
        """인덱서 통계 조회.

        _idf_cache가 비어있으면 DB에서 직접 IDF 용어 수를 계산한다.
        (봇 재시작 후 캐시가 초기화되어 IDF 용어가 0으로 표시되는 문제 방지)
        """
        conn = self._get_conn()
        chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        doc_count   = conn.execute("SELECT COUNT(*) FROM indexed_docs").fetchone()[0]

        if not self._idf_cache and chunk_count > 0:
            try:
                self._update_idf_cache(conn)
            except Exception as e:
                log.debug(f"get_stats: IDF 캐시 복원 실패: {e}")

        return {
            "total_chunks": chunk_count,
            "total_docs":   doc_count,
            "idf_terms":    len(self._idf_cache),
            "db_path":      str(self.db_path),
        }


# ── 싱글톤 인스턴스 (모듈 레벨에서 공유) ───────────────────────────────
_indexer_instance: Optional[HybridKnowledgeIndexer] = None


def get_indexer() -> HybridKnowledgeIndexer:
    """전역 싱글톤 인덱서 반환."""
    global _indexer_instance
    if _indexer_instance is None:
        _indexer_instance = HybridKnowledgeIndexer()
    return _indexer_instance
