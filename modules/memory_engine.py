import os
import json
from datetime import datetime

class MemoryEngine:
    def __init__(self, vault_path="/Users/bluesea/Applications/Mjobsidian"):
        self.vault_path = vault_path
        self.memory_file = os.path.join(self.vault_path, "wiki", "00_Meta", "memory.md")
        self.hot_file = os.path.join(self.vault_path, "wiki", "00_Meta", "hot.md")
        self.style_profile_file = os.path.join(self.vault_path, "wiki", "00_Meta", "style_profile.md")

    def save_important(self, key, val):
        """중요 정보를 memory.md에 기록"""
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            line = f"- [{timestamp}] {val}\n"
            os.makedirs(os.path.dirname(self.memory_file), exist_ok=True)
            with open(self.memory_file, "a", encoding="utf-8") as f:
                f.write(line)
            return f"✅ 중요 정보가 memory.md에 기록되었습니다."
        except Exception as e:
            return f"❌ 기억 저장 중 오류 발생: {e}"

    async def dream(self, history_data, llm_func=None):
        """단기 기억(history)과 hot.md를 기반으로 LLM을 사용하여 지능형 Dreaming 수행"""
        try:
            today_str = datetime.now().strftime("%Y.%m.%d")
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # 1. hot.md 읽기
            hot_content = ""
            if os.path.exists(self.hot_file):
                with open(self.hot_file, "r", encoding="utf-8") as f:
                    hot_content = f.read()

            if not hot_content.strip() and not history_data:
                return "⚠️ 처리할 hot.md 내용이나 최근 대화가 없습니다."

            if not llm_func:
                return "❌ LLM 함수가 제공되지 않아 지능형 Dreaming을 수행할 수 없습니다."

            # 대화 내역을 텍스트로 변환
            chat_text = "\n".join([f"{h['role']}: {h['content'][:200]}" for h in history_data[-10:]]) if history_data else "대화 내역 없음"

            # 2. LLM 프롬프트 작성
            sys_prompt = (
                "당신은 비서 '하네스'의 지식 정리 엔진입니다. 다음의 [최근 대화 내역]과 [hot.md 현재 내용]을 분석하여 "
                "반드시 아래의 3가지 키를 가진 JSON 형식으로 응답하십시오.\n\n"
                "{\n"
                '  "journal_entries": ["완료된 작업 항목 1", "완료된 작업 항목 2", ...],\n'
                '  "memory_updates": ["새로 기억해야 할 사용자 선호도나 시스템 규칙 1", ...],\n'
                '  "hot_remains": ["아직 진행 중이거나 앞으로 해야 할 작업 1", ...]\n'
                "}\n\n"
                "주의: markdown 코드 블록(```json ... ```) 없이 순수 JSON 문자열만 반환하십시오."
            )

            user_prompt = f"[최근 대화 내역]\n{chat_text}\n\n[hot.md 현재 내용]\n{hot_content}"

            # style_profile 주입
            if os.path.exists(self.style_profile_file):
                try:
                    with open(self.style_profile_file, "r", encoding="utf-8") as f:
                        sp = f.read()
                    if sp.strip():
                        user_prompt += f"\n\n[출력 스타일 가이드]\n{sp[:1500]}"
                except Exception:
                    pass
            
            prompt = [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt}
            ]

            llm_response, _ = await llm_func(prompt)
            
            # JSON 파싱 (코드 블록 정리)
            clean_res = llm_response.strip()
            if "```" in clean_res:
                clean_res = clean_res.split("```")[1]
                if clean_res.lower().startswith("json"):
                    clean_res = clean_res[4:]
            
            try:
                parsed_data = json.loads(clean_res.strip())
            except json.JSONDecodeError:
                return f"❌ LLM 응답을 JSON으로 파싱할 수 없습니다. 원본 응답:\n{llm_response}"

            journal_entries = parsed_data.get("journal_entries", [])
            memory_updates = parsed_data.get("memory_updates", [])
            hot_remains = parsed_data.get("hot_remains", [])

            summary_msg = []

            # 3. Journal 추가
            if journal_entries:
                journal_dir = os.path.join(self.vault_path, "wiki", "30_Journal")
                os.makedirs(journal_dir, exist_ok=True)
                journal_file = os.path.join(journal_dir, f"{today_str} 업무일지.md")
                
                with open(journal_file, "a", encoding="utf-8") as f:
                    f.write(f"\n## Dreaming 자동 기록 ({timestamp})\n")
                    for entry in journal_entries:
                        f.write(f"- {entry}\n")
                summary_msg.append(f"📝 Journal 추가: {len(journal_entries)}개 항목")

            # 4. memory.md 추가
            if memory_updates:
                os.makedirs(os.path.dirname(self.memory_file), exist_ok=True)
                with open(self.memory_file, "a", encoding="utf-8") as f:
                    f.write(f"\n## 자동 추출된 핵심 기억 ({timestamp})\n")
                    for update in memory_updates:
                        f.write(f"- {update}\n")
                summary_msg.append(f"💾 memory.md 갱신: {len(memory_updates)}개 항목")

            # 5. hot.md 정리 및 덮어쓰기
            with open(self.hot_file, "w", encoding="utf-8") as f:
                f.write(f"# 📝 프로젝트 핫토픽 (Dreaming 최신화)\n\n")
                f.write(f"**최종 업데이트:** {timestamp} (Harness Dreaming 자동 정리 완료)\n\n")
                f.write(f"## 📌 현재 진행 중인 작업\n")
                if hot_remains:
                    for remain in hot_remains:
                        f.write(f"- {remain}\n")
                else:
                    f.write("- 현재 대기 중인 작업이 없습니다.\n")
            summary_msg.append(f"🔥 hot.md 정리 완료 (잔여: {len(hot_remains)}개 항목)")

            msg_str = "\n".join(summary_msg)

            # style_profile 동기화 (Dreaming 사이클 종료 시)
            try:
                from modules.dialectic_layer import sync_style_profile
                sync_style_profile()
            except Exception:
                pass

            return f"🌙 **지능형 Dreaming 완료 ({today_str})**\n\n{msg_str}"

        except Exception as e:
            return f"❌ Dreaming 중 오류 발생: {e}"