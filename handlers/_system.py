"""handlers._system — 시스템 명령어"""
import os, subprocess, datetime, asyncio, uuid
from modules.weakness_miner import get_weakness_miner
from telegram import Update
from telegram.ext import ContextTypes
from handlers._base import (router, cove_engine_instance, _audit_engine, safe_reply, safe_edit,
    logger, add_to_history, _call_llm, _get_mem_info, check_user,
    BASE_DIR, execute_bash_command, SystemMonitor, _reply_long)
from harness_agent import (get_current_mode, set_mode, ALL_MODES, MODE_LABELS,
    MODE_FILE, is_auto_fallback)
async def cmd_exec(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/exec 명령어 - 자율 에러 복구형 Bash 실행 (SKILL.md 자동 학습)"""
    if not await check_user(update):
        return

    if not context.args:
        await safe_reply(update.message, 
            '⚠️ 실행할 명령어를 함께 입력해 주세요. 예: `/exec ls -la`',
            parse_mode='HTML'
        )
        return

    cmd = ' '.join(context.args)
    msg = await safe_reply(update.message, 
        f'⚙️ **명령어 자율 실행 중...**\n'
        f'에러 발생 시 Gemma4가 최대 3회까지 수정하여 재실행하며,\n'
        f'복구 성공 시 SKILL.md가 자동으로 학습 DB에 저장됩니다.\n'
        f'`{cmd}`',
        parse_mode='HTML'
    )

    try:
        exec_res = await execute_bash_command(cmd)
        # 결과 포맷
        stdout = exec_res.get('stdout', '')
        stderr = exec_res.get('stderr', '')
        exit_code = exec_res.get('exit_code', -1)

        result_text = f'📋 **실행 결과 (Exit: {exit_code})**\n\n'
        if stdout:
            result_text += f'```\n{stdout[:2000]}\n```\n'
        if stderr:
            result_text += f'⚠️ **Stderr:**\n```\n{stderr[:1000]}\n```\n'
        if stdout and len(stdout) > 2000:
            result_text += '\n...(이하 생략 - 로그 길이가 너무 깁니다)...'

        await safe_edit(msg, result_text, parse_mode='HTML')
    except Exception as e:
        logger.error(f'Exec error: {e}')
        await get_weakness_miner().record_failure("cmd_exec", str(e))
        await safe_edit(msg, 
            f'❌ 실행 중 치명적 예외 발생: {e}',
            parse_mode='HTML'
        )

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status [KEY=VALUE ...] — 시스템 상태 점검 및 핫 키-값 설정
    사용법:
      /status          — 시스템 건강 + 핫토픽 표시
      /status K=V      — hot.md에 K=V 기록 (여러 개 가능)
      /status reset    — 실시간 상태 섹션 초기화
    """
    if not await check_user(update):
        return

    try:
        # ── KEY=VALUE 모드 ─────────────────────────────────────────
        if context.args:
            args_str = ' '.join(context.args)
            if args_str.strip() == 'reset':
                # 실시간 상태 섹션 리셋
                _update_hot_kv([])
                await safe_reply(update.message, 
                    "🗑️ **실시간 상태 초기화 완료**\nhot.md KV 섹션이 비워졌습니다.",
                    parse_mode='HTML'
                )
                return

            pairs = []
            for arg in context.args:
                if '=' in arg:
                    k, _, v = arg.partition('=')
                    pairs.append((k.strip(), v.strip()))
                else:
                    await safe_reply(update.message, 
                        f"⚠️ 잘못된 형식: `{arg}`\n사용법: `/status KEY=VALUE`",
                        parse_mode='HTML'
                    )
                    return

            if pairs:
                _update_hot_kv(pairs)
                kv_lines = '\n'.join(f"  • `{k}`: {v}" for k, v in pairs)
                await safe_reply(update.message, 
                    f"✅ **상태 업데이트 완료**\n{kv_lines}",
                    parse_mode='HTML'
                )
            return

        # ── 읽기 모드 ─────────────────────────────────────────────
        sys_monitor = SystemMonitor()
        health_ok, health_msg = sys_monitor.check_health()

        report = f'📊 **시스템 진단 리포트**\n\n'
        report += f'🩺 **건강 상태:** {"✅ 정상" if health_ok else "⚠️ 주의"}\n'
        report += f'{health_msg}\n\n'

        # 핫토픽 파일 확인
        status_file = os.path.join(BASE_DIR, '..', 'wiki', '00_Meta', 'hot.md')
        status_file = os.path.abspath(status_file)

        if os.path.exists(status_file):
            with open(status_file, 'r', encoding='utf-8') as f:
                content = f.read(1000)
            report += f'🔥 **핫토픽:**\n{content[:600]}'
        else:
            report += '🔥 핫토픽 파일 없음'

        await _reply_long(update.message, report, parse_mode='HTML')
    except Exception as e:
        logger.error(f'Status error: {e}')
        await get_weakness_miner().record_failure("cmd_status", str(e))
        await safe_reply(update.message, f'❌ 상태 조회 중 오류: {e}')


def _update_hot_kv(pairs: list) -> None:
    """hot.md의 '🔥 실시간 상태' 섹션에 KEY=VALUE 기록/갱신"""
    status_file = os.path.join(BASE_DIR, '..', 'wiki', '00_Meta', 'hot.md')
    status_file = os.path.abspath(status_file)

    if os.path.exists(status_file):
        with open(status_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    else:
        lines = []

    # 기존 KV 섹션 찾기 및 교체
    kv_start = None
    kv_end = None
    for i, line in enumerate(lines):
        if line.strip().startswith('## 🔥 실시간 상태'):
            kv_start = i
        if kv_start is not None and i > kv_start and line.strip().startswith('## '):
            kv_end = i
            break
    if kv_end is None and kv_start is not None:
        kv_end = len(lines)

    new_section = ['## 🔥 실시간 상태\n', f'*최종 업데이트: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}*\n\n']
    for k, v in pairs:
        new_section.append(f'- **{k}**: {v}\n')
    new_section.append('\n')

    if kv_start is not None:
        # 기존 섹션 교체
        lines = lines[:kv_start] + new_section + (lines[kv_end:] if kv_end else [])
    else:
        # 새 섹션 추가 (hot.md 내용 위에)
        # --- 구분선 찾아서 그 위에 삽입
        sep_idx = None
        for i, line in enumerate(lines):
            if line.strip() == '---':
                sep_idx = i
                break
        if sep_idx is not None:
            lines = lines[:sep_idx] + new_section + ['---\n'] + lines[sep_idx+1:]
        else:
            lines = new_section + lines

    with open(status_file, 'w', encoding='utf-8') as f:
        f.writelines(lines)

_delegate_tasks = {}

async def cmd_delegate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/delegate [작업 설명] — 백그라운드 비동기 위임 실행 + audit 기록"""
    if not await check_user(update):
        return

    if not context.args:
        if not _delegate_tasks:
            await safe_reply(update.message, 
                "📋 현재 실행 중인 delegate 작업이 없습니다.\n"
                "사용법: `/delegate <작업 설명>`",
                parse_mode='HTML'
            )
        else:
            lines = ["📋 **실행 중인 delegate 작업:**\n"]
            for tid, info in list(_delegate_tasks.items()):
                lines.append(f"• `{tid}`: {info.get('description', '?')} — {info.get('status', '?')}")
            await safe_reply(update.message, "\n".join(lines), parse_mode='HTML')
        return

    description = ' '.join(context.args)
    task_id = str(uuid.uuid4())[:8]

    msg = await safe_reply(update.message, 
        f"🔄 **delegate 작업 시작**\n📋 `{task_id}`: {description}\n⏳ 실행 중...",
        parse_mode='HTML'
    )

    async def _run_delegate(tid: str, desc: str, chat_id: int, message_id: int):
        try:
            _delegate_tasks[tid] = {"description": desc, "status": "running", "started": str(datetime.datetime.now())}
            result_lines = [f"📋 **delegate 결과 ({tid})**\n"]
            result_lines.append(f"📝 작업: {desc}\n")

            # ── 실행 전략 판별 ────────────────────────────────────────
            # bash 명령어 패턴: 파이프, 리다이렉션, 일반적인 CLI 명령
            import re
            _shell_pattern = re.compile(
                r'^(ls|cat|ps|df|du|top|htop|free|uname|whoami|id|pwd|echo|'
                r'grep|find|head|tail|wc|sort|cut|tr|diff|ping|curl|wget|'
                r'git|python|pip|npm|node|docker|brew|chmod|chown|mkdir|'
                r'rmdir|cp|mv|rm|kill|nohup|systemctl|launchctl|journalctl|'
                r'dmesg|ifconfig|ip|netstat|ss|scp|ssh|rsync|tar|gzip|'
                r'python3|node|hermes|htop)'
            )
            first_word = desc.strip().split()[0] if desc.strip() else ""
            is_shell = bool(_shell_pattern.match(first_word)) or '|' in desc or '>' in desc or '&&' in desc

            if is_shell:
                # ── 실행 경로 1: Bash 명령어 실행 ─────────────────────
                result_lines.append(f"💻 **실행**: `{desc}`\n")
                await context.bot.edit_message_text(
                    "\n".join(result_lines) + "\n⏳ 실행 중...",
                    chat_id=chat_id, message_id=message_id, parse_mode='HTML'
                )
                exec_res = await execute_bash_command(desc)
                stdout = exec_res.get('stdout', '')
                stderr = exec_res.get('stderr', '')
                exit_code = exec_res.get('exit_code', -1)

                if exit_code == 0:
                    _delegate_tasks[tid]["status"] = "completed"
                    result_lines.append(f"✅ 종료 코드: `{exit_code}`\n")
                    if stdout:
                        # stdout 길이 제한 (3000자)
                        _out = stdout[:3000]
                        if len(stdout) > 3000:
                            _out += "\n\n… (출력이 잘렸습니다)"
                        result_lines.append(f"```\n{_out}\n```")
                else:
                    _delegate_tasks[tid]["status"] = f"error(exit={exit_code})"
                    result_lines.append(f"❌ 종료 코드: `{exit_code}`")
                    if stderr:
                        result_lines.append(f"```\n{stderr[:2000]}\n```")
                    if stdout:
                        result_lines.append(f"```\n{stdout[:1000]}\n```")

                _audit_engine.log_audit("delegate", tid, {
                    "description": desc,
                    "type": "bash",
                    "exit_code": exit_code,
                    "status": "completed" if exit_code == 0 else "error"
                })
            else:
                # ── 실행 경로 2: LLM 위임 ─────────────────────────────
                result_lines.append(f"🧠 **LLM 위임 처리 중...**\n")
                await context.bot.edit_message_text(
                    "\n".join(result_lines) + "\n⏳ LLM 응답 대기 중...",
                    chat_id=chat_id, message_id=message_id, parse_mode='HTML'
                )
                llm_messages = [
                    {"role": "system", "content": "당신은 Hermes 시스템의 delegate 작업 에이전트입니다. 주어진 작업을 분석하고 실행 결과 또는 답변을 제공하세요. 가능하면 구체적인 실행 계획과 결과를 포함하세요."},
                    {"role": "user", "content": f"다음 delegate 작업을 처리해주세요:\n\n{desc}"}
                ]
                llm_result = await router.route_and_execute(llm_messages)
                result_text = str(llm_result)[:3500]
                if len(str(llm_result)) > 3500:
                    result_text += "\n\n… (응답이 잘렸습니다)"

                result_lines.append(f"🧠 **LLM 응답**\n\n{result_text}")
                _delegate_tasks[tid]["status"] = "completed"

                _audit_engine.log_audit("delegate", tid, {
                    "description": desc,
                    "type": "llm",
                    "response_length": len(str(llm_result)),
                    "status": "completed"
                })

            _delegate_tasks[tid]["result"] = result_lines[-1][:200] if result_lines else ""
            await context.bot.edit_message_text(
                "\n".join(result_lines),
                chat_id=chat_id, message_id=message_id, parse_mode='HTML'
            )

        except Exception as e:
            logger.error(f"Delegate task {tid} error: {e}", exc_info=True)
            _delegate_tasks[tid]["status"] = f"error: {e}"
            try:
                await context.bot.edit_message_text(
                    f"❌ delegate 작업 `{tid}` 실패: {e}",
                    chat_id=chat_id, message_id=message_id, parse_mode='HTML'
                )
            except Exception:
                pass
            _audit_engine.log_audit("delegate", tid, {
                "description": desc, "status": "error", "error": str(e)
            })
        finally:
            await asyncio.sleep(300)
            _delegate_tasks.pop(tid, None)

    asyncio.create_task(_run_delegate(task_id, description, update.effective_chat.id, msg.message_id))

async def cmd_audit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/audit — 시스템 감사: 포트 상태, SSH 키, 환경 변수"""
    if not await check_user(update):
        return

    msg = await safe_reply(update.message, "🔍 시스템 감사 실행 중...")

    try:
        report = _audit_engine.infra_audit()

        lines = ["🏛️ **인프라 감사 리포트**\n"]

        # 포트 상태
        lines.append("🔌 **포트 상태:**")
        for name, status in report.get("ports", {}).items():
            lines.append(f"  {status} {name}")
        lines.append("")

        # SSH 키
        keys = report.get("ssh_keys", [])
        lines.append(f"🔑 **SSH 키 ({len(keys)}개):**")
        for k in keys[:5]:
            lines.append(f"  • `{k['file']}` ({k['type']})")
        if len(keys) > 5:
            lines.append(f"  ... 외 {len(keys)-5}개")
        lines.append("")

        # 환경 변수
        lines.append("🔐 **환경 변수:**")
        for var, status in report.get("env_keys", {}).items():
            lines.append(f"  {status} {var}")
        lines.append("")

        # 방화벽
        fw = report.get("firewall", "❓ 확인 불가")
        lines.append(f"🛡️ **방화벽:** {fw}")

        await safe_edit(msg, "\n".join(lines), parse_mode='HTML')
        _audit_engine.log_audit("cmd_audit", "user_request", {"status": "completed"})
    except Exception as e:
        logger.error(f'cmd_audit error: {e}')
        await get_weakness_miner().record_failure("cmd_audit", str(e))
        await safe_edit(msg, f"❌ 감사 실행 중 오류: {e}")

async def cmd_secreview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/secreview [경로] — 보안 코드 리뷰 (git diff 분석, Phase1→2→3, 80% 신뢰도 이상만 보고)"""
    if not await check_user(update):
        return

    target_path = ' '.join(context.args) if context.args else str(BASE_DIR)

    msg = await safe_reply(update.message, 
        f"🔐 보안 코드 리뷰 시작...\n📂 대상: `{target_path}`\n⏳ git diff 수집 중",
        parse_mode='HTML'
    )

    try:
        # Phase 1: git diff 수집
        diff_cmd = ['git', '-C', str(BASE_DIR), 'diff']
        result = subprocess.run(diff_cmd, capture_output=True, text=True, timeout=10)
        git_diff = result.stdout if result.returncode == 0 else "⚠️ git diff 수집 실패"
        if not git_diff.strip() or git_diff.startswith("⚠️"):
            await safe_edit(msg, "📭 변경된 파일이 없습니다. 리뷰할 내용이 없습니다.")
            return

        # Phase 2: DeepSeek/CoVe 보안 분석
        await safe_edit(msg, "🔍 AI 보안 분석 진행 중... (Phase 2/3)")

        analysis_prompt = (
            f"다음 git diff를 보안 관점에서 분석하세요. Phase 1→2→3 단계로 진행:\n"
            f"Phase 1: 식별된 보안 이슈 목록화\n"
            f"Phase 2: 각 이슈의 심각도 분류 (CRITICAL/HIGH/MEDIUM/LOW)\n"
            f"Phase 3: 신뢰도 80% 이상인 항목만 최종 보고\n\n"
            f"분석할 git diff:\n```diff\n{git_diff[:4000]}\n```\n\n"
            f"보고 형식:\n"
            f"## 🔐 보안 코드 리뷰 결과\n"
            f"### 위험도별 요약\n"
            f"- 🔴 CRITICAL: N건\n"
            f"- 🟠 HIGH: N건\n"
            f"- 🟡 MEDIUM: N건\n"
            f"- 🔵 LOW: N건\n\n"
            f"### 상세 분석\n"
            f"(신뢰도 80%+ 항목만)\n"
            f"- 🔴 [CRITICAL][XX%] 제목\n"
            f"  - 위치: 파일명:라인\n"
            f"  - 설명: ...\n"
            f"  - 권장 조치: ..."
        )

        verified, _ = await cove_engine_instance.process_query(analysis_prompt, mode='balanced')
        _audit_engine.log_audit("secreview", target_path, {"status": "completed"})

        reply = f"🔐 **보안 코드 리뷰 완료**\n📂 대상: `{target_path}`\n\n{verified}"
        if len(reply) > 4000:
            reply = reply[:4000] + "\n\n... (결과가 깁니다. 일부만 표시)"

        await safe_edit(msg, reply, parse_mode='HTML')
    except Exception as e:
        logger.error(f'cmd_secreview error: {e}')
        await safe_edit(msg, f"❌ 보안 리뷰 중 오류: {e}")


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/model [list|switch <mode>] - 실시간 모델 전환"""
    if not await check_user(update):
        return

    cur_mode = get_current_mode()
    fallback_active = is_auto_fallback()

    if not context.args:
        # 현재 상태 표시
        lines = [f"**🤖 현재 모드: {MODE_LABELS.get(cur_mode, cur_mode)}**"]
        if fallback_active:
            lines.append("⚠️ 자동 폴백 활성 상태")
        lines.append(f"\n**사용 가능한 모드:**")
        for m in ALL_MODES:
            label = MODE_LABELS.get(m, m)
            marker = " ✅ (현재)" if m == cur_mode else ""
            lines.append(f"- `/model switch {m}` → {label}{marker}")
        lines.append(f"\n파일: `{MODE_FILE}`")
        await safe_reply(update.message, "\n".join(lines), parse_mode='HTML')
        return

    action = context.args[0].lower()
    if action == "list":
        lines = [f"**🤖 모드 목록**\n"]
        for m in ALL_MODES:
            label = MODE_LABELS.get(m, m)
            marker = " ✅ (현재)" if m == cur_mode else ""
            lines.append(f"- {label}{marker}")
        if fallback_active:
            lines.append("\n⚠️ 자동 폴백 활성 — 실제 사용 중인 엔진이 다를 수 있음")
        await safe_reply(update.message, "\n".join(lines), parse_mode='HTML')

    elif action == "switch":
        if len(context.args) < 2:
            await safe_reply(update.message, 
                "⚠️ 전환할 모드를 입력하세요. 예: `/model switch Gemma4`\n"
                f"가능: {', '.join(ALL_MODES)}",
                parse_mode='HTML'
            )
            return
        target = context.args[1]
        if target not in ALL_MODES:
            await safe_reply(update.message, 
                f"⚠️ `{target}`은(는) 유효한 모드가 아닙니다.\n"
                f"가능: {', '.join(ALL_MODES)}",
                parse_mode='HTML'
            )
            return
        set_mode(target)
        await safe_reply(update.message, 
            f"✅ **모드 전환 완료**\n"
            f"{MODE_LABELS.get(cur_mode, cur_mode)} → **{MODE_LABELS.get(target, target)}**\n"
            f"파일: `{MODE_FILE}`",
            parse_mode='HTML'
        )

    else:
        await safe_reply(update.message, 
            f"⚠️ 알 수 없는 액션: `{action}`\n"
            f"사용법: `/model` (현재 상태), `/model list` (목록), `/model switch <mode>` (전환)",
            parse_mode='HTML'
        )


async def cmd_caveman(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/caveman [on|off|status] - 시스템 프롬프트 압축 모드 (토큰 75% 절감)"""
    if not await check_user(update):
        return

    CAVEMAN_FILE = "/Users/bluesea/.hermes/caveman.txt"

    def get_caveman_status():
        if os.path.exists(CAVEMAN_FILE):
            return open(CAVEMAN_FILE).read().strip() == "on"
        return False

    if not context.args or context.args[0] == "status":
        enabled = get_caveman_status()
        status = "🦴 **ON** (압축 모드 활성)" if enabled else "🧑 **OFF** (일반 모드)"
        await safe_reply(update.message, 
            f"{status}\n\n"
            f"사용법: `/caveman on` → 활성화\n"
            f"         `/caveman off` → 비활성화\n"
            f"파일: `{CAVEMAN_FILE}`",
            parse_mode='HTML'
        )
        return

    action = context.args[0].lower()
    if action == "on":
        with open(CAVEMAN_FILE, "w") as f:
            f.write("on")
        await safe_reply(update.message, 
            "🦴 **Caveman 모드 활성화**\n"
            "시스템 프롬프트가 압축되어 토큰 사용량이 약 75% 절감됩니다.\n"
            "→ 다음 대화부터 적용됩니다.",
            parse_mode='HTML'
        )
    elif action == "off":
        if os.path.exists(CAVEMAN_FILE):
            os.remove(CAVEMAN_FILE)
        await safe_reply(update.message, 
            "🧑 **Caveman 모드 비활성화**\n"
            "일반 시스템 프롬프트로 복귀합니다.\n"
            "→ 다음 대화부터 적용됩니다.",
            parse_mode='HTML'
        )
    else:
        await safe_reply(update.message, 
            f"⚠️ 알 수 없는 액션: `{action}`\n"
            f"사용법: `/caveman on|off|status`",
            parse_mode='HTML'
        )


