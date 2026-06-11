"""handlers._vault — Vault 진단 명령어"""
import os
import re
import datetime
from telegram import Update
from telegram.ext import ContextTypes
from handlers._base import (router, logger, add_to_history, _call_llm, safe_reply, safe_edit,
    check_user)
from modules.vault_scanner import scan_vault, calculate_similarities, write_report
from modules.weakness_miner import get_weakness_miner

# graphify — 온디맨드 지식 그래프 분석 (pip 패키지: graphifyy, 임포트: graphify)
_HAS_GRAPHIFY = True

BASE_DIR = "/Users/bluesea/Applications/Mjobsidian"
EXCLUDE_DIRS = {".obsidian", ".smart-env", ".tmp.drivedownload", ".tmp.driveupload",
                ".vscode", "graphify-out", "outputs", "raw", ".git", ".trash"}

GRAPHIFY_CACHE = os.path.join(BASE_DIR, ".graphify_cache")  # graphify 온디맨드 출력 경로

async def cmd_vault(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/vault check - Vault 보관소 정합성 종합 진단"""
    if not await check_user(update):
        return

    subcmd = context.args[0].lower() if context.args else 'check'

    if subcmd == 'check':
        await _vault_check(update, context)
    elif subcmd == 'duplicates':
        await _vault_duplicates(update, context)
    elif subcmd == 'graph':
        await _vault_graph(update, context)
    else:
        await safe_reply(update.message, 
            "📦 **Vault 진단 명령어**\n\n"
            "• `/vault check` — 종합 정합성 진단 (캐시/프론트매터/중복)\n"
            "• `/vault duplicates` — 중복 문서 상세 스캔\n"
            "• `/vault graph` — Graphify 그래프 분석 (고립 문서/허브 노드)\n",
            parse_mode='HTML'
        )

async def _vault_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """종합 정합성 진단"""
    msg = await safe_reply(update.message, 
        "🔍 **Vault 정합성 진단 중...** (캐시/프론트매터/중복)",
        parse_mode='HTML'
    )

    results = []
    issues = []
    warnings = []
    pairs = []

    # ── 1. 캐시/찌꺼기 파일 검사 ──────────────────────────
    cache_files = []
    ds_stores = []
    for root, dirs, files in os.walk(BASE_DIR):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith('.')]
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, BASE_DIR)
            if '.DS_Store' in f:
                ds_stores.append(rel)
            elif f.endswith(('.tmp', '.bak', '~')):
                cache_files.append(rel)

    if ds_stores:
        issues.append(f"🗑️ `.DS_Store` 파일 {len(ds_stores)}개 발견 (무해하나 정리 가능)")
    if cache_files:
        issues.append(f"🗑️ 임시/백업 파일 {len(cache_files)}개 발견")

    # ── 2. 프론트매터 스키마 검사 ────────────────────────
    bad_frontmatter = []
    no_title = 0
    for root, dirs, files in os.walk(BASE_DIR):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for f in files:
            if not f.endswith('.md') or f.startswith('.'):
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, BASE_DIR)
            try:
                with open(full, 'r', encoding='utf-8', errors='ignore') as fh:
                    first = fh.read(512)
            except Exception:
                continue
            if first.startswith('---'):
                end = first.find('---', 3)
                if end == -1:
                    bad_frontmatter.append(rel)
                elif 'title:' not in first[:end]:
                    no_title += 1
            # no frontmatter at all → skip (Obsidian은 파일명이 title이므로 정상)

    if bad_frontmatter:
        issues.append(f"⚠️ 닫히지 않은 프론트매터: {len(bad_frontmatter)}개 파일")
        for p in bad_frontmatter[:5]:
            warnings.append(f"  └─ {p}")
        if len(bad_frontmatter) > 5:
            warnings.append(f"  └─ 외 {len(bad_frontmatter)-5}개")

    # ── 3. 중복 문서 스캔 (TF-IDF) ───────────────────────
    try:
        docs = scan_vault()
        pairs = calculate_similarities(docs)
        high_dup = [p for p in pairs if p['similarity'] >= 0.80]
        med_dup = [p for p in pairs if 0.60 <= p['similarity'] < 0.80]
        if high_dup:
            issues.append(f"🔴 고중복 (≥80%): {len(high_dup)}쌍 — 병합 필요")
        if med_dup:
            issues.append(f"🟡 중간 중복 (60~80%): {len(med_dup)}쌍 — 검토 권장")
        if not high_dup and not med_dup:
            issues.append(f"✅ 중복 문서 없음 (40% 이상 유사 {len(pairs)}쌍은 경미)")
    except Exception as e:
        issues.append(f"❌ 중복 스캔 오류: {e}")

    # ── 4. 통계 ────────────────────────────────────────
    total_md = 0
    total_bytes = 0
    for root, dirs, files in os.walk(BASE_DIR):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for f in files:
            if f.endswith('.md') and not f.startswith('.'):
                total_md += 1
                total_bytes += os.path.getsize(os.path.join(root, f))

    total_mb = total_bytes / (1024 * 1024)
    stats = (
        f"📊 **Vault 통계**\n"
        f"  ├─ 총 .md 파일: **{total_md}개**\n"
        f"  ├─ 총 용량: **{total_mb:.1f} MB**\n"
        f"  ├─ `.DS_Store`: {len(ds_stores)}개\n"
        f"  └─ 프론트매터 title 없음: {no_title}개 (파일명 대체, 정상)"
    )

    # ── 5. Graphify 그래프 분석 (고립 노드/허브 노드) ────────────
    graph_stats = None
    try:
        from pathlib import Path
        from graphify.extract import extract as g_extract
        from graphify.build import build_from_json
        from graphify.analyze import god_nodes

        vault_root = Path(BASE_DIR)
        files = list(vault_root.rglob("*.md"))
        files = [f for f in files if not any(
            ex in f.relative_to(vault_root).parts
            for ex in EXCLUDE_DIRS
        )]

        if files:
            extraction = g_extract(files, parallel=False)
            G = build_from_json(extraction, directed=True)
            n_nodes = G.number_of_nodes()
            n_edges = G.number_of_edges()
            isolated = [n for n, d in G.degree() if d == 0]
            orphan_count = len(isolated)
            orphan_pct = orphan_count / n_nodes * 100 if n_nodes > 0 else 0
            gods = god_nodes(G, top_n=5)

            graph_stats = {
                "nodes": n_nodes,
                "edges": n_edges,
                "orphans": orphan_count,
                "orphan_pct": orphan_pct,
                "hubs": [{
                    "label": g.get('label', g.get('id', str(g)))[:50],
                    "degree": g.get('degree', '?')
                } for g in gods[:5]]
            }

            if orphan_pct > 15:
                issues.append(f"🕸️ **고립 문서 {orphan_count}개 ({orphan_pct:.1f}%)** — 연결 없는 문서 비율 높음, `/vault graph` 확인")
            elif orphan_pct > 5:
                issues.append(f"🕸️ 고립 문서 {orphan_count}개 ({orphan_pct:.1f}%) — 경미")
    except ImportError:
        pass
    except Exception:
        pass

    verdict = "✅ **정합성 양호**" if len(issues) == 0 else (
        f"⚠️ **진단 결과: {len(issues)}개 항목 발견**"
    )

    graph_line = ""
    if graph_stats:
        nl = "\n"
        hub_line = ""
        if graph_stats["hubs"]:
            hub_line = "  └─ 허브: " + ", ".join(
                f"`{h['label']}`(연결 {h['degree']})" for h in graph_stats["hubs"]
            )
        graph_line = (
            f"\n### 🕸️ 그래프 연결성\n"
            f"  ├─ 노드: **{graph_stats['nodes']}개** | 엣지: **{graph_stats['edges']}개**\n"
            f"  ├─ 고립: **{graph_stats['orphans']}개 ({graph_stats['orphan_pct']:.1f}%)**\n"
            f"{hub_line}\n"
        )

    result = (
        f"{verdict}\n\n"
        f"{stats}\n\n"
        f"### 🔍 이슈\n"
        + "\n".join(f"- {i}" for i in issues) +
        (f"\n\n### 📋 세부\n" + "\n".join(warnings) if warnings else "") +
        (graph_line if graph_stats else "") +
        "\n\n`/vault duplicates`로 중복 문서 상세 확인 · `/vault graph`로 그래프 전체 분석"
    )

    await safe_edit(msg, result, parse_mode='HTML')

    # 진단 결과 히스토리 기록
    hist_parts = [f"[Vault Check] {len(issues)}개 이슈, {total_md}개 문서, {total_mb:.1f}MB"]
    hist_parts.append(f"중복: {len(pairs) if isinstance(pairs, list) else 0}쌍")
    if graph_stats:
        hist_parts.append(f"고립: {graph_stats['orphans']}/{graph_stats['nodes']}({graph_stats['orphan_pct']:.1f}%)")
    await add_to_history("assistant", " | ".join(hist_parts))

async def _vault_duplicates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """중복 문서 상세 스캔"""
    msg = await safe_reply(update.message, 
        "🔍 **Vault 중복 문서 스캔 중...** (TF-IDF 분석)",
        parse_mode='HTML'
    )

    try:
        docs = scan_vault()
        pairs = calculate_similarities(docs)

        if not pairs:
            await safe_edit(msg, 
                "✅ **중복 문서 없음** — 40% 이상 유사한 문서가 없습니다.",
                parse_mode='HTML'
            )
            return

        # 요약
        high = [p for p in pairs if p['similarity'] >= 0.80]
        med = [p for p in pairs if 0.60 <= p['similarity'] < 0.80]
        low = [p for p in pairs if p['similarity'] < 0.60]

        result = (
            f"🔍 **중복 문서 분석 결과** ({len(pairs)}쌍)\n\n"
            f"| 수준 | 기준 | 건수 |\n"
            f"| :--- | :--- | ---: |\n"
            f"| 🔴 고중복 | ≥80% | {len(high)} |\n"
            f"| 🟡 중간 | 60~80% | {len(med)} |\n"
            f"| 🟢 경미 | 40~60% | {len(low)} |\n\n"
            f"**상위 10쌍:**\n"
            f"| # | 문서 A | 문서 B | 유사도 |\n"
            f"| :--- | :--- | :--- | :--- |\n"
        )
        for idx, pair in enumerate(pairs[:10], 1):
            d1 = os.path.basename(pair['doc1'])
            d2 = os.path.basename(pair['doc2'])
            sim = f"{pair['similarity']*100:.1f}%"
            result += f"| {idx} | `{d1}` | `{d2}` | **{sim}** |\n"

        result += "\n보고서: `wiki/00_Meta/보관소_유사도_분석보고서.md`"

        # 보고서 파일 생성
        write_report(pairs)

        await safe_edit(msg, result, parse_mode='HTML')

    except Exception as e:
        await get_weakness_miner().record_failure("cmd_vault_duplicates", str(e))
        await safe_edit(msg, f"❌ 중복 스캔 오류: `{e}`", parse_mode='HTML')


async def _vault_graph(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await safe_reply(update.message, 
        "🕸️ **Graphify 그래프 분석 중...** (문서 간 연결 추출)",
        parse_mode='HTML'
    )

    try:
        import graphify
        from graphify.extract import extract as g_extract
        from graphify.build import build_from_json
        from graphify.analyze import god_nodes, surprising_connections
        from pathlib import Path

        # ── 1. wiki/ 내 모든 .md 파일 수집 ────────────────
        vault_root = Path(BASE_DIR)
        files = list(vault_root.rglob("*.md"))
        # 제외 디렉토리 필터
        files = [f for f in files if not any(
            ex in f.relative_to(vault_root).parts
            for ex in EXCLUDE_DIRS
        )]

        if not files:
            await safe_edit(msg, "❌ 분석할 `.md` 파일이 없습니다.", parse_mode='HTML')
            return

        await safe_edit(msg, 
            f"🕸️ **Graphify 분석 중...** ({len(files)}개 파일)\n"
            f"└─ 추출(extract) → 그래프(build) → 분석(analyze)",
            parse_mode='HTML'
        )

        # ── 2. extract (parallel=False: Hermes venv spawn 제한으로 순차 처리) ──
        extraction = g_extract(files, parallel=False)

        # ── 3. build_from_json (directed=True) ─────────────
        G = build_from_json(extraction, directed=True)

        n_nodes = G.number_of_nodes()
        n_edges = G.number_of_edges()

        # ── 4. god_nodes ───────────────────────────────────
        gods = god_nodes(G, top_n=10)
        hub_lines = []
        for item in gods[:8]:
            if isinstance(item, dict):
                nid = item.get('label', item.get('id', str(item)))[:60]
                deg = item.get('degree', '?')
                hub_lines.append(f"  └─ `{nid}` (연결 {deg})")
            else:
                hub_lines.append(f"  └─ `{str(item)[:60]}`")

        # ── 5. 고립 노드 ──────────────────────────────────
        isolated = [n for n, d in G.degree() if d == 0]
        orphan_count = len(isolated)

        # ── 6. 커뮤니티 클러스터링 + HTML 생성 ──────────────
        communities = graphify.cluster(G)
        html_path = Path(BASE_DIR) / "graph.html"
        graphify.to_html(G, communities, str(html_path))

        # ── 7. surprising_connections ──────────────────────
        surprises = []
        try:
            surprises = surprising_connections(G, top_n=5)
        except Exception:
            pass

        # ── 8. 빌드 리포트 ─────────────────────────────────
        NL = "\n"
        result = (
            f"🕸️ **Vault 그래프 분석**{NL}{NL}"
            f"📊 **통계**{NL}"
            f"  ├─ 노드(문서): **{n_nodes}개**{NL}"
            f"  ├─ 엣지(연결): **{n_edges}개**{NL}"
            f"  ├─ 커뮤니티: **{len(communities)}개**{NL}"
            f"  ├─ 스캔 파일: **{len(files)}개**{NL}"
            f"  └─ 고립 문서: **{orphan_count}개"
            + (f" ({orphan_count/n_nodes*100:.1f}%)" if n_nodes > 0 else "")
            + f"{NL}{NL}"
            f"🔗 **허브 노드 (상위 {len(hub_lines)}개)**{NL}"
            + (f"{NL}".join(hub_lines) if hub_lines else f"  └─ 없음{NL}")
            + (f"{NL}{NL}💡 **놀라운 연결**{NL}" + f"{NL}".join(
                f"  └─ `{s.get('source','?')[:40]}` ↔ `{s.get('target','?')[:40]}`"
                for s in surprises
            ) if surprises else "")
            + f"{NL}{NL}📊 **시각화 HTML**: `file://{html_path}`"
            + f"{NL}> 💡 `/vault check`로 전체 진단 · `wiki/`내 모든 .md 대상"
        )

        await safe_edit(msg, result, parse_mode='HTML')
        await add_to_history("assistant",
            f"[Vault Graph] {n_nodes}노드 {n_edges}엣지, {orphan_count}고립, {len(gods)}허브"
        )

    except ImportError:
        NL = "\n"
        await safe_edit(msg, 
            f"❌ **Graphify 패키지가 설치되지 않았습니다.**{NL}{NL}"
            f"설치: `pip install graphifyy`{NL}"
            f"이 명령어를 사용하려면 pip 환경에 graphifyy 패키지가 필요합니다.",
            parse_mode='HTML'
        )
    except Exception as e:
        await get_weakness_miner().record_failure("cmd_vault_graph", str(e))
        await safe_edit(msg, f"❌ 그래프 분석 오류: `{e}`", parse_mode='HTML')
