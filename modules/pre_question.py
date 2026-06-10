from typing import Optional


class PreQuestion:
    def ask(self, text: str) -> Optional[str]:
        t = text.strip()
        if not t:
            return "질문이 비어 있습니다. 무엇을 도와드릴까요?"
        if "이거" in t or "그거" in t:
            return "대상이 모호합니다. 파일명, 링크, 문서명 중 하나를 지정해 주세요."
        if len(t) < 5:
            return "질문이 너무 짧습니다. 목적이나 대상, 원하는 결과를 한 줄 더 써 주세요."
        return None
