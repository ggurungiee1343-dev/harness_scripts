import os
import sqlite3
import json
import openai
from pathlib import Path
from datetime import datetime

from fastembed import TextEmbedding

class SemanticEngine:
    def __init__(self, vault_path, db_path=None):
        self.vault_path = Path(vault_path)
        self.db_path = db_path or self.vault_path / "wiki" / "00_Meta" / "semantic_index.db"
        # Phase 5: MPNet Multilingual 모델로 업그레이드 (E5-Large 오류 대응 및 안정성 확보)
        self.model = TextEmbedding("sentence-transformers/paraphrase-multilingual-mpnet-base-v2")
        self.init_db()

    def init_db(self):
        """벡터 DB 및 FTS5(전체 텍스트 검색) 테이블 초기화"""
        os.makedirs(self.db_path.parent, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        # 1. 문서 메타데이터 및 임베딩 저장
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT UNIQUE,
                content TEXT,
                embedding BLOB,
                last_updated TIMESTAMP
            )
        ''')
        # 2. 고속 키워드 검색을 위한 FTS5 가상 테이블
        cursor.execute('''
            CREATE VIRTUAL TABLE IF NOT EXISTS fts_docs USING fts5(
                path, content, content='documents', content_rowid='id'
            )
        ''')
        # FTS5 트리거 설정 (데이터 동기화)
        cursor.execute("DROP TRIGGER IF EXISTS fts_insert")
        cursor.execute("CREATE TRIGGER fts_insert AFTER INSERT ON documents BEGIN INSERT INTO fts_docs(rowid, path, content) VALUES (new.id, new.path, new.content); END")
        
        conn.commit()
        conn.close()

    def get_embedding(self, text):
        """FastEmbed를 사용하여 임베딩 생성 (NPU 가속)"""
        try:
            embeddings = list(self.model.embed([text]))
            return embeddings[0].tolist()
        except Exception as e:
            return None

    def index_vault(self):
        """옵시디언 전체 문서를 스캔하고 인덱싱"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 기존 FTS 데이터 클리어 (재인덱싱 시)
        cursor.execute("DELETE FROM documents")
        cursor.execute("DELETE FROM fts_docs")
        
        md_files = list(self.vault_path.glob("**/*.md"))
        indexed_count = 0

        for md_file in md_files:
            if "00_Meta" in str(md_file): continue
            
            rel_path = str(md_file.relative_to(self.vault_path))
            try:
                with open(md_file, "r", encoding="utf-8") as f:
                    content = f.read()
                
                embedding = self.get_embedding(content[:2000])
                if embedding:
                    embedding_blob = json.dumps(embedding).encode('utf-8')
                    cursor.execute('''
                        INSERT INTO documents (path, content, embedding, last_updated)
                        VALUES (?, ?, ?, ?)
                    ''', (rel_path, content, embedding_blob, datetime.now().isoformat()))
                    indexed_count += 1
            except Exception as e:
                print(f"⚠️ {rel_path} 읽기 실패: {e}")
        
        conn.commit()
        conn.close()
        return f"✅ 하이브리드 인덱싱 완료: {indexed_count}개 문서 등록"

    def search(self, query, top_k=5):
        """RRF 기반 하이브리드 검색 (Dense + Sparse)"""
        import numpy as np
        query_vec = self.get_embedding(query)
        if not query_vec: return "❌ 검색 실패: 임베딩 생성 불가"

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 1. Dense 검색 (Vector Similarity)
        cursor.execute("SELECT id, path, content, embedding FROM documents")
        dense_results = []
        for doc_id, path, content, emb_blob in cursor.fetchall():
            doc_vec = json.loads(emb_blob.decode('utf-8'))
            score = np.dot(query_vec, doc_vec) / (np.linalg.norm(query_vec) * np.linalg.norm(doc_vec))
            dense_results.append((doc_id, path, content, score))
        dense_results.sort(key=lambda x: x[3], reverse=True)

        # 2. Sparse 검색 (FTS5 Keyword)
        # 키워드 매칭을 위해 쿼리에서 공백 기준 단어 추출
        keywords = " OR ".join(query.split())
        cursor.execute("SELECT rowid, rank FROM fts_docs WHERE fts_docs MATCH ? ORDER BY rank", (keywords,))
        sparse_results = cursor.fetchall()

        # 3. RRF (Reciprocal Rank Fusion) 결합
        # 점수가 아닌 '순위'를 기반으로 합산 (k=60은 표준 상수)
        scores = {}
        doc_map = {}
        
        for rank, (doc_id, path, content, _) in enumerate(dense_results):
            scores[doc_id] = scores.get(doc_id, 0) + 1 / (60 + rank + 1)
            doc_map[doc_id] = (path, content)
            
        for rank, (doc_id, _) in enumerate(sparse_results):
            scores[doc_id] = scores.get(doc_id, 0) + 1 / (60 + rank + 1)

        # 최종 순위 정렬
        final_rank = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        
        output = []
        for doc_id, score in final_rank[:top_k]:
            path, content = doc_map[doc_id]
            snippet = content[:150].replace("\n", " ")
            output.append((path, snippet, score))

        conn.close()
        return output

if __name__ == "__main__":
    # 테스트 코드
    engine = SemanticEngine("/Users/bluesea/Applications/Mjobsidian")
    # print(engine.index_vault()) # 최초 실행 시 주석 해제하여 인덱싱 진행
