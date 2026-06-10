"""Test claude_brief generation"""
import sys
sys.path.insert(0, '/Users/bluesea/Applications/Mjauto/Scripts/modules')
sys.path.insert(0, '/Users/bluesea/Applications/Mjauto/Scripts')

from handlers._meta import _generate_briefing
content, success_count, missing = _generate_briefing()
print(f'성공: {success_count}/6')
print(f'누락: {missing}')
print(f'크기: {len(content.encode("utf-8"))/1024:.1f}KB')
print(f'줄수: {len(content.split(chr(10)))}')
print()
print('=== 처음 800자 ===')
print(content[:800])
