"""Test claude_brief generation - full output"""
import sys
sys.path.insert(0, '/Users/bluesea/Applications/Mjauto/Scripts/modules')
sys.path.insert(0, '/Users/bluesea/Applications/Mjauto/Scripts')

from handlers._meta import _generate_briefing
content, success_count, missing = _generate_briefing()
print(content)
