"""handlers._paper — 논문 명령어 (/paper)"""
import os
import json
from telegram import Update
from telegram.ext import ContextTypes
from handlers._base import (router, cove_engine_instance, _audit_engine, safe_reply, safe_edit,
    logger, add_to_history, _call_llm, _get_mem_info)
from index_db import add_paper, get_paper, list_papers, create_bundle


def _build_paper_table(papers: list) -> str:
    """논문 목록을 텔레그램 표로 변환"""
    if not papers:
        return "📂 저장된 논문이 없습니다."
    lines = ["📚 **논문 목록**\n"]
    for p in papers:
        bundle_tag = f" [번들#{p['bundle_id']}]" if p.get('bundle_id') else ""
        lines.append(f"• `{p['id']}` {p['title'][:60]}{bundle_tag}")
    return "\n".join(lines)


async def cmd_paper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /paper humanize [텍스트] — 논문 초안/법률 문건을 학술 문체로 변환
    /paper draft [주제]  — 논문 초안 생성 (개요→초안→문체 3단계)
    /paper review [문서] — 논문 검토
    /paper list         — 저장된 논문 목록
    /paper show [id]    — 논문 상세 조회
    /paper bundle [id1] [id2] ... — 여러 논문을 번들로 묶기
    /paper claims [번들id] — 번들에서 주장(claim) 추출
    /paper compare [id1] [id2] — 두 논문/번들 비교
    /paper save [번들id] — 번들 상태를 마크다운으로 저장
    """
    from telegram.constants import ParseMode
    args = context.args
    if not args:
        await safe_reply(update.message, 
            "📄 **/paper 사용법**\n\n"
            "• `/paper humanize [텍스트]` — 법률/학술 문체 변환\n"
            "• `/paper draft [주제]` — 논문 초안 생성 (개요→초안→문체)\n"
            "• `/paper review [문서]` — 논문 검토\n"
            "• `/paper list` — 저장된 논문 목록\n"
            "• `/paper show [id]` — 논문 상세 조회\n"
            "• `/paper bundle [id1] [id2] ...` — 여러 논문을 번들로 묶기\n"
            "• `/paper claims [번들id]` — 번들에서 주장 추출\n"
            "• `/paper compare [id1] [id2]` — 두 논문/번들 비교\n"
            "• `/paper save [번들id]` — 번들 상태를 마크다운으로 저장",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    subcmd = args[0].lower()

    # ── /paper list ────────────────────────────────────────────────
    if subcmd == 'list':
        papers = list_papers()
        if not papers:
            await safe_reply(update.message, "📂 저장된 논문이 없습니다.")
            return
        text = _build_paper_table(papers)
        await safe_reply(update.message, text, parse_mode=ParseMode.MARKDOWN)
        return

    # ── /paper show ────────────────────────────────────────────────
    if subcmd == 'show':
        if len(args) < 2:
            await safe_reply(update.message, "사용법: `/paper show [논문ID]`", parse_mode=ParseMode.MARKDOWN)
            return
        paper = get_paper(args[1])
        if not paper:
            await safe_reply(update.message, f"❌ 논문을 찾을 수 없습니다: `{args[1]}`", parse_mode=ParseMode.MARKDOWN)
            return
        text = (
            f"📄 **{paper['title']}**\n\n"
            f"**저자**: {paper.get('authors', '미기재')}\n"
            f"**arXiv**: {paper.get('arxiv_id', '없음')}\n"
            f"**URL**: {paper.get('url', '없음')}\n"
            f"**태그**: {paper.get('tags', '없음')}\n"
            f"**번들ID**: {paper.get('bundle_id', '없음')}\n"
            f"**등록일**: {paper.get('created_at', '알 수 없음')}\n\n"
            f"**초록**:\n{paper.get('abstract', '없음')[:500]}"
        )
        await safe_reply(update.message, text, parse_mode=ParseMode.MARKDOWN)
        return

    # ── /paper bundle ──────────────────────────────────────────────
    if subcmd == 'bundle':
        paper_ids = args[1:]
        if len(paper_ids) < 2:
            await safe_reply(update.message, 
                "📦 **/paper bundle [id1] [id2] ...**\n\n"
                "번들로 묶을 논문 ID를 2개 이상 입력해주세요.\n"
                "예: `/paper bundle 1 2 3`",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # 모든 ID가 유효한지 확인
        valid_ids = []
        for pid in paper_ids:
            try:
                pid_int = int(pid)
                p = get_paper(str(pid_int))
                if p:
                    valid_ids.append(pid_int)
            except (ValueError, TypeError):
                continue

        if len(valid_ids) < 2:
            await safe_reply(update.message, 
                "❌ 유효한 논문 ID가 2개 미만입니다. `/paper list`로 ID를 확인하세요.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        bundle_id = create_bundle(valid_ids)
        if bundle_id:
            await safe_reply(update.message, 
                f"📦 **번들 생성 완료** (번들#{bundle_id})\n\n"
                f"묶인 논문: {', '.join(str(i) for i in valid_ids)}\n"
                f"`/paper save {bundle_id}` — 번들을 마크다운으로 저장\n"
                f"`/paper claims {bundle_id}` — 번들에서 주장 추출",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await safe_reply(update.message, "❌ 번들 생성 실패.", parse_mode=ParseMode.MARKDOWN)
        return

    # ── /paper claims ──────────────────────────────────────────────
    if subcmd == 'claims':
        bundle_id = args[1] if len(args) > 1 else ''
        if not bundle_id:
            await safe_reply(update.message, 
                "📋 **/paper claims [번들ID]**\n\n"
                "주장을 추출할 번들 ID를 입력해주세요.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        try:
            papers = list_papers(bundle_id=int(bundle_id))
        except (ValueError, TypeError):
            papers = []

        if not papers:
            await safe_reply(update.message, 
                f"❌ 번들#{bundle_id}에 논문이 없습니다.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        await update.message.reply_chat_action("typing")
        await safe_reply(update.message, 
            f"🔍 **번들#{bundle_id} 주장 추출 중...**\n"
            f"대상 논문: {len(papers)}편",
            parse_mode=ParseMode.MARKDOWN
        )

        # 논문 제목과 초록을 합쳐서 LLM에 전달
        papers_text = "\n\n".join(
            f"[{i+1}] {p['title']}\n초록: {p.get('abstract', '없음')[:500]}"
            for i, p in enumerate(papers)
        )

        claims_prompt = (
            "You are a research analyst. Given the following papers in a bundle, "
            "extract the key claims (주장/thesis statements) from each paper. "
            "Group related claims across papers. "
            "Output in Korean with the following format:\n\n"
            "## 📋 번들 주장 분석\n\n"
            "### 공통 주제\n"
            "- [공통 주제 1]: 설명...\n\n"
            "### 논문별 핵심 주장\n"
            "- **논문1**: 주장...\n"
            "- **논문2**: 주장...\n\n"
            "### 상충/대립 주장\n"
            "- 논문X vs 논문Y: ...\n\n"
            "### 추가 연구가 필요한 영역\n"
            "- ...\n\n"
            f"Papers:\n{papers_text[:3000]}"
        )

        reply = await _call_llm(claims_prompt)
        await safe_reply(update.message, reply, parse_mode=ParseMode.MARKDOWN)
        return

    # ── /paper compare ────────────────────────────────────────────
    if subcmd == 'compare':
        if len(args) < 3:
            await safe_reply(update.message, 
                "⚖️ **/paper compare [id1] [id2]**\n\n"
                "비교할 두 논문 ID를 입력해주세요.\n"
                "예: `/paper compare 1 2`",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        paper1 = get_paper(args[1])
        paper2 = get_paper(args[2])

        if not paper1 or not paper2:
            not_found = [args[1] if not paper1 else '', args[2] if not paper2 else '']
            not_found = [x for x in not_found if x]
            await safe_reply(update.message, 
                f"❌ 찾을 수 없는 논문: {', '.join(not_found)}",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        await update.message.reply_chat_action("typing")
        await safe_reply(update.message, 
            f"⚖️ **논문 비교 중...**\n"
            f"① {paper1['title'][:50]} vs ② {paper2['title'][:50]}",
            parse_mode=ParseMode.MARKDOWN
        )

        compare_prompt = (
            "You are a research analyst. Compare the following two academic papers "
            "and provide a structured comparison in Korean.\n\n"
            "Format:\n"
            "## ⚖️ 논문 비교\n\n"
            "| 항목 | 논문① | 논문② |\n"
            "|---|---|---|\n"
            "| 제목 | ... | ... |\n"
            "| 방법론 | ... | ... |\n"
            "| 핵심 주장 | ... | ... |\n"
            "| 강점 | ... | ... |\n"
            "| 한계 | ... | ... |\n\n"
            "### 주요 차이점\n"
            "- ...\n\n"
            "### 공통점\n"
            "- ...\n\n"
            "### 시사점\n"
            "- ...\n\n"
            f"**[논문①]**\n제목: {paper1['title']}\n저자: {paper1.get('authors', '')}\n초록: {paper1.get('abstract', '')[:1000]}\n\n"
            f"**[논문②]**\n제목: {paper2['title']}\n저자: {paper2.get('authors', '')}\n초록: {paper2.get('abstract', '')[:1000]}"
        )

        reply = await _call_llm(compare_prompt)
        await safe_reply(update.message, reply[:3900], parse_mode=ParseMode.MARKDOWN)
        return

    # ── /paper save ────────────────────────────────────────────────
    if subcmd == 'save':
        bundle_id = args[1] if len(args) > 1 else ''
        if not bundle_id:
            await safe_reply(update.message, 
                "💾 **/paper save [번들ID]**\n\n"
                "저장할 번들 ID를 입력해주세요.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        try:
            papers = list_papers(bundle_id=int(bundle_id))
        except (ValueError, TypeError):
            papers = []

        if not papers:
            await safe_reply(update.message, 
                f"❌ 번들#{bundle_id}에 논문이 없습니다.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # 마크다운 문서 생성
        lines = [
            "---",
            f"created: auto",
            f"bundle_id: {bundle_id}",
            f"paper_count: {len(papers)}",
            "---",
            "",
            f"# 📦 논문 번들 #{bundle_id}",
            "",
            f"총 {len(papers)}편의 논문이 포함되어 있습니다.",
            "",
        ]

        for i, p in enumerate(papers, 1):
            lines.extend([
                f"## {i}. {p['title']}",
                "",
                f"- **저자**: {p.get('authors', '미기재')}",
                f"- **arXiv**: {p.get('arxiv_id', '없음')}",
                f"- **URL**: {p.get('url', '없음')}",
                f"- **태그**: {p.get('tags', '없음')}",
                "",
                p.get('abstract', '초록 없음')[:800],
                "",
                "---",
                "",
            ])

        md_content = "\n".join(lines)

        # Vault에 저장
        vault_path = os.path.expanduser("~/Applications/Mjobsidian/wiki/20_Research")
        os.makedirs(vault_path, exist_ok=True)
        file_name = f"번들_{bundle_id}.md"
        file_path = os.path.join(vault_path, file_name)

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        await safe_reply(update.message, 
            f"💾 **번들 #{bundle_id} 저장 완료**\n\n"
            f"📄 `20_Research/{file_name}`\n"
            f"논문 {len(papers)}편\n\n"
            f"`/read wiki/20_Research/{file_name}` — 내용 확인",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # ── /paper humanize ────────────────────────────────────────────
    if subcmd == 'humanize':
        text = ' '.join(args[1:]) if len(args) > 1 else ''
        if not text:
            await safe_reply(update.message, 
                "✍️ **/paper humanize [텍스트]**\n\n"
                "변환할 텍스트를 입력해주세요.\n"
                "예: `/paper humanize 이 연구는 AI 알고리즘의 차별적 결과가 현행 평등권 법리로 규율 가능한지 검토한다.`",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        await update.message.reply_chat_action("typing")

        llm_prompt = (
            "You are an academic writing assistant specializing in Korean legal academia and AI research. "
            "Transform the following text into formal academic prose suitable for Korean law journals "
            "(서울대학교 법학, 법조, 저스티스, 법학연구 등). "
            "Requirements:\n"
            "- Use precise legal/academic terminology\n"
            "- Maintain rigorous argument structure\n"
            "- Remove colloquialisms and informal phrasing\n"
            "- Ensure logical flow with appropriate transitions\n"
            "- Keep the original meaning and factual claims intact\n"
            "- Output in the same language as the input\n"
            "- Follow Korean legal academic writing conventions\n"
            "- Use Korean legal terminology (판례 인용 시 대법원/헌법재판소 형식 준수)\n"
            "- Maintain honorific-neutral formal register (합쇼체 금지, 명사형 종결 선호)\n"
            "- Citation format: 각주 방식, 저자명(발행연도), 면수\n\n"
            f"Text to transform:\n{text}"
        )

        reply = await _call_llm(llm_prompt)

        await safe_reply(update.message, 
            f"✍️ **학술 문체 변환 완료**\n\n{reply}",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # ── /paper draft ────────────────────────────────────────────────
    if subcmd == 'draft':
        topic = ' '.join(args[1:]) if len(args) > 1 else ''
        if not topic:
            await safe_reply(update.message, 
                "📝 **/paper draft [주제]**\n\n"
                "논문 초안을 생성할 주제를 입력해주세요.\n"
                "예: `/paper draft AI 알고리즘 차별과 평등권 침해에 대한 법적 규율`\n"
                "예: `/paper draft 생성형 AI의 저작물 침해 책임에 관한 연구`",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        await update.message.reply_chat_action("typing")
        await safe_reply(update.message, 
            "📝 **논문 초안 생성 중...**\n"
            f"주제: `{topic[:80]}`\n\n"
            "🔍 선행연구 검색 → 개요 구성 → 초안 작성 순으로 진행합니다.\n"
            "잠시만 기다려주세요...",
            parse_mode=ParseMode.MARKDOWN
        )

        # 1단계: 개요(Outline) 생성
        outline_prompt = (
            "You are an expert academic research assistant specializing in Korean legal studies "
            "and AI law convergence research. Given the following research topic, "
            "generate a detailed paper outline in Korean.\n\n"
            "Requirements:\n"
            "- Follow Korean legal academic paper structure\n"
            "- Include: 제목, 초록 핵심문장, 키워드, 각 장(chapter)별 핵심 논점\n"
            "- Use rigorous legal terminology\n"
            "- Consider both Korean precedent (판례) and comparative law perspectives\n"
            "- Output format:\n\n"
            "## 📋 논문 개요\n\n"
            "**제목**: [title]\n"
            "**키워드**: [keyword1], [keyword2], ...\n"
            "**연구 배경**: [2-3문장]\n"
            "**연구 문제**: [명확한 연구 질문]\n\n"
            "### I. 서론\n"
            "- 연구의 배경과 필요성\n"
            "- 연구 문제 및 가설\n"
            "- 논문의 구성\n\n"
            "### II. [주제 영역명]\n"
            "- ...\n\n"
            "### III. [주제 영역명]\n"
            "- ...\n\n"
            "### IV. [분석/논증]\n"
            "- ...\n\n"
            "### V. 결론\n"
            "- 요약 및 시사점\n"
            "- 한계 및 향후 연구\n\n"
            f"Research Topic:\n{topic}"
        )

        outline = await _call_llm(outline_prompt)

        # 2단계: 본문 초안 생성 (개요 기반)
        draft_prompt = (
            "You are an expert Korean legal-AI convergence academic paper writer. "
            "Based on the following paper outline, write a complete first draft in Korean.\n\n"
            "Requirements:\n"
            "- Write in formal Korean academic prose (명사형 종결, 합쇼체 금지)\n"
            "- Each section should be 3-5 paragraphs with logical flow\n"
            "- Include placeholder citations in format: (저자명, 발행연도, 면수)\n"
            "- Use precise legal terminology (법률 용어 정확히)\n"
            "- Maintain critical analytical tone, not descriptive\n"
            "- 서론: research gap을 명확히, 독창성 강조\n"
            "- 본론: 논증 구조 (주장 → 근거 → 판례/사례 → 분석)\n"
            "- 결론: 요약 + 기여 + 연구의 한계 + 향후 연구 제안\n"
            "- Total length: 3000-5000자\n\n"
            f"[Paper Outline]\n{outline}\n\n"
            "Write the full draft now."
        )

        draft = await _call_llm(draft_prompt)

        # 3단계: 문체 변환 (humanize) 적용
        humanize_prompt = (
            "You are an academic writing assistant specializing in Korean legal journals. "
            "Polish the following paper draft to meet Korean law journal publication standards "
            "(서울대학교 법학, 법조, 저스티스, 법학연구 등).\n\n"
            "Requirements:\n"
            "- Ensure formal academic register (명사형 종결)\n"
            "- Verify legal terminology accuracy\n"
            "- Remove any informal phrasing\n"
            "- Ensure logical flow and paragraph transitions\n"
            "- Keep the original argument structure intact\n"
            "- Output in Korean\n\n"
            f"Draft to polish:\n{draft}"
        )

        final_draft = await _call_llm(humanize_prompt)

        # 최종 출력 (텔레그램 메시지 길이 제한: 4096자)
        MAX_LEN = 3900
        header = f"📝 **논문 초안** — `{topic[:60]}`\n\n"
        footer = "\n\n---\n💡 **다음 단계**: `/paper humanize [텍스트]`로 특정 부분 문체 보정 | `/searchpaper [키워드]`로 참고문헌 검색"

        if len(header + final_draft + footer) <= MAX_LEN:
            await safe_reply(update.message, 
                header + final_draft + footer,
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await safe_reply(update.message, 
                f"📝 **논문 초안** — `{topic[:60]}`\n\n"
                f"**📋 개요**\n{outline}\n\n"
                f"_(본문이 길어 나누어 전송합니다 →)_",
                parse_mode=ParseMode.MARKDOWN
            )
            await safe_reply(update.message, 
                f"**✍️ 본문 초안**\n\n{final_draft[:3900]}" + footer,
                parse_mode=ParseMode.MARKDOWN
            )
        return

    # ── /paper review ─────────────────────────────────────────────
    if subcmd == 'review':
        doc_path = ' '.join(args[1:]) if len(args) > 1 else ''
        if not doc_path:
            await safe_reply(update.message, 
                "📋 **/paper review [문서 경로 또는 텍스트]**\n\n"
                "논문 문서를 읽고 학술적 검토를 수행합니다.\n"
                "예: `/paper review wiki/20_Research/내논문초안.md`\n"
                "예: `/paper review AI 알고리즘 차별에 관한 법적 고찰` (텍스트 직접 입력)",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        await update.message.reply_chat_action("typing")
        msg = await safe_reply(update.message, 
            f"🔍 **논문 검토 중...**\n문서: `{doc_path[:60]}`",
            parse_mode=ParseMode.MARKDOWN
        )

        # 문서 내용 읽기 시도 (파일이면)
        doc_content = doc_path
        vault_base = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'wiki')
        vault_base = os.path.abspath(vault_base)
        possible_paths = [
            doc_path,
            os.path.join(vault_base, doc_path),
            os.path.join(vault_base, '00_Meta', doc_path),
            os.path.join(vault_base, '10_AI_Automation', doc_path),
            os.path.join(vault_base, '20_Research', doc_path),
            os.path.join(vault_base, '30_Journal', doc_path),
            os.path.join(vault_base, '40_Thesis', doc_path),
            os.path.join(vault_base, '50_Invest', doc_path),
        ]
        for p in possible_paths:
            if os.path.isfile(p):
                with open(p, 'r', encoding='utf-8') as f:
                    doc_content = f.read(5000)
                doc_path = p
                break

        review_prompt = (
            "You are an expert academic peer reviewer specializing in Korean legal-AI convergence research. "
            "Review the following document and provide a structured analysis.\n\n"
            "Review criteria:\n"
            "1. **논문 구조 (Structure)**: 서론-본론-결론의 논리적 흐름, 각 섹션의 적절성\n"
            "2. **논증의 타당성 (Argumentation)**: 주장-근거-판례/사례 연결의 논리적 건전성\n"
            "3. **선행연구 검토 (Literature Review)**: 관련 연구의 충분한 인용 및 비판적 검토\n"
            "4. **법률 용어의 정확성 (Terminology)**: 법률 용어 사용의 정확성과 일관성\n"
            "5. **참고문헌 및 인용 (Citations)**: 인용 포맷의 일관성, 주요 문헌 누락 여부\n"
            "6. **개선 제안 (Recommendations)**: 구체적인 수정 제안\n\n"
            "Output format in Korean:\n"
            "## 📋 논문 검토 결과\n\n"
            "### 📊 종합 평가\n"
            "- **구조**: ⭐⭐⭐⭐⭐ (평가)\n"
            "- **논증**: ⭐⭐⭐⭐⭐ (평가)\n"
            "- **선행연구**: ⭐⭐⭐⭐⭐ (평가)\n"
            "- **용어 정확성**: ⭐⭐⭐⭐⭐ (평가)\n"
            "- **인용**: ⭐⭐⭐⭐⭐ (평가)\n\n"
            "### 🔍 상세 검토\n"
            "**[1. 구조]**\n"
            "...\n\n"
            "**[2. 논증]**\n"
            "...\n\n"
            "### 💡 개선 제안\n"
            "- **[중요] 제안 1**: ...\n"
            "- **[권장] 제안 2**: ...\n\n"
            "### ✅ 종합 의견\n"
            "...\n\n"
            f"Document to review:\n{doc_content[:4000]}"
        )

        reply = await _call_llm(review_prompt)

        MAX_LEN = 3900
        header = f"📋 **논문 검토 결과** — `{os.path.basename(doc_path) if os.path.isfile(doc_path) else '(직접 입력)'}`\n\n"
        if len(header + reply) <= MAX_LEN:
            await safe_edit(msg, header + reply, parse_mode=ParseMode.MARKDOWN)
        else:
            await safe_edit(msg, header + reply[:MAX_LEN], parse_mode=ParseMode.MARKDOWN)
            remaining = reply[MAX_LEN:]
            if remaining:
                await safe_reply(update.message, remaining[:MAX_LEN], parse_mode=ParseMode.MARKDOWN)
        return

    await safe_reply(update.message, 
        f"❌ 알 수 없는 하위 명령어: `{subcmd}`\n"
        "사용법: `/paper humanize`, `/paper draft`, `/paper review`, `/paper list`, `/paper show`, `/paper bundle`, `/paper claims`, `/paper compare`, `/paper save`",
        parse_mode=ParseMode.MARKDOWN
    )


# ═══════════════════════════════════════════════════════════════════
# PKM_2 — Knowledge Mesh (Private Knowledge Mesh)
# ═══════════════════════════════════════════════════════════════════

_RESEARCH_HELP = (
    "🔬 **/research 명령어** — Knowledge Mesh (PKM_2)\n\n"
    "• `/research [질문]` — 전체 연구 파이프라인 실행\n"
    "   (로컬 검색 + 타임라인 + 주제 클러스터링 + LLM 분석)\n"
    "• `/research local [질문]` — 로컬 위키 검색만\n"
    "• `/research tl [주제]` — 타임라인만\n"
    "• `/research xref [주제]` — 교차 참조 클러스터링만\n\n"
    "📂 **주제 관리**\n"
    "• `/research topics` — 전체 주제 목록\n"
    "• `/research classify [문서경로]` — 문서 주제 분류\n"
    "• `/research classifyall` — 전체 문서 배치 분류\n"
    "• `/research recluster` — 주제 클러스터 재구성\n\n"
    "📊 **상태**\n"
    "• `/research stats` — 시스템 통계"
)


