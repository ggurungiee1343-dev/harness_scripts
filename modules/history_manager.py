import json, os

# Context Compaction 설정 (2026-06-09)
COMPACT_THRESHOLD = 30   # 이 턴 수 초과 시 요약 압축 실행
KEEP_RECENT       = 10   # 압축 후 보존할 최근 턴 수 (verbatim)
SUMMARY_MARKER    = "[📦 이전 대화 요약]"

# 조기 핸드오프 경고 임계값 (규칙 5 — 세션 포화 전 알림)
HANDOFF_WARN_RATIO = 0.75  # COMPACT_THRESHOLD의 75% 도달 시 경고

class HistoryManager:
    def __init__(self, file_path):
        self.file_path = file_path
        self.history = []
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                self.history = json.load(f)

    def add_message(self, role, content):
        self.history.append({"role": role, "content": content})
        # 저장 시 최근 20턴만 유지 (기존 동작 보존)
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(self.history[-20:], f, ensure_ascii=False)

    def get_history_for_llm(self):
        """LLM에 전달할 히스토리 반환.

        COMPACT_THRESHOLD 초과 시 오래된 대화를 요약 메시지 1개로 압축.
        → 토큰 낭비 방지 + 핵심 맥락 보존.
        """
        h = self.history[-40:]  # 최대 40턴까지만 검토
        if len(h) <= COMPACT_THRESHOLD:
            return h[-10:]  # 기존 동작: 최근 10턴

        # 압축: 오래된 부분 요약 → 최근 KEEP_RECENT턴 보존
        old_turns = h[:-KEEP_RECENT]
        recent_turns = h[-KEEP_RECENT:]

        # 요약 텍스트 구성 (LLM 호출 없이 간단 텍스트 압축)
        summary_lines = [f"{SUMMARY_MARKER} (총 {len(old_turns)}턴 압축)"]
        for turn in old_turns:
            role_label = "사용자" if turn["role"] == "user" else "어시스턴트"
            snippet = str(turn["content"])[:120].replace("\n", " ")
            summary_lines.append(f"- [{role_label}] {snippet}…")

        summary_msg = {
            "role": "system",
            "content": "\n".join(summary_lines)
        }
        return [summary_msg] + recent_turns

    def get_context_pressure(self) -> dict:
        """컨텍스트 포화도 반환. 규칙 5: 세션 터지기 전 미리 알림.

        Returns:
            {turns: int, ratio: float, warn: bool, critical: bool}
        """
        turns = len(self.history)
        ratio = turns / COMPACT_THRESHOLD
        return {
            "turns": turns,
            "ratio": round(ratio, 2),
            "warn":     ratio >= HANDOFF_WARN_RATIO,   # 75%+ → 경고
            "critical": ratio >= 1.0,                  # 100%+ → 압축 실행
        }

    def compact_and_save(self):
        """강제 압축 저장 — 외부에서 명시적 호출 가능."""
        h = self.history
        if len(h) <= COMPACT_THRESHOLD:
            return  # 압축 불필요
        old_turns = h[:-KEEP_RECENT]
        recent_turns = h[-KEEP_RECENT:]
        summary_lines = [f"{SUMMARY_MARKER} (총 {len(old_turns)}턴 압축)"]
        for turn in old_turns:
            role_label = "사용자" if turn["role"] == "user" else "어시스턴트"
            snippet = str(turn["content"])[:200].replace("\n", " ")
            summary_lines.append(f"- [{role_label}] {snippet}…")
        self.history = [{"role": "system", "content": "\n".join(summary_lines)}] + recent_turns
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(self.history, f, ensure_ascii=False)
