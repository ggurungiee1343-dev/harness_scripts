"""/research stats 시뮬레이션"""
import sys, json
sys.path.insert(0, '.')
from pathlib import Path
from modules.auto_topic_manager import topic_manager
from modules.knowledge_indexer import get_indexer

ix = get_indexer()
s = ix.get_stats()
t = topic_manager.get_topics()

print("=== ix.get_stats() ===")
for k,v in s.items():
    print(f"  {k}: {v}")

print()
print(f"=== topic_manager.get_topics() ===")
print(f"  개수: {len(t)}")
for topic in t:
    print(f"  - {topic['name']}: {', '.join(topic['keywords'][:3])}")

msg = (
    f"**Knowledge Mesh 상태**\n\n"
    f"**인덱서**\n"
    f"- 총 청크: {s.get('total_chunks', 0)}\n"
    f"- 총 문서: {s.get('total_docs', 0)}\n"
    f"- IDF 용어: {s.get('idf_terms', 0)}\n"
    f"- DB 경로: `{s.get('db_path', '?')}`\n\n"
    f"**주제 분류**\n"
    f"- 주제 수: {len(t)}\n"
    f"- DB 경로: `{topic_manager.topics_path}`"
)
print()
print("=== 메시지 출력 ===")
print(msg)
