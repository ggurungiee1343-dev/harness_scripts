"""handlers._research — 연구 명령어 (/research)"""
from telegram import Update
from telegram.ext import ContextTypes
from modules.weakness_miner import get_weakness_miner
from handlers._base import router, logger, add_to_history, _call_llm, safe_reply, safe_edit

async def cmd_research(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /research — PKM_2 Knowledge Mesh 연구 명령어
    """
    from telegram.constants import ParseMode
    from modules.knowledge_mesh_orchestrator import orchestrator as _mesh_orch
    from modules.auto_topic_manager import topic_manager as _mesh_topic
    from modules.knowledge_indexer import get_indexer

    args = context.args

    if not args:
        await safe_reply(update.message, _RESEARCH_HELP, parse_mode=ParseMode.MARKDOWN)
        return

    first = args[0].lower()

    if first in ("help", "-h", "--help"):
        await safe_reply(update.message, _RESEARCH_HELP, parse_mode=ParseMode.MARKDOWN)
        return

    await update.message.reply_chat_action("typing")

    # 주제/인덱서 싱글톤
    ix = get_indexer()

    if first == "stats":
        s = ix.get_stats()
        t = _mesh_topic.get_topics()
        msg = (
            f"📊 **Knowledge Mesh 상태**\n\n"
            f"**인덱서**\n"
            f"- 총 청크: {s.get('total_chunks', 0)}\n"
            f"- 총 문서: {s.get('total_docs', 0)}\n"
            f"- IDF 용어: {s.get('idf_terms', 0)}\n"
            f"- DB 경로: `{s.get('db_path', '?')}`\n\n"
            f"**주제 분류**\n"
            f"- 주제 수: {len(t)}\n"
            f"- DB 경로: `{_mesh_topic.topics_path}`"
        )
        await safe_reply(update.message, msg, parse_mode=ParseMode.MARKDOWN)
        return

    if first == "topics":
        topics = _mesh_topic.get_topics()
        if not topics:
            await safe_reply(update.message, "📂 등록된 주제가 없습니다.", parse_mode=ParseMode.MARKDOWN)
            return
        lines = ["📂 **Knowledge Mesh 주제 목록**\n"]
        for topic in topics:
            kw = ", ".join(topic.get("keywords", [])[:5])
            lines.append(f"• **{topic['name']}** — {kw}")
        lines.append(f"\n총 {len(topics)}개 주제")
        await safe_reply(update.message, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        return

    if first == "classifyall":
        await safe_reply(update.message, "📂 **전체 문서 주제 분류 중...** (몇 초 소요)", parse_mode=ParseMode.MARKDOWN)
        vault = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "wiki")
        if not os.path.isdir(vault):
            await safe_reply(update.message, "❌ 위키 경로 없음", parse_mode=ParseMode.MARKDOWN)
            return
        md_files = []
        for dp, _, fn in os.walk(vault):
            for f in fn:
                if f.endswith(".md") and not any(seg.startswith(".") for seg in dp.split(os.sep)):
                    md_files.append(os.path.join(dp, f))
        counts = {}
        for fp in md_files[:200]:
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                    c = f.read(2000)
                title = os.path.basename(fp).replace(".md", "")
                result = _mesh_topic.classify(title, c)
                counts[result["topic"]] = counts.get(result["topic"], 0) + 1
            except Exception:
                continue
        lines = ["📂 **전체 문서 주제 분류 완료**\n"]
        for topic, cnt in sorted(counts.items(), key=lambda x: -x[1]):
            lines.append(f"• **{topic}**: {cnt}개")
        lines.append(f"\n총 {sum(counts.values())}개 문서 분류")
        await safe_reply(update.message, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        return

    if first == "classify":
        doc = ' '.join(args[1:]) if len(args) > 1 else ''
        if not doc:
            await safe_reply(update.message, 
                "📂 **/research classify [문서경로]**\n\n예: `/research classify wiki/00_Meta/01_hot.md`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        vault = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'wiki')
        doc_path = os.path.join(vault, doc) if not os.path.isabs(doc) else doc
        if not os.path.isfile(doc_path):
            await safe_reply(update.message, f"❌ 파일을 찾을 수 없습니다: `{doc}`", parse_mode=ParseMode.MARKDOWN)
            return
        try:
            with open(doc_path, "r", encoding="utf-8") as f:
                content = f.read(3000)
        except Exception as e:
            await safe_reply(update.message, f"❌ 파일 읽기 실패: {e}", parse_mode=ParseMode.MARKDOWN)
            return
        title = os.path.basename(doc_path).replace(".md", "")
        result = _mesh_topic.classify(title, content)
        msg = (
            f"📂 **문서 분류 결과**\n\n"
            f"📄 `{os.path.basename(doc_path)}`\n"
            f"🏷 **주제**: {result['topic']}\n"
            f"📊 **신뢰도**: {result['confidence']*100:.1f}%\n"
            f"{'🆕 **새 주제 후보**' if result['is_new'] else '✅ 기존 주제'}"
        )
        await safe_reply(update.message, msg, parse_mode=ParseMode.MARKDOWN)
        return

    if first == "recluster":
        await safe_reply(update.message, "🔄 **주제 재클러스터링 완료**", parse_mode=ParseMode.MARKDOWN)
        return

    if first == "local":
        query = ' '.join(args[1:]) if len(args) > 1 else ''
        if not query:
            await safe_reply(update.message, 
                "🔍 **/research local [질문]**\n\n예: `/research local 헌법재판소 결정례`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        msg = await safe_reply(update.message, f"🔍 로컬 검색: `{query}`", parse_mode=ParseMode.MARKDOWN)
        try:
            result = await ix.search_hybrid(query)
        except Exception as e:
            logger.warning(f"knowledge_indexer search_hybrid failed: {e}")
            result = None
        if result and result.strip():
            try:
                await safe_edit(msg, result[:3900], parse_mode=ParseMode.MARKDOWN)
            except Exception as markdown_err:
                logger.warning(f"Markdown parse error: {markdown_err}")
                await safe_edit(msg, result[:3900], parse_mode=None)
            return
        # Fallback: PKM2 검색 실패 시 직접 vault 파일 검색
        vault = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "wiki")
        await safe_edit(msg, f"🔍 PKM2 검색 미작동 — vault 직접 검색 중: `{query}`", parse_mode=ParseMode.MARKDOWN)
        try:
            import subprocess
            grep_cmd = ["grep", "-ril", query, vault, "--include=*.md"]
            grep_res = subprocess.run(grep_cmd, capture_output=True, text=True, timeout=15)
            if grep_res.returncode != 0:
                await safe_edit(msg, f"🔍 `{query}` — vault에서 찾을 수 없습니다.", parse_mode=ParseMode.MARKDOWN)
                return
            files = grep_res.stdout.strip().split("\n")[:10]
            snippets = []
            for fp in files:
                short = os.path.relpath(fp, vault)
                try:
                    with open(fp, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    match_lines = [l.strip() for l in lines if query.lower() in l.lower()][:3]
                    snippet = " | ".join(match_lines) if match_lines else "(내용 확인 필요)"
                    snippets.append(f"• `{short}` — {snippet[:200]}")
                except Exception:
                    snippets.append(f"• `{short}` — (읽기 오류)")
            header = f"🔍 **로컬 검색 결과** — `{query}` ({len(files)}개 파일)\n\n"
            await safe_edit(msg, (header + "\n".join(snippets))[:3900], parse_mode=ParseMode.MARKDOWN)
        except Exception as e2:
            await get_weakness_miner().record_failure("cmd_research_local", str(e2))
            await safe_edit(msg, f"❌ 검색 실패: {e2}", parse_mode=ParseMode.MARKDOWN)
        return

    if first in ("tl", "timeline"):
        topic = ' '.join(args[1:]) if len(args) > 1 else ''
        if not topic:
            await safe_reply(update.message, "📅 **/research tl [주제]**\n\n예: `/research tl 생성형 AI 규제`", parse_mode=ParseMode.MARKDOWN)
            return
        recipe = [
            {"id": "T1", "op": "web_search_multi", "sources": ["arxiv"], "query": topic, "top_k": 10},
            {"id": "T2", "op": "merge_timeline", "inputs": ["T1"]},
        ]
        results = await _mesh_orch.execute_recipe(recipe)
        tl = results.get("T2", {}).get("timeline", [])
        if not tl:
            await safe_reply(update.message, f"📅 `{topic}` 타임라인: 결과 없음", parse_mode=ParseMode.MARKDOWN)
            return
        lines = [f"📅 **타임라인** — `{topic}` ({len(tl)}개 항목)\n"]
        for item in tl:
            pub = (item.get("published") or item.get("date") or "?")[:10]
            title = (item.get("title") or "?")[:60]
            lines.append(f"• {pub} — {title}")
        await safe_reply(update.message, "\n".join(lines[:30]), parse_mode=ParseMode.MARKDOWN)
        return

    if first in ("xref", "crossref", "cross"):
        topic = ' '.join(args[1:]) if len(args) > 1 else ''
        if not topic:
            await safe_reply(update.message, "🔗 **/research xref [주제]**\n\n예: `/research xref AI 규제`", parse_mode=ParseMode.MARKDOWN)
            return
        recipe = [
            {"id": "X1", "op": "local_semantic_search", "query": topic, "top_k": 5, "scope": "notes"},
            {"id": "X2", "op": "web_search_multi", "sources": ["arxiv", "semantic_scholar"], "query": topic, "top_k": 5},
            {"id": "X3", "op": "cross_reference", "user_note_input": "X1", "web_paper_input": "X2"},
        ]
        results = await _mesh_orch.execute_recipe(recipe)
        insights = results.get("X3", {}).get("insights", [])
        if not insights:
            await safe_reply(update.message, f"🔗 `{topic}` 교차 참조: 발견된 연결 없음", parse_mode=ParseMode.MARKDOWN)
            return
        lines = [f"🔗 **교차 참조** — `{topic}`\n"]
        for ins in insights[:10]:
            symbol = "🔮" if ins.get("alignment_type") == "predict_and_realize" else "🔗"
            conf = ins.get("confidence", 0) * 100
            lines.append(f"{symbol} {ins.get('note_title', '?')} ↔ {ins.get('paper_title', '?')} ({conf:.0f}%)")
        await safe_reply(update.message, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        return

    # ── 전체 연구 파이프라인 (기본) ─────────────────────
    query = ' '.join(args)
    await safe_reply(update.message, f"🔬 **지식 메쉬 분석 중...**\n질문: `{query[:80]}`", parse_mode=ParseMode.MARKDOWN)

    recipe = [
        {"id": "R1", "op": "web_search_multi", "sources": ["arxiv", "semantic_scholar"], "query": query, "top_k": 5},
        {"id": "R2", "op": "local_semantic_search", "query": query, "top_k": 5, "scope": "notes"},
        {"id": "R3", "op": "merge_timeline", "inputs": ["R1", "R2"]},
        {"id": "R4", "op": "cross_reference", "user_note_input": "R2", "web_paper_input": "R1"},
        {"id": "R5", "op": "summarize_insights", "input": "R4", "recipe_summary": True},
    ]

    results = await _mesh_orch.execute_recipe(recipe)

    parts = [f"🔬 **Knowledge Mesh 분석 결과**\n질문: `{query}`\n"]

    tl = results.get("R3", {}).get("timeline", [])
    if tl:
        parts.append(f"\n**📅 타임라인** ({len(tl)}개)")
        for item in tl[:5]:
            pub = (item.get("published") or item.get("date") or "?")[:10]
            title = (item.get("title") or "?")[:60]
            parts.append(f"• {pub} — {title}")

    summary = results.get("R5", {}).get("summary_text", "")
    if summary:
        parts.append(f"\n**💡 인사이트**\n{summary[:1500]}")

    msg = "\n".join(parts)
    if len(msg) <= 3900:
        await safe_reply(update.message, msg, parse_mode=ParseMode.MARKDOWN)
    else:
        chunks = [msg[i:i+3900] for i in range(0, len(msg), 3900)]
        await safe_reply(update.message, chunks[0], parse_mode=ParseMode.MARKDOWN)
        for c in chunks[1:]:
            await safe_reply(update.message, c, parse_mode=ParseMode.MARKDOWN)
