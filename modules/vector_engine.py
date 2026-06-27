# modules/vector_engine.py
# TurboVecLight + VectorIndexManager — bio_memory_engine.py에서 분리 (2026-06-23 SRP)

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("VectorEngine")

VECTOR_BACKEND = "hybrid"  # "npu" | "turbovec" | "hybrid"
VECTOR_BACKEND_CONFIG_PATH = Path("/Users/bluesea/.hermes/memory/vector_backend.json")


def get_vector_backend() -> str:
    if VECTOR_BACKEND_CONFIG_PATH.exists():
        try:
            cfg = json.loads(VECTOR_BACKEND_CONFIG_PATH.read_text(encoding="utf-8"))
            return cfg.get("backend", VECTOR_BACKEND)
        except Exception:
            pass
    return VECTOR_BACKEND


def set_vector_backend(backend: str):
    global VECTOR_BACKEND
    if backend not in ("npu", "turbovec", "hybrid"):
        logger.warning(f"⚠️ 지원하지 않는 벡터 백엔드: {backend}")
        return
    VECTOR_BACKEND = backend
    VECTOR_BACKEND_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    VECTOR_BACKEND_CONFIG_PATH.write_text(
        json.dumps({"backend": backend, "updated_at": datetime.now().isoformat()}, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info(f"⚡ [VectorEngine] 백엔드 전환: {backend}")


class TurboVecLight:
    """numpy 기반 경량 벡터 인덱스."""

    def __init__(self, dim: int = 384, index_path: Path = None):
        self.dim = dim
        self.index_path = index_path or Path("/Users/bluesea/.hermes/memory/turbovec_index.npy")
        self.keys_path = self.index_path.with_suffix(".keys.json")
        self.vectors: np.ndarray = np.empty((0, dim), dtype=np.float32)
        self.keys: list = []
        self._load()

    def _load(self):
        if self.index_path.exists():
            try:
                self.vectors = np.load(str(self.index_path))
            except Exception:
                self.vectors = np.empty((0, self.dim), dtype=np.float32)
        if self.keys_path.exists():
            try:
                self.keys = json.loads(self.keys_path.read_text(encoding="utf-8"))
            except Exception:
                self.keys = []

    def _save(self):
        np.save(str(self.index_path), self.vectors)
        self.keys_path.write_text(json.dumps(self.keys, ensure_ascii=False), encoding="utf-8")

    def add(self, key: str, vector: list):
        vec = np.array(vector, dtype=np.float32).reshape(1, -1)
        existing = [i for i, k in enumerate(self.keys) if k == key]
        if existing:
            self.vectors[existing[0]] = vec
        else:
            self.vectors = np.vstack([self.vectors, vec]) if self.vectors.size else vec
            self.keys.append(key)
        self._save()

    def search(self, query_vec: list, top_k: int = 5) -> list:
        if self.vectors.shape[0] == 0:
            return []
        qv = np.array(query_vec, dtype=np.float32).reshape(1, -1)
        norms = np.linalg.norm(self.vectors, axis=1, keepdims=True)
        q_norm = np.linalg.norm(qv)
        if q_norm == 0:
            return []
        sims = (self.vectors @ qv.T).flatten() / (norms.flatten() * q_norm + 1e-8)
        sims = np.nan_to_num(sims, nan=0.0)
        top_idx = np.argsort(sims)[::-1][:top_k]
        return [(self.keys[i], float(sims[i])) for i in top_idx if i < len(self.keys)]

    def remove(self, key: str):
        idx = [i for i, k in enumerate(self.keys) if k == key]
        if idx:
            i = idx[0]
            self.keys.pop(i)
            self.vectors = np.delete(self.vectors, i, axis=0)
            self._save()

    def size(self) -> int:
        return len(self.keys)


class VectorIndexManager:
    """NPU / turbovec / hybrid 통합 벡터 인덱스 매니저."""

    ALLOWLIST = {
        "에러", "버그", "오류", "수정", "복구", "긴급",
        "설정", "경로", "포트", "토큰", "API", "PATH",
        "승인", "결정", "규칙", "기억", "중요",
        "확정", "완료", "배포", "실패", "치명",
        "auto-skill", "Curator", "Honcho", "Dreaming", "turbovec",
    }

    def __init__(self, sem_engine=None, dim: int = 384):
        self.sem_engine = sem_engine
        self.dim = dim
        self.turbovec = TurboVecLight(dim=dim)

    def get_embedding(self, text: str) -> Optional[list]:
        backend = get_vector_backend()
        if backend in ("npu", "hybrid") and self.sem_engine:
            try:
                return self.sem_engine.get_embedding(text)
            except Exception as e:
                logger.warning(f"⚠️ [VectorIndex] NPU 임베딩 실패: {e}")
        return None

    def add_to_index(self, key: str, text: str):
        backend = get_vector_backend()
        if backend in ("turbovec", "hybrid") and self.sem_engine:
            try:
                vec = self.sem_engine.get_embedding(text)
                if vec:
                    self.turbovec.add(key, vec)
            except Exception:
                pass

    def search(self, query: str, top_k: int = 5) -> list:
        if not self.sem_engine:
            return []
        query_vec = self.sem_engine.get_embedding(query)
        if not query_vec:
            return []
        backend = get_vector_backend()
        results = []
        if backend in ("turbovec", "hybrid"):
            results.extend(self.turbovec.search(query_vec, top_k=top_k * 2))
        filtered = []
        for key, score in results:
            multiplier = 1.0 if any(kw in key.lower() for kw in self.ALLOWLIST) else 0.3
            filtered.append((key, score * multiplier))
        filtered.sort(key=lambda x: x[1], reverse=True)
        return filtered[:top_k]

    def is_allowlisted(self, key: str) -> bool:
        return any(kw in key.lower() for kw in self.ALLOWLIST)


def init_vector_index(engine) -> Optional[VectorIndexManager]:
    """BioMemoryEngine에 VectorIndexManager 연결."""
    if engine.sem_engine:
        vim = VectorIndexManager(sem_engine=engine.sem_engine)
        engine.vim = vim
        backend = get_vector_backend()
        logger.info(f"⚡ [VectorIndex] 초기화 완료 (백엔드: {backend})")
        return vim
    logger.warning("⚠️ [VectorIndex] SemanticEngine 없음 — VectorIndex 미초기화")
    return None


def auto_bind_vim(engine) -> None:
    """BioMemoryEngine 인스턴스에 VectorIndexManager 자동 바인딩."""
    if engine.sem_engine and engine.vim is None:
        try:
            engine.vim = VectorIndexManager(sem_engine=engine.sem_engine)
            logger.info("⚡ [Bio-Memory] VectorIndexManager 자동 바인딩 완료")
            engine._strip_inline_embeddings()
        except Exception as e:
            logger.warning(f"[Bio-Memory] vim 자동 바인딩 실패: {e}")
