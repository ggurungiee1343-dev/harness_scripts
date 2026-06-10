import os
import asyncio

class CognitiveEngine:
    def __init__(self, workspace_path):
        self.workspace_path = workspace_path

    async def map_reduce_summarize(self, file_path, llm_func, chunk_lines=150):
        """
        긴 문서를 일정 단위로 쪼개어 각각 요약한 뒤, 마지막에 합치는(Map-Reduce) 함수.
        8B 모델의 Context Window 한계와 지시사항 누락 문제를 우회합니다.
        """
        if not os.path.isabs(file_path):
            file_path = os.path.join(self.workspace_path, file_path)

        if not os.path.exists(file_path):
            return f"❌ 파일을 찾을 수 없습니다: {file_path}"

        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        if len(lines) <= chunk_lines:
            # 문서가 짧으면 바로 요약
            content = "".join(lines)
            messages = [
                {"role": "system", "content": "주어진 문서를 누락 없이 핵심 위주로 상세히 요약하라."},
                {"role": "user", "content": content}
            ]
            ans, _ = await llm_func(messages)
            return f"📄 **[단일 요약 완료]**\n\n{ans}"

        # 1. Map 단계 (분할 요약)
        chunks = ["".join(lines[i:i + chunk_lines]) for i in range(0, len(lines), chunk_lines)]
        chunk_summaries = []
        
        for idx, chunk in enumerate(chunks):
            messages = [
                {"role": "system", "content": f"이 텍스트는 전체 문서의 파트 {idx+1}/{len(chunks)}입니다. 상세히 요약하세요."},
                {"role": "user", "content": chunk}
            ]
            ans, engine = await llm_func(messages)
            chunk_summaries.append(f"--- 파트 {idx+1} 요약 ---\n{ans}")
        
        # 2. Reduce 단계 (종합 요약)
        combined_text = "\n\n".join(chunk_summaries)
        final_messages = [
            {"role": "system", "content": "당신은 여러 파트의 요약본을 하나로 매끄럽게 합치고 구조화하는 전문가입니다. 중복을 제거하고 하나의 완벽한 글로 합성하세요."},
            {"role": "user", "content": combined_text}
        ]
        final_ans, final_engine = await llm_func(final_messages)

        return f"🧩 **[Map-Reduce 분할 요약 완료 (총 {len(chunks)}개 파트 결합)]**\n\n{final_ans}"

    def inject_persona_glossary(self, user_text):
        """
        특정 전문 도메인의 질문일 경우 프롬프트에 뉘앙스(용어집/제약사항)를 동적 주입.
        """
        injection = ""
        # 법학/특허 관련 뉘앙스 보정
        if any(keyword in user_text for keyword in ["법", "특허", "계약", "소송", "판례"]):
            injection += "\n[추가 지시사항: 이 질문은 법학/특허 도메인입니다. 반드시 격식 있는 법률 용어를 사용하고, 근거 없는 추측을 배제하며 단호한 어조로 작성할 것.]"
        
        # 코딩 관련 뉘앙스 보정
        if any(keyword in user_text for keyword in ["코드", "스크립트", "파이썬", "에러", "디버깅"]):
            injection += "\n[추가 지시사항: 코드를 제시할 때 불필요한 설명은 최소화하고, 수정된 부분 주석 처리 및 가독성을 최우선으로 할 것.]"

        return injection
