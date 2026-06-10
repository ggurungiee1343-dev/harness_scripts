"""handlers._memory — 메모리/지식 명령어"""
import uuid, datetime, shutil
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from handlers._base import (router, cove_engine_instance, _audit_engine,
    logger, add_to_history, _call_llm, _get_mem_info, check_user,
    history_mgr, PENDING_TASKS, verifier, _reply_long, _edit_or_send_long)
async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ask 질문 - CoVe 팩트체크 팩터 실행"""
    if not await check_user(update):
        return

    if not context.args:
        await update.message.reply_text(
            '⚠️ 질문을 함께 입력해 주세요. 예: `/ask 세종대왕의 업적을 요약해줘`',
            parse_mode='Markdown'
        )
        return

    question = ' '.join(context.args)
    await add_to_history('user', question)

    # 히스토리 조회
    try:
        if history_mgr:
            history_data = history_mgr.get_history_for_llm()[-10:]
        else:
            history_data = []
    except Exception:
        history_data = []

    # CoVe 실행
    verified_ans, pending_actions = await verifier.process_query(
        question, history_data=history_data
    )

    await add_to_history('assistant', verified_ans)

    # 승인 대기 액션이 있으면 버튼 생성
    if pending_actions:
        keyboard = []
        for action in pending_actions:
            task_id = str(uuid.uuid4())[:8]
            PENDING_TASKS[task_id] = action

            action_name = action.get('action', '')
            args = action.get('args', {})

            if action_name == 'move_file':
                target = args.get('source_path') or args.get('path', '')
                btn_text = f"📦 이동 승인: {Path(target).name}"
            elif action_name == 'delete_file':
                target = args.get('path', '')
                btn_text = f"🗑️ 삭제 승인: {Path(target).name}"
            else:
                target = args.get('path', '파일')
                btn_text = f"✅ {action_name} 승인: {target}"

            keyboard.append([
                InlineKeyboardButton(
                    btn_text,
                    callback_data=task_id
                )
            ])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await _reply_long(
            update.message,
            f'{verified_ans}\n\n📋 **승인 대기 작업:**\n위 버튼을 눌러 작업을 승인하거나 거절하세요.',
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    else:
        await _reply_long(update.message, verified_ans, parse_mode='Markdown')

async def cmd_cove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/cove [devil] 질문 - 독립형 팩트체크 (devil: 반론 생성 모드)"""
    if not await check_user(update):
        return

    if not context.args:
        await update.message.reply_text(
            '⚠️ 질문을 입력해 주세요. 예: `/cove 이순신 장군의 주요 업적은?`\n'
            '🔄 `devil` 모드: `/cove devil <주장>` — 반론 생성',
            parse_mode='Markdown'
        )
        return

    # [8] devil 모드 — 반론 생성
    is_devil = context.args[0].lower() == 'devil'
    if is_devil:
        if len(context.args) < 2:
            await update.message.reply_text(
                '⚠️ 반론 생성 모드입니다. 주장을 입력해 주세요.\n'
                '예: `/cove devil AI가 인류를 멸망시킬 것이다`',
                parse_mode='Markdown'
            )
            return
        question = ' '.join(context.args[1:])
        await add_to_history('user', f'[devil] {question}')

        try:
            verified_ans, _ = await cove_engine_instance.process_query(
                f'다음 주장에 대한 반론(counterargument)을 생성하세요: "{question}"\n'
                f'해당 주장의 취약점을 지적하고, 반대 증거나 논리를 제시하세요.',
                mode='strict'
            )
            header = f"⚔️ **Devil's Advocate — 반론 생성**\n📌 원 주장: `{question}`\n\n"
            reply = header + verified_ans
        except Exception as e:
            logger.error(f'CoVe Devil Error: {e}')
            reply = f'❌ 반론 생성 중 에러 발생: {e}'

        await add_to_history('assistant', reply)
        await _reply_long(update.message, reply, parse_mode='Markdown')
        return

    question = ' '.join(context.args)
    await add_to_history('user', question)

    try:
        verified_ans, _ = await cove_engine_instance.process_query(
            question, mode='strict'
        )
    except Exception as e:
        logger.error(f'CoVe Error: {e}')
        verified_ans = f'❌ CoVe 실행 중 에러 발생: {e}'

    await add_to_history('assistant', verified_ans)
    await _reply_long(update.message, verified_ans, parse_mode='Markdown')

async def execute_dreaming_job(context, chat_id: int) -> None:
    """Dreaming 백그라운드 작업"""
    try:
        from memory_engine import MemoryEngine

        async def async_llm_func(prompt):
            from hybrid_router import router as _hr_router
            res, name = _hr_router.send_completion(prompt)
            return res, name

        history_data = history_mgr.get_history_for_llm()[-20:] if history_mgr else []
        engine = MemoryEngine()
        res = await engine.dream(
            history_data=history_data,
            llm_func=async_llm_func
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=res,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f'Dreaming job error: {e}')
        await context.bot.send_message(
            chat_id=chat_id,
            text=f'❌ Dreaming 백그라운드 작업 실패: {e}'
        )

async def cmd_dreaming(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/dreaming - 대화/작업 → Journal/Memory/hot.md 자동 분배"""
    if not await check_user(update):
        return

    msg = await update.effective_message.reply_text('🌙 Dreaming 엔진을 구동합니다...')

    try:
        from memory_engine import MemoryEngine

        async def async_llm_func(prompt):
            from hybrid_router import router as _hr_router
            res, name = _hr_router.send_completion(prompt)
            return res, name

        history_data = history_mgr.get_history_for_llm()[-20:] if history_mgr else []
        engine = MemoryEngine()
        res = await engine.dream(
            history_data=history_data,
            llm_func=async_llm_func
        )
        await _edit_or_send_long(msg, res, parse_mode='Markdown')
    except Exception as e:
        logger.error(f'Dreaming Error: {e}')
        await msg.edit_text(f'❌ Dreaming 실행 중 오류 발생: {e}')

async def cmd_clip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/clip [내용] - 텍스트를 Clippings 폴더에 .md 파일로 즉시 저장"""
    if not await check_user(update):
        return

    if not context.args:
        await update.message.reply_text(
            '⚠️ 사용법: `/clip [저장할 내용]`\n'
            '예: `/clip 양자역학에서 얽힘(entanglement)이란 두 입자의 상태가 서로 연결된 현상을 말한다.`',
            parse_mode='Markdown'
        )
        return

    content = ' '.join(context.args)
    now = datetime.datetime.now()
    date_str = now.strftime('%Y-%m-%d %H:%M')
    timestamp = now.strftime('%Y%m%d_%H%M%S')

    clippings_dir = '/Users/bluesea/Applications/Mjobsidian/Clippings'
    os.makedirs(clippings_dir, exist_ok=True)

    # 파일명 생성 (첫 20자 추출)
    first_line = content.split('\n')[0][:40].strip()
    safe_name = ''.join(c if c.isalnum() or c in ' _-.' else '_' for c in first_line)
    filename = f'clip_{timestamp}_{safe_name[:20]}.md'
    clip_path = os.path.join(clippings_dir, filename)

    md_content = (
        f'# Clipping ({date_str})\n\n'
        f'{content}\n\n'
        f'---\n'
        f'*텔레그램 /clip 으로 자동 저장*\n'
    )

    try:
        with open(clip_path, 'w', encoding='utf-8') as f:
            f.write(md_content)

        await update.message.reply_text(
            f'📎 **Clipping 저장 완료!**\n'
            f'📁 위치: `Clippings/{filename}`\n'
            f'⏰ {date_str}',
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f'Clip error: {e}')
        await update.message.reply_text(
            f'❌ 저장 중 오류 발생: {e}'
        )

async def cmd_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/goal [목표] - 장기 목표 설정 (Dreaming 시 헌법과 함께 감사)"""
    if not await check_user(update):
        return

    goal_file = os.path.expanduser('~/.hermes/active_goal.txt')
    
    if not context.args:
        if os.path.exists(goal_file):
            with open(goal_file, 'r', encoding='utf-8') as f:
                current_goal = f.read().strip()
            msg = f"🎯 **현재 설정된 목표:**\n{current_goal}\n\n목표를 새로 설정하려면 `/goal [새 목표]`를 입력하고, 삭제하려면 `/goal clear`를 입력하세요."
        else:
            msg = "⚠️ 현재 설정된 장기 목표가 없습니다.\n목표를 설정하려면 `/goal [목표 내용]`을 입력하세요."
        await update.message.reply_text(msg, parse_mode='Markdown')
        return

    subcmd = context.args[0].lower()
    if subcmd == 'clear':
        if os.path.exists(goal_file):
            os.remove(goal_file)
            await update.message.reply_text("🗑️ 현재 설정된 목표가 삭제되었습니다.", parse_mode='Markdown')
        else:
            await update.message.reply_text("⚠️ 삭제할 목표가 없습니다.", parse_mode='Markdown')
        return

    new_goal = ' '.join(context.args)
    try:
        os.makedirs(os.path.dirname(goal_file), exist_ok=True)
        with open(goal_file, 'w', encoding='utf-8') as f:
            f.write(new_goal)
        await update.message.reply_text(f"✅ **새로운 장기 목표가 설정되었습니다.**\n\n🎯 {new_goal}\n\n(오늘 밤 Dreaming 스케줄러가 이 목표의 진척도와 헌법 준수 여부를 평가합니다.)", parse_mode='Markdown')
    except Exception as e:
        logger.error(f'Goal command error: {e}')
        await update.message.reply_text(f'❌ 목표 설정 중 오류 발생: {e}')

async def _cmd_topmem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🧠 메모리 버튼: 현황 표시 + 캐시 정리(sudo purge) 원클릭"""
    try:
        mem_info = await _get_mem_info()
        report = (
            f'**💾 Mac Studio 메모리 현황**\n'
            f'총 용량: `{mem_info["total_gb"]:.1f} GB`\n'
            f'활성 사용: `{mem_info["used_gb"]:.1f} GB ({mem_info["used_pct"]:.1f}%)`\n'
            f'파일 캐시: `{mem_info["cached_gb"]:.1f} GB` ← 정리 대상\n'
            f'진짜 여유: `{mem_info["free_gb"]:.1f} GB`\n\n'
            f'🧹 **캐시 정리**를 누르면 파일 캐시 `{mem_info["cached_gb"]:.1f} GB`가\n'
            f'즉시 반환됩니다. 실행 중인 앱·파일은 전혀 영향 없습니다.'
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton('🧹 캐시 정리 실행 (sudo purge)', callback_data='mem_purge_confirm')
        ]])
        await update.message.reply_text(report, reply_markup=keyboard, parse_mode='Markdown')

    except Exception as e:
        logger.error(f'Memory query error: {e}')
        await update.message.reply_text(f'❌ 메모리 조회 중 오류: {e}')


async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """  /memory [forget|health] - L1/L2/L3 memory load status + refinement """
    if not await check_user(update):
        return

    subcmd = context.args[0] if context.args else None

    # /memory forget [confirm] — Forget 정책 실행
    if subcmd == 'forget':
        from modules.memory_refinement import auto_forget
        confirm = len(context.args) > 1 and context.args[1] == 'confirm'
        result = auto_forget(dry_run=not confirm)

        if not result["candidates"]:
            await update.message.reply_text("✅ Forget 대상이 없습니다. 메모리가 건강합니다.")
            return

        if confirm:
            await update.message.reply_text(
                f"🧹 <b>Forget 완료</b>\n\n"
                f"• 제거: {result['removed']}개\n"
                f"• 유지: {result['remaining']}개\n"
                f"• 정리 전: {result['total_before']}개",
                parse_mode="HTML",
            )
        else:
            lines = [f"🔍 <b>Forget 대상 (dry-run)</b> — {len(result['candidates'])}개\n"]
            for c in result["candidates"][:10]:
                lines.append(
                    f"  · <code>{c['id'][:12]}</code> ⭐{c['importance']:.1f} "
                    f"보유율 {c['retention']:.0%} ({c['days_old']}일 전)\n"
                    f"    {c['content']}"
                )
            lines.append(f"\n💡 실행: <code>/memory forget confirm</code>")
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        return

    # /memory health — 정제 상태
    if subcmd == 'health':
        from modules.memory_refinement import get_memory_health
        await update.message.reply_text(get_memory_health(), parse_mode="HTML")
        return

    # 자동 백업: L2 episodic_memory.json 수정 전 스냅샷
    l2_path = Path('~/.hermes/runtime/memory/episodic_memory.json').expanduser()
    backup_dir = l2_path.parent / 'backups'
    if l2_path.exists():
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_path = backup_dir / f'episodic_memory_{ts}.json'
        shutil.copy2(str(l2_path), str(backup_path))
        # 오래된 백업 정리 (최근 5개만 유지)
        backups = sorted(backup_dir.glob('episodic_memory_*.json'), reverse=True)
        for old in backups[5:]:
            old.unlink(missing_ok=True)

    paths = {
        'L1 (\ub2e8\uae30)': Path('~/Applications/Mjauto/Scripts/harness_memory.json'),
        'L2 (\uc5d0\ud53c\uc18c\ub4dc)': Path('~/.hermes/runtime/memory/episodic_memory.json'),
        'L3 (\uc758\ubbf8)': Path('~/.hermes/runtime/memory/semantic_memory.json'),
    }
    config_path = Path('~/.hermes/runtime/memory/bio_memory_config.json')

    lines = ['**\U0001f9e0 Hermes \uba54\ubaa8\ub9ac \ud604\ud669**']
    try:
        cfg = {}
        if config_path.expanduser().exists():
            import json
            with open(config_path.expanduser()) as f:
                cfg = json.load(f)
        dream_enabled = cfg.get('auto_dreaming', {}).get('enabled', '?')
        dream_interval = cfg.get('auto_dreaming', {}).get('interval_minutes', '?')
        lines.append('\u2699\ufe0f \uc790\ub3d9 Dreaming: `{}` | \uac04\uaca9: `{}`\ubd84'.format(dream_enabled, dream_interval))
    except Exception:
        lines.append('\uac24\uc0c9 :   ')  # fallback

    for tier, p in paths.items():
        expanded = p.expanduser()
        if not expanded.exists():
            lines.append('\u274c {}: \ud30c\uc77c \uc5c6\uc74c (`{}`)'.format(tier, expanded))
            continue
        try:
            stat = expanded.stat()
            kb = stat.st_size / 1024
            mtime = datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%m-%d %H:%M')
            raw = expanded.read_text(encoding='utf-8').strip()
            item_count = '?'
            if raw:
                import json as _json
                data = _json.loads(raw)
                if isinstance(data, dict):
                    keys = ['episodes', 'events', 'items', 'memories', 'entries']
                    item_count = sum(len(data[k]) for k in keys if isinstance(data.get(k), list))
                    if item_count == 0 and data:
                        item_count = len(data)
                elif isinstance(data, list):
                    item_count = len(data)
            lines.append('\u2705 {}: {} \ud56d\ubaa9 | {:.1f}KB | \ucd5c\uadfc {}'.format(tier, item_count, kb, mtime))
        except Exception as e:
            lines.append('\u26a0\ufe0f {}: \uc77d\uae30 \uc624\ub958 \u2014 {}'.format(tier, e))

    lines.append('')
    lines.append('\U0001f4cc L1 \uacbd\ub85c: `~/Mjauto/Scripts/harness_memory.json`')
    lines.append('\U0001f4cc L2 \uacbd\ub85c: `~/.hermes/runtime/memory/episodic_memory.json`')
    lines.append('\U0001f4cc L3 \uacbd\ub85c: `~/.hermes/runtime/memory/semantic_memory.json`')
    lines.append('')
    lines.append('\U0001f4a1 `/memory_dream` \u2014 Dreaming \uc218\ub3d9 \uc2e4\ud589')

    await update.message.reply_text('\n'.join(lines), parse_mode='Markdown')


async def cmd_ask_logic(question: str) -> str:
    """CoVe 팩트체크 기반 질의응답 핵심 로직 (외부 리듀서 호출용)"""
    try:
        if history_mgr:
            history_data = history_mgr.get_history_for_llm()[-10:]
        else:
            history_data = []
    except Exception:
        history_data = []

    verified_ans, _ = await verifier.process_query(
        question, history_data=history_data
    )

    # v9.x fallback: CoVe 빈 결과 시 LLM 직접 호출
    if not verified_ans or not verified_ans.strip():
        verified_ans = await _call_llm(question)

    return verified_ans
