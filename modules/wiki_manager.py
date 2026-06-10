import os, unicodedata

class WikiManager:
    def __init__(self, vault_path="/Users/bluesea/Applications/Mjobsidian"):
        self.vault_path = unicodedata.normalize('NFC', vault_path)

    def get_graph_context(self):
        """Graphify 보고서를 읽어 지식 간의 연결 구조 파악"""
        report_path = os.path.join(self.vault_path, "graphify-out", "GRAPH_REPORT.md")
        if os.path.exists(report_path):
            with open(report_path, "r", encoding="utf-8") as f:
                return f"\n\n[🕸️ Knowledge Graph Context]\n{f.read()[:1000]}" # 핵심만
        return ""

    def read_wiki(self, rel_path):
        full_path = os.path.join(self.vault_path, "wiki", rel_path)
        if os.path.exists(full_path):
            with open(full_path, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def get_recent_context(self):
        """보관소 전체에서 최근 수정/업로드된 파일 목록(최신순 10개) 반환"""
        if not os.path.exists(self.vault_path):
            return "❌ 보관소 경로가 존재하지 않습니다."
        
        ignore_dirs = {".git", ".obsidian", ".stversions", ".trash", ".system_generated", "node_modules"}
        
        files = []
        for root, dirs, filenames in os.walk(self.vault_path):
            # hidden/meta directories filter
            dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]
            
            for f in filenames:
                if f.startswith("."):
                    continue
                path = os.path.join(root, f)
                try:
                    mtime = os.path.getmtime(path)
                    files.append((path, mtime))
                except OSError:
                    continue
        
        if not files:
            return "🕒 최근 갱신된 파일이 없습니다."
            
        # 최신순 정렬
        files.sort(key=lambda x: x[1], reverse=True)
        recent_files = files[:10]
        
        import datetime
        
        lines = ["🕒 **최근 업로드 및 갱신된 파일 목록 (최신순 10개)**\n"]
        for idx, (path, mtime) in enumerate(recent_files, 1):
            rel_p = os.path.relpath(path, self.vault_path)
            dt = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            lines.append(f"{idx}. 📄 `{os.path.basename(rel_p)}` (경로: `{os.path.dirname(rel_p) or '/'}`) - *{dt}*")
            
        return "\n".join(lines)

    def get_system_context(self):
        """참고용 메타 문서의 '내용'만 전달하며, 폴더 목록 오해를 방지함"""
        core_files = [
            "00_Meta/나의 비서 가이드.md",
            "00_Meta/guardrails.md",
            "00_Meta/스크립트 정보.md",
            "00_Meta/USER.md",
            "00_Meta/hot.md"
        ]
        
        context = "### [REFERENCE DOCUMENTS - CONTENT ONLY]\n"
        context += "⚠️ WARNING: 아래 리스트는 참고용 지침서의 내용일 뿐, 실제 폴더의 전체 파일 목록이 아닙니다.\n"
        
        for idx, rel_p in enumerate(core_files):
            content = self.read_wiki(rel_p)
            if content:
                # 절대로 파일명을 노출하지 않음 (환각의 근본 원인 차단)
                context += f"\n---\n[[ SYSTEM GUIDELINE {idx+1} ]]\n{content[:1500]}\n"
        
        # 실제 데이터 조회 원칙 재강조
        context += "\n\n[🚨 ABSOLUTE TRUTH RULE]\n"
        context += "1. 파일 목록 조회 시 위 문서들의 이름을 인용하는 것은 '환각(Hallucination)'입니다.\n"
        context += "2. 반드시 [LIST] 태그를 실행하여 얻은 결과만 '실제 파일 목록'으로 인정하십시오.\n"
        context += "3. 당신은 현재 이 문장들을 읽기 전까지 폴더 내에 어떤 파일이 있는지 전혀 모르는 상태라고 가정하십시오.\n"
        
        graph = self.get_graph_context()
        return f"{context}\n\n{graph}"