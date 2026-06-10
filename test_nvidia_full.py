import asyncio
import os
import sys

sys.path.insert(0, "/Users/bluesea/Applications/Mjauto/Scripts")
sys.path.insert(0, "/Users/bluesea/Applications/Mjauto/Scripts/modules")

from harness_agent import _call_nvidia, get_sys_prompt
import harness_agent

async def main():
    messages = [
        {"role": "system", "content": get_sys_prompt()},
        {"role": "user", "content": "오늘 서울 날씨 어때?"}
    ]
    try:
        # Mocking the get_current_mode to force NVIDIA
        harness_agent.get_current_mode = lambda: harness_agent.MODE_NVIDIA
        ans, engine = await harness_agent.get_llm_response(messages)
        print("First ans:", ans)
        
        import re
        executed = False; exec_results = []
        m_search = re.search(r"\[SEARCH:\s*(.*?)\]", ans)
        if m_search:
            print("Found SEARCH tool:", m_search.group(1))
            try:
                if "/Users/bluesea/hermes" not in sys.path:
                    sys.path.append("/Users/bluesea/hermes")
                from web_agent_module import search_web
                search_res = await search_web(m_search.group(1).strip())
                exec_results.append(f"[🔍 SEARCH 결과]\n{search_res}"); executed = True
            except Exception as e:
                print("Search error:", e)
        if executed:
            messages.append({"role": "assistant", "content": ans})
            messages.append({"role": "user", "content": "\n".join(exec_results)})
            ans2, engine2 = await harness_agent.get_llm_response(messages)
            print("Final ans:", ans2)
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    asyncio.run(main())
