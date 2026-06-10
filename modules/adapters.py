"""
adapters.py — Hermes3 v9.0 UnifiedChannelAdapter (WeKnora 패턴)
================================================================
다중 채널 입력(Telegram, Discord, Slack, Notion Webhook, Cron 등)을
코어 엔진으로 통일된 방식으로 라우팅합니다.

WeKnora 설계 원칙 적용:
  - Factor 11: 단일 진입점 (UnifiedChannelAdapter._route_to_core)
  - 채널별 페이로드 정규화
  - 에러 채널 격리 (채널 오류가 코어에 전파되지 않음)
  - 메모리 영향: 0MB (stateless)

지원 채널:
  - Telegram (기존 핸들러 연동)
  - Discord 봇
  - Slack webhook
  - Notion 데이터베이스 webhook
  - HTTP Webhook (범용)
  - Cron 스케줄러 (자동 트리거)
"""
from __future__ import annotations

import logging
import time
from typing import Dict, Any, Optional

log = logging.getLogger("adapters")


# ── 정규화된 메시지 모델 ──────────────────────────────────────────────────

class NormalizedMessage:
    """
    채널 무관 정규화 메시지.
    어떤 채널에서 왔든 동일한 형태로 코어에 전달됩니다.
    dataclass 미사용 — Python 3.14 격리 임포트 호환성 보장.
    """

    def __init__(
        self,
        text: str,
        channel: str,
        user_id: str = "",
        username: str = "",
        chat_id: str = "",
        raw: Optional[Dict[str, Any]] = None,
        timestamp: Optional[float] = None,
    ):
        self.text      = text
        self.channel   = channel
        self.user_id   = user_id
        self.username  = username
        self.chat_id   = chat_id
        self.raw       = raw or {}
        self.timestamp = timestamp if timestamp is not None else time.time()

    def to_decision(self) -> Dict[str, Any]:
        """메시지를 ExecutionAgent 결정 딕셔너리로 변환."""
        text = self.text.strip()

        # 액션 자동 감지 (슬래시 명령어 / 한국어 prefix)
        if text.startswith("/web ") or text.startswith("웹검색:"):
            query = text.split(" ", 1)[-1]
            return {"action": "web", "query": query}
        elif text.startswith("/wiki ") or text.startswith("위키:"):
            query = text.split(" ", 1)[-1]
            return {"action": "wiki", "query": query}
        elif text.startswith("/tag "):
            query = text[5:].strip()
            return {"action": "tag", "query": query}
        elif text.startswith("/exec ") or text.startswith("실행:"):
            query = text.split(" ", 1)[-1]
            return {"action": "exec", "query": query}
        else:
            # 기본: ask (CoVe 팩트체크)
            return {"action": "ask", "query": text}


# ── 코어 라우터 ───────────────────────────────────────────────────────────

class UnifiedChannelAdapter:
    """
    WeKnora 다중 채널 어댑터 (v9.0)

    모든 채널 입력을 NormalizedMessage로 변환한 뒤
    ExecutionAgent로 전달합니다.
    """

    async def _route_to_core(
        self,
        text: str,
        channel: str,
        user_id: str,
        payload: Dict[str, Any],
        username: str = "",
        chat_id: str = "",
    ) -> str:
        """
        코어 엔진 라우팅.
        모든 채널 어댑터의 최종 수렴점.
        """
        msg = NormalizedMessage(
            text=text,
            channel=channel,
            user_id=user_id,
            username=username,
            chat_id=chat_id,
            raw=payload,
        )
        decision = msg.to_decision()

        log.info(
            f"[Adapter:{channel}] user={user_id!r} "
            f"action={decision.get('action')!r} "
            f"query={decision.get('query', '')[:40]!r}"
        )

        try:
            from modules.core_reducer import HermesCoreReducer, AgentContext, SourceChannel
            # 채널명을 SourceChannel Enum으로 매핑
            channel_map = {
                "telegram": SourceChannel.TELEGRAM,
                "slack":    SourceChannel.SLACK,
                "discord":  SourceChannel.WEBHOOK,  # discord는 webhook으로 처리
                "notion":   SourceChannel.WEBHOOK,
                "webhook":  SourceChannel.WEBHOOK,
                "cron":     SourceChannel.CRON,
            }
            source_channel = channel_map.get(channel, SourceChannel.WEBHOOK)

            ctx = AgentContext(
                user_query=decision.get("query", text),
                source_channel=source_channel,
                user_id=user_id,
                metadata={
                    "action": decision.get("action", "ask"),
                    "channel": channel,
                    **payload,
                },
            )
            reducer = HermesCoreReducer()
            return await reducer.reduce(ctx)

        except ImportError as e:
            log.warning(f"[Adapter] core_reducer 임포트 실패: {e} — 직접 응답")
            return f"[{channel}] 처리 완료: {text[:50]}"
        except Exception as e:
            log.error(f"[Adapter:{channel}] 코어 라우팅 오류: {e}", exc_info=True)
            return f"❌ 처리 중 오류가 발생했습니다: {str(e)[:100]}"


# ── 전역 어댑터 인스턴스 (싱글톤) ────────────────────────────────────────
_adapter: Optional[UnifiedChannelAdapter] = None


def get_adapter() -> UnifiedChannelAdapter:
    """전역 싱글톤 어댑터 반환."""
    global _adapter
    if _adapter is None:
        _adapter = UnifiedChannelAdapter()
    return _adapter


# ── 채널별 진입점 함수 ────────────────────────────────────────────────────

async def from_telegram(message: str, chat_id: int, username: str = "") -> str:
    """
    Telegram 봇 트리거.
    기존 handlers/ 와 병행 사용 가능 (직접 호출 방식).
    """
    return await get_adapter()._route_to_core(
        text=message,
        channel="telegram",
        user_id=str(chat_id),
        username=username,
        chat_id=str(chat_id),
        payload={"chat_id": chat_id, "username": username},
    )


async def from_discord(discord_message: str, discord_user_id: int, username: str = "") -> str:
    """
    Discord 봇 트리거.
    Discord.py 또는 nextcord 이벤트에서 호출.

    Example:
        @bot.event
        async def on_message(message):
            reply = await from_discord(message.content, message.author.id, message.author.name)
            await message.channel.send(reply)
    """
    return await get_adapter()._route_to_core(
        text=discord_message,
        channel="discord",
        user_id=str(discord_user_id),
        username=username,
        payload={"discord_user_id": discord_user_id},
    )


async def from_slack(
    text: str,
    slack_user_id: str,
    channel_id: str = "",
    payload: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Slack Events API / Webhook 트리거.

    Example (Flask):
        @app.route("/slack/events", methods=["POST"])
        async def slack_events():
            data = request.json
            reply = await from_slack(data["event"]["text"], data["event"]["user"])
            return jsonify({"text": reply})
    """
    return await get_adapter()._route_to_core(
        text=text,
        channel="slack",
        user_id=slack_user_id,
        chat_id=channel_id,
        payload=payload or {"slack_user_id": slack_user_id, "channel_id": channel_id},
    )


async def from_notion_webhook(payload: Dict[str, Any]) -> str:
    """
    Notion 데이터베이스 변경 트리거.

    Payload 형식 (Notion Webhook):
        {
            "text": "처리할 내용",
            "user_id": "notion_user_id",
            "database_id": "...",
            "page_id": "..."
        }
    """
    text    = payload.get("text", "") or payload.get("content", "")
    user_id = payload.get("user_id", "notion_system")

    return await get_adapter()._route_to_core(
        text=text,
        channel="notion",
        user_id=str(user_id),
        payload=payload,
    )


async def from_webhook(
    text: str,
    source: str = "webhook",
    user_id: str = "system",
    payload: Optional[Dict[str, Any]] = None,
) -> str:
    """
    범용 HTTP Webhook 트리거.
    어떤 외부 서비스도 이 함수로 연결 가능.

    Example (FastAPI):
        @app.post("/hermes/webhook")
        async def webhook(body: dict):
            reply = await from_webhook(body["text"], source="zapier", user_id=body.get("user_id"))
            return {"reply": reply}
    """
    return await get_adapter()._route_to_core(
        text=text,
        channel=source,
        user_id=user_id,
        payload=payload or {"source": source},
    )


async def from_cron(task: str, trigger_source: str = "launchd") -> str:
    """
    Cron / LaunchAgent 스케줄 트리거.
    자동화 작업(야간 인덱싱, 정리 등)에 사용.

    Example (launchd):
        python3 -c "
        import asyncio
        from modules.adapters import from_cron
        asyncio.run(from_cron('위키 인덱싱 갱신'))
        "
    """
    return await get_adapter()._route_to_core(
        text=task,
        channel="cron",
        user_id="system",
        payload={"trigger_source": trigger_source, "task": task},
    )
