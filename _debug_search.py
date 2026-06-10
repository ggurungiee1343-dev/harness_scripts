"""search_hybrid 직접 호출해서 빈 결과인지 확인"""
import sys, asyncio
sys.path.insert(0, '.')
from modules.knowledge_indexer import get_indexer

async def test():
    ix = get_indexer()
    print(f'IDF 캐시 크기: {len(ix._idf_cache)}')
    
    for q in ['헌법', 'AI 규제', '연구 방법론']:
        result = await ix.search_hybrid(q, auto_index=False)
        print(f'\n=== search_hybrid("{q}") ===')
        print(f'결과 길이: {len(result)} chars')
        print(f'첫 300자: {result[:300]}')

asyncio.run(test())
