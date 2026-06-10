"""
git_manager.py — Git 저장소 관리 모듈
=======================================
옵시디언 보관소의 Git 동기화를 담당합니다.
harness_agent.py에서 인스턴스화하여 사용합니다.

[Critical Fix 2026-05-21]
이 파일이 누락되어 harness_agent.py의 전체 모듈 Import가
try/except에 의해 조용히 실패하는 치명적 버그가 있었습니다.
"""
import subprocess
import os
import logging

log = logging.getLogger("git_manager")


class GitManager:
    def __init__(self, repo_path: str, remote_name: str = "origin"):
        self.repo_path = os.path.abspath(repo_path)
        self.remote_name = remote_name

    def _run(self, args: list[str], timeout: int = 30) -> tuple[str, str, int]:
        """Git 명령어 실행 래퍼."""
        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.stdout.strip(), result.stderr.strip(), result.returncode
        except subprocess.TimeoutExpired:
            return "", "Timeout", -1
        except Exception as e:
            return "", str(e), -1

    def status(self) -> str:
        """현재 저장소 상태 요약."""
        out, err, rc = self._run(["status", "--short"])
        if rc != 0:
            return f"❌ Git 상태 조회 실패: {err}"
        if not out:
            return "✅ 깨끗한 상태 (변경 없음)"
        changed = len(out.strip().split("\n"))
        return f"📝 변경된 파일 {changed}개\n{out}"

    def add_commit(self, message: str = "Auto-sync by Hermes") -> str:
        """변경사항 스테이징 및 커밋."""
        self._run(["add", "-A"])
        out, err, rc = self._run(["commit", "-m", message])
        if rc != 0:
            if "nothing to commit" in err or "nothing to commit" in out:
                return "ℹ️ 커밋할 변경사항이 없습니다."
            return f"❌ 커밋 실패: {err}"
        return f"✅ 커밋 완료: {message}"

    def push(self, branch: str = "main") -> str:
        """원격 저장소로 Push."""
        out, err, rc = self._run(["push", self.remote_name, branch], timeout=60)
        if rc != 0:
            return f"❌ Push 실패: {err}"
        return f"✅ Push 완료 ({self.remote_name}/{branch})"

    def pull(self, branch: str = "main") -> str:
        """원격 저장소에서 Pull."""
        out, err, rc = self._run(["pull", self.remote_name, branch], timeout=60)
        if rc != 0:
            return f"❌ Pull 실패: {err}"
        return f"✅ Pull 완료\n{out}"

    def sync(self, message: str = "Auto-sync by Hermes") -> str:
        """Pull → Add → Commit → Push 전체 동기화."""
        results = []
        results.append(self.pull())
        results.append(self.add_commit(message))
        results.append(self.push())
        return "\n".join(results)

    def log_recent(self, count: int = 5) -> str:
        """최근 커밋 이력."""
        out, err, rc = self._run(["log", f"--oneline", f"-{count}"])
        if rc != 0:
            return f"❌ 로그 조회 실패: {err}"
        return f"📋 최근 {count}개 커밋:\n{out}"
