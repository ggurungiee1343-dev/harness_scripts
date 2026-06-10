# modules/paper_ingest.py
# Hermes3 Phase 2 - 논문 인제스트 파이프라인
import os, sqlite3, hashlib
from typing import Dict, List, Optional


class PaperIngest:
    DB_PATH = os.path.expanduser("~/.hermes/runtime/hermes_index.db")
    PDF_CACHE = os.path.expanduser("~/.hermes/runtime/pdf_cache")

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or self.DB_PATH
        os.makedirs(self.PDF_CACHE, exist_ok=True)
        self._ensure_tables()

    def from_arxiv_id(self, arxiv_id: str) -> Dict:
        """arXiv ID로 논문 다운로드 및 인제스트"""
        try:
            import arxiv
        except ImportError:
            raise ImportError("pip install arxiv 필요")

        search = arxiv.Search(id_list=[arxiv_id])
        paper = next(search.results())

        pdf_path = os.path.join(self.PDF_CACHE, f"{arxiv_id}.pdf")
        if not os.path.exists(pdf_path):
            paper.download_pdf(dirpath=self.PDF_CACHE, filename=f"{arxiv_id}.pdf")

        metadata = {
            "arxiv_id": arxiv_id,
            "title": paper.title,
            "authors": ", ".join(a.name for a in paper.authors),
            "published": paper.published.date().isoformat(),
            "abstract": paper.summary,
            "pdf_path": pdf_path
        }

        text = self._extract_text(pdf_path)
        chunks = self._chunk_text(text)
        paper_id = self._save_to_db(metadata, chunks)
        metadata["paper_id"] = paper_id
        return metadata

    def from_local_pdf(self, pdf_path: str, metadata: Optional[Dict] = None) -> Dict:
        """로컬 PDF 파일 인제스트"""
        pdf_path = os.path.expanduser(pdf_path)
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        meta = metadata or {}
        meta.setdefault("arxiv_id", hashlib.md5(pdf_path.encode()).hexdigest()[:12])
        meta.setdefault("title", os.path.basename(pdf_path).replace(".pdf", ""))
        meta.setdefault("authors", "Unknown")
        meta.setdefault("published", "")
        meta.setdefault("abstract", "")
        meta["pdf_path"] = pdf_path

        text = self._extract_text(pdf_path)
        chunks = self._chunk_text(text)
        paper_id = self._save_to_db(meta, chunks)
        meta["paper_id"] = paper_id
        return meta

    def _extract_text(self, pdf_path: str) -> str:
        """PDF 텍스트 추출 (pdfplumber 우선, PyPDF2 fallback)"""
        try:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                return "\n".join(
                    page.extract_text() or "" for page in pdf.pages
                )
        except ImportError:
            pass
        try:
            import PyPDF2
            reader = PyPDF2.PdfReader(pdf_path)
            return "\n".join(
                page.extract_text() or "" for page in reader.pages
            )
        except ImportError:
            raise ImportError("pip install pdfplumber 또는 PyPDF2 필요")

    def _chunk_text(
        self, text: str, chunk_size: int = 500, overlap: int = 50
    ) -> List[str]:
        """단어 기반 청크 분할 (overlap 포함)"""
        words = text.split()
        chunks = []
        step = chunk_size - overlap
        for i in range(0, len(words), step):
            chunk = " ".join(words[i: i + chunk_size])
            if chunk:
                chunks.append(chunk)
        return chunks

    def _save_to_db(self, metadata: Dict, chunks: List[str]) -> int:
        """papers + paper_chunks 테이블에 저장"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            """INSERT OR REPLACE INTO papers
               (arxiv_id, title, authors, published, abstract, pdf_path)
               VALUES (?,?,?,?,?,?)""",
            (
                metadata["arxiv_id"],
                metadata["title"],
                metadata["authors"],
                metadata.get("published", ""),
                metadata.get("abstract", ""),
                metadata["pdf_path"],
            ),
        )
        paper_id = cursor.lastrowid
        for i, chunk in enumerate(chunks):
            conn.execute(
                "INSERT INTO paper_chunks (paper_id, chunk_index, text) VALUES (?,?,?)",
                (paper_id, i, chunk),
            )
        conn.commit()
        conn.close()
        return paper_id

    def _ensure_tables(self):
        """필요한 테이블 자동 생성"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS papers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                arxiv_id TEXT UNIQUE,
                title TEXT,
                authors TEXT,
                published TEXT,
                abstract TEXT,
                pdf_path TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_id INTEGER,
                chunk_index INTEGER,
                text TEXT,
                FOREIGN KEY (paper_id) REFERENCES papers(id)
            )
        """)
        conn.commit()
        conn.close()
