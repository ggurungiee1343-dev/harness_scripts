import os
import re
import sys
import subprocess
import logging
import unicodedata

# Scripts/ 디렉토리 기준 import (하향식)
from hybrid_router import router
from modules.wiki_manager import WikiManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CoveEngine")

class CoveEngine:
    def __init__(self):
        self.wiki = WikiManager(vault_path="/Users/bluesea/Applications/Mjobsidian")

    def local_wiki_search(self, query):
        """
        옵시디언 위키 내의 모든 마크다운 파일에서 검색어와 연관된 단락들을 찾아 반환합니다.
        """
        query_normalized = unicodedata.normalize('NFC', query).lower()
        keywords = [word for word in query_normalized.split() if len(word) > 1]
        
        wiki_dir = os.path.join(self.wiki.vault_path, "wiki")
        if not os.path.exists(wiki_dir):
            return "로컬 위키 경로가 존재하지 않습니다."

        matched_snippets = []
        
        for root, dirs, files in os.walk(wiki_dir):
            for file in files:
                if file.endswith(".md") and not file.startswith("."):
                    file_path = os.path.join(root, file)
                    rel_p = os.path.relpath(file_path, wiki_dir)
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            content = f.read()
                            paragraphs = content.split("\n\n")
                            for p in paragraphs:
                                p_norm = unicodedata.normalize('NFC', p).lower()
                                if any(kw in p_norm for kw in keywords):
                                    matched_snippets.append(f"출처: [{rel_p}]\n내용: {p.strip()}")
                    except Exception as e:
                        logger.error(f"파일 읽기 오류: {file_path} - {e}")
                        
        if matched_snippets:
            return "\n\n---\n\n".join(matched_snippets[:4])
        return "위키에서 관련된 직접적인 팩트 정보를 찾지 못했습니다."

    def run_cove_pipeline(self, user_question):
        """
        Meta CoVe 4단계 팩트체크 파이프라인 구동
        """
        logger.info(f"CoVe Pipeline started for question: {user_question}")
        pending_actions = []

        # 1. Baseline 작성
        logger.info("Step 1 Generating baseline response...")
        prompt_1 = f"당신은 똑똑하고 정확한 비서입니다. 질문에 대해 상세하게 답변해주세요.\n질문: {user_question}"
        messages = [{"role": "user", "content": prompt_1}]
        baseline_ans, engine_name = router.send_completion(messages)

        if baseline_ans.startswith("❌"):
            return baseline_ans, pending_actions

        # 2. 검증 질문 도출
        logger.info("Step 2 Formulating verification questions...")
        prompt_2 = (
            f"다음 답변의 사실 여부를 검증하기 위해 필요한 독립적인 단답형 질문 3가지를 도출하세요.\n\n"
            f"답변: {baseline_ans}"
        )
        messages = [{"role": "user", "content": prompt_2}]
        verification_questions, _ = router.send_completion(messages)

        # 3. 로컬 지식 기반 팩트 검색
        logger.info("Step 3 Retrieving facts from local wiki...")
        wiki_context = self.local_wiki_search(verification_questions)

        # 4. 최종 답변 도출
        logger.info("Step 4 Formulating final verified response...")
        prompt_4 = (
            f"다음은 사용자의 원본 질문, 초기 답변, 그리고 확인된 로컬 위키 검색 결과입니다.\n"
            f"위키 검색 결과를 바탕으로 초기 답변의 오류나 환각을 수정하여 최종 답변을 작성하세요.\n"
            f"분석 과정이나 설명은 제외하고, 사용자에게 전달할 깔끔한 최종 답변만 출력하세요.\n\n"
            f"[원본 질문]: {user_question}\n"
            f"[초기 답변]: {baseline_ans}\n"
            f"[위키 검색 결과]: {wiki_context}"
        )
        messages = [{"role": "user", "content": prompt_4}]
        final_ans, _ = router.send_completion(messages)

        # 5. 파일시스템 실존 검증 — 시스템 관련 주장 교차확인 (환각 방지)
        logger.info("Step 5 Filesystem grounding check...")
        SCRIPTS_DIR = "/Users/bluesea/Applications/Mjauto/Scripts"
        grounding_warnings = self._filesystem_grounding(final_ans, SCRIPTS_DIR)
        if grounding_warnings:
            warning_block = "\n\n---\n⚠️ **[CoVe 파일시스템 검증]** 아래 주장은 실제 코드에서 확인되지 않았습니다:\n"
            warning_block += "\n".join(f"  · {w}" for w in grounding_warnings)
            final_ans += warning_block
            logger.warning(f"[CoVe Step5] 검증 실패 항목: {grounding_warnings}")

        return final_ans, pending_actions

    def _filesystem_grounding(self, answer: str, scripts_dir: str) -> list:
        """Step 5: 답변에서 시스템 파일/기능 주장을 추출해 실존 여부를 grep으로 검증.

        '이 스크립트에서 X를 사용한다'는 주장이 실제 코드에 없으면 경고를 반환.
        과탐지 방지를 위해 명시적 파일명(.py/.sh)이 언급된 경우만 검사.
        """
        warnings = []

        # 패턴: "파일명.py/sh 에서 X 를 사용/호출/실행" 주장 추출
        file_claim_pattern = re.findall(
            r'([`"\']?([\w_\-]+\.(?:py|sh))[`"\']?)[^\n]*?(사용|호출|실행|포함|존재|있다|있음)',
            answer
        )

        for match in file_claim_pattern:
            filename = match[1]
            # 실제 파일 존재 여부
            result = subprocess.run(
                ["find", scripts_dir, "-name", filename],
                capture_output=True, text=True, timeout=5
            )
            found_paths = result.stdout.strip().splitlines()
            if not found_paths:
                warnings.append(f"`{filename}` — 파일이 실제로 존재하지 않음")
                continue

            # 파일이 존재하면 내용도 문맥에서 언급된 기술 키워드와 교차 검증
            # 기술 키워드: 답변에서 백틱으로 감싸진 단어들 추출
            tech_keywords = re.findall(r'`([a-zA-Z0-9_\-]{3,})`', answer)
            _IGNORE = {"python", "bash", "true", "false", "none", "null", "the", "and"}
            for kw in set(tech_keywords) - _IGNORE:
                if len(kw) < 4:
                    continue
                grep = subprocess.run(
                    ["grep", "-rl", kw, scripts_dir],
                    capture_output=True, text=True, timeout=5
                )
                # 언급된 파일명 중 실제로 kw가 없는 경우
                for fp in found_paths:
                    grep_in_file = subprocess.run(
                        ["grep", "-c", kw, fp],
                        capture_output=True, text=True, timeout=3
                    )
                    count = int(grep_in_file.stdout.strip() or "0")
                    claim_text = f"`{filename}`에서 `{kw}` 사용"
                    if count == 0 and claim_text in answer:
                        warnings.append(f"`{filename}`에 `{kw}` 코드 없음 — 실제 확인 필요")

        return warnings[:5]  # 최대 5개만 반환 (메시지 과부하 방지)

# 싱글톤 인스턴스
cove_engine_instance = CoveEngine()
