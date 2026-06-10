import os
import re
from datetime import datetime

class LogicEngine:
    def __init__(self, vault_path, modules=None):
        self.vault_path = vault_path
        self.modules = modules or {}
        self.memory_file = os.path.join(self.vault_path, "wiki", "00_Meta", "memory.md")
        self.rules = []
        self.load_rules()
        
        self.semantic_instance = None

    def get_semantic(self):
        """세만틱 엔진 지연 로드"""
        if self.semantic_instance is None:
            try:
                try:
                    from MacBot.semantic_engine import SemanticEngine
                except ImportError:
                    from semantic_engine import SemanticEngine
                self.semantic_instance = SemanticEngine(self.vault_path)
                print("✅ LogicEngine: 세만틱 엔진(MPNet) 지연 로드 완료")
            except Exception as e:
                print(f"⚠️ LogicEngine: 세만틱 엔진 로드 실패: {e}")
        return self.semantic_instance

    def load_rules(self):
        """memory.md에서 [RULE]로 시작하는 규칙들을 로드"""
        if not os.path.exists(self.memory_file):
            return
        
        try:
            with open(self.memory_file, "r", encoding="utf-8") as f:
                for line in f:
                    if "[RULE]" in line:
                        # 예: - [RULE] if "AI" in title -> folder: /wiki/20_Research/AI
                        self.rules.append(line.strip())
        except Exception as e:
            print(f"Error loading rules: {e}")

    def process_message(self, text):
        """메시지를 분석하여 적절한 응답이나 동작을 반환"""
        
        # 1. 시스템 상태 체크 (@상태)
        if "@상태" in text or "상태" in text:
            if "system_monitor" in self.modules:
                _, msg = self.modules["system_monitor"].check_health()
                return f"🖥️ **맥스튜디오 상태 보고**\n\n{msg}"
            return "⚠️ 시스템 모니터 모듈이 로드되지 않았습니다."

        # 2. 뉴스 브리핑 (@뉴스)
        if "@뉴스" in text or "뉴스" in text:
            if "news_engine" in self.modules:
                return f"📰 **최신 기술 브리핑**\n\n{self.modules['news_engine'].get_latest_ai_news()}"
            return "⚠️ 뉴스 엔진 모듈이 로드되지 않았습니다."

        # 3. 기억 저장 (@기억)
        if text.startswith("@기억"):
            content = text.replace("@기억", "").strip()
            if "memory_engine" in self.modules:
                return self.modules["memory_engine"].save_important("memory.md", content)
            return "⚠️ 메모리 엔진 모듈이 로드되지 않았습니다."

        # 4. 정리 (@정리)
        if "@정리" in text:
            if "memory_engine" in self.modules:
                # history_data는 호출부에서 전달받아야 하지만, 일단 기본 호출
                return self.modules["memory_engine"].dream([])
            return "⚠️ 메모리 엔진 모듈이 로드되지 않았습니다."

        # 5. 파일 목록 조회 (리스트, 목록, 보여줘)
        if any(kw in text for kw in ["리스트", "목록", "보여줘", "ls"]):
            # 경로 추출 시도 (간단한 공백 분리 및 wiki/ 시작 경로 찾기)
            path_match = re.search(r"(wiki/[^\s]+|/Users/[^\s]+)", text)
            target_path = path_match.group(1).strip() if path_match else "wiki"
            
            if "file_manager" in self.modules:
                res = self.modules["file_manager"].list_files(target_path)
                return f"📂 **파일 목록 조회: {target_path}**\n\n{res}"
            return "⚠️ 파일 관리자 모듈이 로드되지 않았습니다."

        # 6. 학습 및 습득 (습득해, 학습해)
        if any(kw in text for kw in ["습득", "학습"]):
            path_match = re.search(r"(wiki/[^\s]+|/Users/[^\s]+)", text)
            if path_match:
                target_path = path_match.group(1).strip()
                if "file_manager" in self.modules:
                    content = self.modules["file_manager"].read_file(target_path)
                    # [RULE] 태그 추출
                    new_rules = re.findall(r"\[RULE\]\s*(.*)", content)
                    if new_rules:
                        self.rules.extend(new_rules)
                        # memory.md에도 백업
                        if "memory_engine" in self.modules:
                            for r in new_rules:
                                self.modules["memory_engine"].save_important("memory.md", f"[습득된 규칙] {r}")
                        return f"🧠 **습득 완료: {target_path}**\n\n총 {len(new_rules)}개의 새로운 규칙을 로직 엔진에 등록했습니다.\n\n" + "\n".join([f"- {r}" for r in new_rules])
                    return f"📄 **내용 분석 완료**: {target_path}에서 추출할 수 있는 구체적인 [RULE]이 발견되지 않았습니다. 대화용 지식 습득은 하네스(LLM)에게 요청해 주세요."
                return "⚠️ 파일 관리자 모듈이 로드되지 않았습니다."

        # 7. 파일 내용 읽기 (읽어줘, 확인해)
        if any(kw in text for kw in ["읽어", "확인", "내용"]):
            path_match = re.search(r"(wiki/[^\s]+|/Users/[^\s]+)", text)
            if path_match:
                target_path = path_match.group(1).strip()
                if "file_manager" in self.modules:
                    res = self.modules["file_manager"].read_file(target_path)
                    return f"📄 **파일 내용 읽기: {target_path}**\n\n{res[:4000]}"
                return "⚠️ 파일 관리자 모듈이 로드되지 않았습니다."

        # 7. 세만틱 분석 (의미 기반 매칭 - Phase 1)
        # 키워드 매칭 실패 시 의미적 유사도를 판단합니다.
        semantic_res = self.get_semantic_match(text)
        if semantic_res:
            return semantic_res

        # 9. 기본 응답
        return self.get_default_response(text)

    def get_semantic_match(self, text):
        """키워드가 정확히 일치하지 않아도 의미적으로 유사한 명령어를 찾아 실행"""
        import difflib
        
        # [의도 : 관련 키워드 세트] 정의
        intents = {
            "status": ["컨디션", "어때", "상태", "점검", "하드웨어", "건강"],
            "news": ["브리핑", "소식", "트렌드", "동향", "요즘", "기사"],
            "list": ["어디", "파일", "폴더", "구조", "ls", "트리"],
            "read": ["내용", "본문", "체크", "봐줘", "훑어"]
        }

        # 1. 의도별 점수 계산
        for intent, keywords in intents.items():
            for kw in keywords:
                if kw in text:
                    # 의미가 일치하는 의도를 찾으면 해당 로직 실행
                    if intent == "status":
                        return self.process_message("@상태")
                    if intent == "news":
                        return self.process_message("@뉴스")
                    if intent == "list":
                        return self.process_message("목록 보여줘")
                    if intent == "read":
                        return self.process_message("내용 확인해줘")

        # 2. 아주 유사한 문장인 경우 (difflib 활용)
        # 예: "맥 상태가 어떠니" -> "상태" 매칭
        return None

    def get_default_response(self, text):
        """매칭되는 명령어가 없을 때의 기본 처리"""
        # 간단한 인사 처리
        if any(kw in text for kw in ["안녕", "하이", "반갑", "반가"]):
            return "안녕하세요, MJ 박사님! 무엇을 도와드릴까요?"
        
        if "누구" in text:
            return "🤖 저는 박사님의 맥 스튜디오 전용 비서, **맥봇(MacBot)**입니다. 이제 세만틱 지능을 통해 박사님의 의도를 파악하기 시작했습니다."

        return f"❓ 말씀을 이해하지 못했습니다. 새로운 규칙으로 추가할까요? (@기억 사용)\n입력하신 내용: {text}"

if __name__ == "__main__":
    # 간단한 테스트
    engine = LogicEngine("/Users/bluesea/Applications/Mjobsidian")
    print(engine.process_message("@상태"))
    print(engine.process_message("안녕"))
