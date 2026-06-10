"""
agent_harness.py — ACID 기반 에이전트 제어 하네스 (Harness V2.5 전용)
=======================================================================
박사님의 Mac Studio 지능화 프로젝트에서 에이전트의 탈주·오작동을 방지하는
6-레이어 방어 인프라입니다.

레이어 구조:
  Layer 1 — Single Source of Truth (단일 진실 원천)
  Layer 2 — AGENTS.md / 지식 가시성 체크 (Cold-Start Test)
  Layer 3 — 지식 부패 방지 (Staleness Guard)
  Layer 4 — 기계 검증 DoD (Definition of Done)
  Layer 5 — ACID 상태 관리 (Git 격리 트랜잭션)
  Layer 6 — 진단 루프 (Diagnostic Loop)

사용법:
  python agent_harness.py                      # 대화형 CLI 모드
  python agent_harness.py --task "작업명" --auto-verify  # 자동 검증 모드
  python agent_harness.py --status             # 마지막 진단 로그 출력
"""

import os
import sys
import subprocess
import json
import argparse
import hashlib
from datetime import datetime, timezone
from pathlib import Path

# ============================================================
# ⚙️  환경 설정 — harness_config.py 없으면 여기서 직접 수정
# ============================================================
try:
    from harness_config import HarnessConfig
    CFG = HarnessConfig()
except ImportError:
    class HarnessConfig:
        # Mac Studio 기본 경로
        PROJECT_ROOT       = Path("/Users/bluesea/Applications/Mjauto")
        VAULT_PATH         = Path("/Users/bluesea/Applications/Mjobsidian")
        SCRIPTS_DIR        = PROJECT_ROOT / "Scripts"
        MODULES_DIR        = SCRIPTS_DIR / "modules"
        PROGRESS_FILE      = PROJECT_ROOT / "PROGRESS.md"
        DIAGNOSTIC_LOG     = PROJECT_ROOT / "harness_diagnostic_log.json"
        STALENESS_DB       = PROJECT_ROOT / ".harness_staleness.json"

        # 필수 지식 문서 (Cold-Start 체크)
        REQUIRED_DOCS = [
            PROJECT_ROOT / "AGENTS.md",
            PROJECT_ROOT / "스크립트_정보.md",
        ]

        # 신선도 임계값 (일 단위) — 이 기간 넘으면 부패 경고
        STALENESS_THRESHOLD_DAYS = 7

        # 기계 검증 DoD 명령어 목록 (프로젝트에 맞게 수정)
        VERIFICATION_COMMANDS = [
            "python -m py_compile Scripts/hermes_local.py",   # 문법 검사
            "python -m py_compile Scripts/modules/executor.py",
        ]

        # 선택적 검증 (존재할 때만 실행)
        OPTIONAL_VERIFICATION = [
            "pytest Scripts/tests/ -q --tb=short",
            "flake8 Scripts/ --max-line-length=120 --exclude=__pycache__",
        ]

    CFG = HarnessConfig()

# ============================================================
# 🎨  ANSI 색상 헬퍼
# ============================================================
class C:
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    RED    = "\033[91m"
    CYAN   = "\033[96m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RESET  = "\033[0m"

def ok(msg):    print(f"{C.GREEN}✅ {msg}{C.RESET}")
def warn(msg):  print(f"{C.YELLOW}⚠️  {msg}{C.RESET}")
def err(msg):   print(f"{C.RED}❌ {msg}{C.RESET}")
def info(msg):  print(f"{C.CYAN}ℹ️  {msg}{C.RESET}")
def head(msg):  print(f"\n{C.BOLD}{C.CYAN}{'='*60}\n{msg}\n{'='*60}{C.RESET}")


# ============================================================
# 🛡️  AgentHarness 메인 클래스
# ============================================================
class AgentHarness:
    """ACID 원칙 기반 에이전트 실행 제어 하네스"""

    def __init__(self):
        self.project_root   = Path(CFG.PROJECT_ROOT)
        self.current_branch = self._run("git rev-parse --abbrev-ref HEAD",
                                        cwd=self.project_root) or "main"
        self.session_id     = datetime.now().strftime("%Y%m%d_%H%M%S")

    # ----------------------------------------------------------
    # 내부 유틸
    # ----------------------------------------------------------
    def _run(self, cmd: str, cwd=None, capture=True) -> str:
        """쉘 명령어 실행 → stdout 문자열 반환 (에러면 'ERROR:...' 반환)"""
        try:
            res = subprocess.run(
                cmd, shell=True, check=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, cwd=str(cwd or self.project_root)
            )
            return res.stdout.strip()
        except subprocess.CalledProcessError as e:
            return f"ERROR: {e.stderr.strip()}"

    def _run_verbose(self, cmd: str, cwd=None) -> tuple[bool, str]:
        """명령어 실행 → (성공여부, 출력) 반환"""
        try:
            res = subprocess.run(
                cmd, shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, cwd=str(cwd or self.project_root)
            )
            output = (res.stdout + res.stderr).strip()
            return res.returncode == 0, output
        except Exception as e:
            return False, str(e)

    def _file_hash(self, path: Path) -> str:
        """파일 MD5 해시 (신선도 추적용)"""
        if not path.exists():
            return ""
        return hashlib.md5(path.read_bytes()).hexdigest()

    def _ts(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # ----------------------------------------------------------
    # Layer 1 & 2: Cold-Start 준비도 + 지식 가시성 체크
    # ----------------------------------------------------------
    def check_readiness(self) -> bool:
        """
        Layer 1 — Single Source of Truth 확인
        Layer 2 — AGENTS.md + 필수 문서 존재 여부 (Cold-Start Test)
        """
        head("🔍 [Layer 1+2] Cold-Start 준비도 검증")

        # Git 저장소 확인
        git_ok = self._run("git status --short")
        if git_ok.startswith("ERROR"):
            err("이 디렉토리는 Git 저장소가 아닙니다.")
            info("👉 `git init` 을 먼저 실행하거나 올바른 프로젝트 루트로 이동하세요.")
            return False
        ok(f"Git 저장소 확인 완료 (현재 브랜치: {self.current_branch})")

        # 필수 문서 존재 확인
        missing = []
        for doc_path in CFG.REQUIRED_DOCS:
            p = Path(doc_path)
            if not p.exists():
                missing.append(str(p))
            else:
                ok(f"필수 문서 확인: {p.name}")

        if missing:
            err(f"[지식 가시성 격차] 필수 문서 누락: {missing}")
            info("👉 에이전트 진입 전 AGENTS.md 및 스크립트_정보.md를 먼저 생성하세요.")
            return False

        # Cold-Start 5문 체크리스트 출력
        print(f"\n{C.BOLD}💡 [Cold-Start 체크리스트] 에이전트가 다음 5가지에 답할 수 있는지 확인:{C.RESET}")
        checks = [
            "시스템 정의 (무엇을 만드는가?)",
            "구성 요소 (어떤 모듈이 있는가?)",
            "실행 방법 (어떻게 기동하는가?)",
            "검증 방법 (어떻게 DoD를 확인하는가?)",
            "현재 진행 상황 (PROGRESS.md / hot.md 상태)"
        ]
        for i, c in enumerate(checks, 1):
            print(f"  {C.DIM}{i}. {c}{C.RESET}")

        return True

    # ----------------------------------------------------------
    # Layer 3: 지식 부패 방지 (Staleness Guard)
    # ----------------------------------------------------------
    def check_staleness(self) -> bool:
        """
        Layer 3 — 필수 문서의 최종 수정일을 체크하여
        STALENESS_THRESHOLD_DAYS 초과 시 경고 발령
        """
        head("📅 [Layer 3] 지식 부패 방지 (Staleness Guard)")

        db_path = Path(CFG.STALENESS_DB)
        db: dict = {}
        if db_path.exists():
            try:
                db = json.loads(db_path.read_text(encoding="utf-8"))
            except Exception:
                db = {}

        now = datetime.now()
        stale_docs = []
        updated_db = {}

        for doc_path in CFG.REQUIRED_DOCS:
            p = Path(doc_path)
            if not p.exists():
                continue

            mtime = datetime.fromtimestamp(p.stat().st_mtime)
            age_days = (now - mtime).days
            current_hash = self._file_hash(p)

            prev = db.get(str(p), {})
            prev_hash = prev.get("hash", "")

            if age_days > CFG.STALENESS_THRESHOLD_DAYS and current_hash == prev_hash:
                warn(f"{p.name} — {age_days}일 동안 미수정 (부패 위험)")
                stale_docs.append(p.name)
            else:
                ok(f"{p.name} — 최근 수정: {mtime.strftime('%Y-%m-%d')} ({age_days}일 전)")

            updated_db[str(p)] = {"hash": current_hash, "mtime": mtime.isoformat()}

        # DB 갱신
        db_path.write_text(json.dumps(updated_db, ensure_ascii=False, indent=2), encoding="utf-8")

        if stale_docs:
            warn(f"지식 부패 감지된 문서: {stale_docs}")
            warn("작업 시작 전 위 문서들을 최신화하는 것을 강력히 권장합니다.")
            # 경고이지만 실행 중단은 하지 않음 (warn-only)
        else:
            ok("모든 필수 문서 신선도 양호")

        return True  # 경고만, 블록킹 안 함

    # ----------------------------------------------------------
    # Layer 5: ACID 격리 환경 준비 (Isolation)
    # ----------------------------------------------------------
    def prepare_task_environment(self, task_name: str) -> str:
        """
        Layer 5 — ACID Isolation: 격리된 Git 브랜치에서 작업
        Durability: PROGRESS.md 에 초기 상태 영속화
        """
        head(f"⚡ [Layer 5] ACID 격리 환경 준비: {task_name}")

        safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in task_name)
        branch_name = f"agent/{safe_name[:40]}-{self.session_id}"

        result = self._run(f"git checkout -b {branch_name}")
        if "ERROR" in result:
            # 이미 존재하면 checkout만
            self._run(f"git checkout {branch_name}")
            warn(f"브랜치 이미 존재 → 재사용: {branch_name}")
        else:
            ok(f"격리 브랜치 생성: {branch_name}")

        # PROGRESS.md 초기화 (Durability 확보)
        progress_path = Path(CFG.PROGRESS_FILE)
        progress_path.parent.mkdir(parents=True, exist_ok=True)
        progress_content = (
            f"# PROGRESS — {task_name}\n\n"
            f"- **Status**: IN_PROGRESS\n"
            f"- **Branch**: `{branch_name}`\n"
            f"- **Started**: {self._ts()}\n"
            f"- **Session**: {self.session_id}\n\n"
            f"## 작업 내용\n{task_name}\n\n"
            f"## 체크포인트\n- [ ] 작업 시작\n- [ ] DoD 검증\n- [ ] 커밋 완료\n"
        )
        progress_path.write_text(progress_content, encoding="utf-8")
        ok(f"PROGRESS.md 초기화 (Durability 확보)")

        return branch_name

    # ----------------------------------------------------------
    # Layer 4: 기계 검증 DoD (Definition of Done)
    # ----------------------------------------------------------
    def run_verification(self) -> tuple[bool, str]:
        """
        Layer 4 — Definition of Done: 모든 검증 명령어를 기계적으로 실행
        에이전트의 자기선언("다 했어요")을 절대 신뢰하지 않음
        """
        head("🧪 [Layer 4] 기계 검증 DoD (Definition of Done)")

        failed_cmd = ""
        failed_output = ""

        # 필수 검증
        for cmd in CFG.VERIFICATION_COMMANDS:
            info(f"실행 중 (필수): {cmd}")
            success, output = self._run_verbose(cmd)
            if success:
                ok(f"통과: {cmd}")
            else:
                err(f"실패: {cmd}")
                if output:
                    print(f"{C.DIM}{output[:500]}{C.RESET}")
                failed_cmd = cmd
                failed_output = output
                return False, f"[필수 검증 실패] {cmd}\n{output}"

        # 선택적 검증 (파일이 없으면 skip)
        for cmd in CFG.OPTIONAL_VERIFICATION:
            # 실행 대상 파일이 존재하는지 간단 체크
            target = cmd.split()[-1] if cmd.split() else ""
            if target and not Path(target.split("/")[0]).exists():
                info(f"건너뜀 (대상 없음): {cmd}")
                continue
            info(f"실행 중 (선택): {cmd}")
            success, output = self._run_verbose(cmd)
            if success:
                ok(f"통과: {cmd}")
            else:
                warn(f"선택 검증 미통과 (비블로킹): {cmd}")
                if output:
                    print(f"{C.DIM}{output[:300]}{C.RESET}")

        ok("모든 필수 DoD 검증 통과")
        return True, "ALL_PASS"

    # ----------------------------------------------------------
    # Layer 5: ACID 커밋 또는 롤백 (Atomicity + Consistency)
    # ----------------------------------------------------------
    def finalize(self, task_branch: str, verified: bool, task_name: str) -> bool:
        """
        검증 성공 → Atomic Commit
        검증 실패 → 완전 롤백 (임시 브랜치 폐기) + 진단 루프 가동
        """
        if verified:
            head("💾 [Layer 5] ACID 커밋 (Atomicity 확보)")

            # PROGRESS.md 완료 상태로 업데이트
            progress_path = Path(CFG.PROGRESS_FILE)
            if progress_path.exists():
                prev = progress_path.read_text(encoding="utf-8")
                prev = prev.replace("IN_PROGRESS", "SUCCESS")
                prev += f"\n- **Completed**: {self._ts()}\n"
                progress_path.write_text(prev, encoding="utf-8")

            self._run("git add -A")
            commit_msg = (
                f"feat(agent-harness): {task_name[:60]}\n\n"
                f"Session: {self.session_id}\n"
                f"DoD: ALL_PASS\n"
                f"Branch: {task_branch}"
            )
            result = self._run(f'git commit -m "{commit_msg}"')
            if "ERROR" in result or "nothing to commit" in result:
                if "nothing to commit" in result:
                    warn("커밋할 변경사항이 없습니다. (작업이 없었거나 이미 커밋됨)")
                else:
                    warn(f"커밋 실패: {result}")
            else:
                ok(f"커밋 완료: {task_branch}")

            info(f"👉 메인 브랜치로 병합하려면: git merge {task_branch}")
            return True

        else:
            head("🔄 [Layer 5] ACID 롤백 (반쪽짜리 변경 방지)")

            # 격리 브랜치의 모든 변경사항 강제 폐기 및 원래 브랜치 복귀
            self._run("git reset --hard HEAD")
            self._run(f"git checkout {self.current_branch}")
            
            # 실패한 에이전트 브랜치는 깨끗이 강제 삭제하여 Stash 누적 차단
            if task_branch != self.current_branch and task_branch.startswith("agent/"):
                self._run(f"git branch -D {task_branch}")
                ok(f"실패한 격리 브랜치 '{task_branch}' 완전 삭제 완료 (Stash 누적 없음)")
                
            warn(f"롤백 완료 → 원래 브랜치 '{self.current_branch}' 복귀")
            return False

    # ----------------------------------------------------------
    # Layer 6: 진단 루프 (Diagnostic Loop)
    # ----------------------------------------------------------
    def trigger_diagnostic_loop(self, task_branch: str, error_detail: str,
                                 task_name: str = "") -> dict:
        """
        Layer 6 — 실패 원인을 5대 레이어로 분류하여 JSON 로그에 누적 기록
        """
        head("📊 [Layer 6] 진단 루프 가동")

        print(f"{C.YELLOW}⚠️  에이전트 실패의 원인을 모델 탓으로 돌리기 전에 아래 레이어를 점검하세요.{C.RESET}\n")

        triage_guide = {
            "L1_Task_Specification": {
                "question": "지시가 모호했습니까?",
                "fix": "엔드포인트 명세, 입출력 예시, 예외 처리 방식을 더 명확히 기술하세요."
            },
            "L2_Context_Provision": {
                "question": "AGENTS.md나 암묵적 규칙 문서가 부패·누락되었습니까?",
                "fix": "스크립트_정보.md 및 모듈 README를 최신화하고 Staleness Guard를 재실행하세요."
            },
            "L3_Execution_Environment": {
                "question": "의존성 라이브러리(pip, npm) 버전 충돌이 있습니까?",
                "fix": "pip list, python --version 을 확인하고 requirements.txt를 갱신하세요."
            },
            "L4_Verification_Feedback": {
                "question": "에이전트에게 테스트 에러 로그가 왜곡 없이 전달되었습니까?",
                "fix": "VERIFICATION_COMMANDS 출력을 그대로 에이전트 컨텍스트에 삽입하세요."
            },
            "L5_State_Management": {
                "question": "컨텍스트 단절이나 세션 단절로 상태를 잃었습니까?",
                "fix": "harness_memory.json 및 PROGRESS.md를 에이전트에게 명시적으로 주입하세요."
            }
        }

        # 콘솔 출력
        for layer, info_d in triage_guide.items():
            print(f"  {C.BOLD}[{layer}]{C.RESET}")
            print(f"    {C.YELLOW}Q: {info_d['question']}{C.RESET}")
            print(f"    {C.CYAN}→ {info_d['fix']}{C.RESET}\n")

        # JSON 로그 누적
        report = {
            "timestamp"      : self._ts(),
            "session_id"     : self.session_id,
            "task_name"      : task_name,
            "failed_branch"  : task_branch,
            "raw_error"      : error_detail[:2000],
            "triage_guide"   : triage_guide,
        }

        log_path = Path(CFG.DIAGNOSTIC_LOG)
        logs: list = []
        if log_path.exists():
            try:
                logs = json.loads(log_path.read_text(encoding="utf-8"))
            except Exception:
                logs = []

        logs.append(report)
        # 최근 50개만 보관
        if len(logs) > 50:
            logs = logs[-50:]

        log_path.write_text(json.dumps(logs, ensure_ascii=False, indent=2), encoding="utf-8")
        ok(f"진단 보고서 저장: {log_path}")

        return report

    # ----------------------------------------------------------
    # 공개 API: 전체 하네스 파이프라인 실행
    # ----------------------------------------------------------
    def run(self, task_name: str, auto_verify: bool = False) -> bool:
        """
        완전한 ACID 하네스 파이프라인 실행:
        Layer 1+2 → Layer 3 → Layer 5 (격리) → 에이전트 작업 →
        Layer 4 (DoD) → Layer 5 (커밋/롤백) → [Layer 6 (진단)]
        """
        print(f"\n{C.BOLD}{C.CYAN}🛡️  ACID 에이전트 제어 하네스 v2.5  |  Session: {self.session_id}{C.RESET}")

        # Layer 1+2: 준비도 체크
        if not self.check_readiness():
            return False

        # Layer 3: 신선도 체크
        self.check_staleness()

        # Layer 5: 격리 브랜치 생성
        task_branch = self.prepare_task_environment(task_name)

        # 에이전트 작업 구간
        if not auto_verify:
            print(f"\n{C.BOLD}🤖 [에이전트 작업 구간]{C.RESET}")
            print(f"  현재 격리 브랜치 [{C.GREEN}{task_branch}{C.RESET}] 에서")
            print("  에이전트가 코드를 수정하거나 파일을 편집하게 하세요.\n")
            input(f"  {C.YELLOW}에이전트 작업 완료 후 [Enter]를 눌러 DoD 검증을 시작합니다...{C.RESET} ")

        # Layer 4: 기계 검증
        verified, error_detail = self.run_verification()

        # Layer 5: 커밋 또는 롤백
        success = self.finalize(task_branch, verified, task_name)

        # Layer 6: 실패 시 진단 루프
        if not success:
            self.trigger_diagnostic_loop(task_branch, error_detail, task_name)

        # 최종 결과 출력
        if success:
            print(f"\n{C.BOLD}{C.GREEN}🏁 하네스 제어 종료: 작업이 성공적으로 레코드에 반영되었습니다.{C.RESET}")
        else:
            print(f"\n{C.BOLD}{C.RED}🏁 하네스 제어 종료: 안전하게 롤백되었으며 진단 로그가 저장되었습니다.{C.RESET}")
            print(f"  {C.DIM}→ {CFG.DIAGNOSTIC_LOG}{C.RESET}")

        return success

    def show_last_diagnostic(self):
        """마지막 진단 보고서 출력"""
        log_path = Path(CFG.DIAGNOSTIC_LOG)
        if not log_path.exists():
            info("진단 로그가 없습니다. (아직 실패가 발생하지 않았거나 경로가 다릅니다.)")
            return
        logs = json.loads(log_path.read_text(encoding="utf-8"))
        if not logs:
            info("진단 로그가 비어 있습니다.")
            return
        last = logs[-1]
        head(f"📊 마지막 진단 보고서 ({last['timestamp']})")
        print(json.dumps(last, ensure_ascii=False, indent=2))


# ============================================================
# 🚀  CLI 진입점
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="ACID 기반 에이전트 제어 하네스 (Harness V2.5)"
    )
    parser.add_argument("--task", "-t", type=str, default="",
                        help="에이전트에게 부여할 작업 명세")
    parser.add_argument("--auto-verify", "-a", action="store_true",
                        help="에이전트 작업 대기 없이 즉시 DoD 검증 실행 (CI 모드)")
    parser.add_argument("--status", "-s", action="store_true",
                        help="마지막 진단 로그 출력")
    parser.add_argument("--readiness-only", "-r", action="store_true",
                        help="준비도 + 신선도 체크만 실행 (격리 없음)")
    args = parser.parse_args()

    harness = AgentHarness()

    if args.status:
        harness.show_last_diagnostic()
        return

    if args.readiness_only:
        ok_r = harness.check_readiness()
        harness.check_staleness()
        sys.exit(0 if ok_r else 1)

    # 작업명 결정
    task = args.task
    if not task:
        print(f"\n{C.BOLD}=== 하네스 에이전트 제어 쉘 ==={C.RESET}")
        task = input("📝 에이전트에게 부여할 작업 명세를 입력하세요: ").strip()
        if not task:
            err("작업 명세가 비어 있습니다. 종료합니다.")
            sys.exit(1)

    success = harness.run(task_name=task, auto_verify=args.auto_verify)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
