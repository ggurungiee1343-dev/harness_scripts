import os
import sys
import requests
from bs4 import BeautifulSoup

sys.path.append("/Users/bluesea/.hermes/plugins")
try:
    from hybrid_router import router
except ImportError:
    router = None

def fetch_url(url):
    """지정된 URL에서 텍스트를 추출 (최대 3000자)"""
    try:
        # User-Agent 설정
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        
        # 불필요한 태그 제거
        for script in soup(["script", "style", "header", "footer", "nav"]):
            script.extract()
            
        text = soup.get_text(separator=' ', strip=True)
        return text[:3000]
    except Exception as e:
        return f"웹 페이지를 읽을 수 없습니다. ({e})"

def analyze_url(url, question):
    """
    URL에서 텍스트를 추출한 뒤, LLM(hybrid_router)을 통해 
    사용자의 질문/요청(예: 요약해줘)에 맞게 분석하여 반환합니다.
    """
    if not router:
        return "⚠️ hybrid_router를 로드할 수 없어 분석 기능을 사용할 수 없습니다."
        
    web_text = fetch_url(url)
    
    if web_text.startswith("웹 페이지를 읽을 수 없습니다"):
        return web_text
        
    prompt = (
        f"다음은 웹 페이지({url})에서 추출한 텍스트입니다.\n"
        f"사용자의 요청에 맞춰 내용을 분석/요약하여 응답하세요.\n\n"
        f"[사용자 요청]: {question}\n\n"
        f"[웹 페이지 텍스트 (앞부분)]:\n{web_text}\n"
    )
    
    messages = [{"role": "user", "content": prompt}]
    ans, _ = router.send_completion(messages)
    return ans


class WebReader:
    """agentic_loop에서 WebReader().fetch_url() 형태로 호출하는 래퍼"""
    def fetch_url(self, url: str) -> str:
        return fetch_url(url)

    def analyze_url(self, url: str, question: str) -> str:
        return analyze_url(url, question)