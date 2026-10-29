"""Public discovery and owner-bound personal progress; never registers participants."""

import html
import logging
from datetime import timedelta
from uuid import UUID, uuid4

from aiogram.types import InlineKeyboardButton
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from tg_lottery.application.lottery_queries import query_campaigns
from tg_lottery.application.records import add_outbox, utcnow
from tg_lottery.domain.enums import OutboxState
from tg_lottery.infrastructure.db import TelegramOutbox
from tg_lottery.telegram.lottery_query_views import (
    activity_url,
    discovery,
    page_buttons,
    personal_progress,
)
from tg_lottery.telegram.panel_edit import edit_panel
from tg_lottery.telegram.ui_style import row_keyboard

PROGRESS_ALIASES = {"抽奖进度", "发言进度", "门槛进度", "progress", "抽奖加成"}


def query_kind(text, username):
    raw = text.strip()
    if raw.startswith("/"):
        word, separator, target = raw[1:].partition("@")
        if separator and target.casefold() != username.casefold():
            return None
        raw = word
    if raw in {"抽奖", "lottery"}:
        return "lottery"
    return "progress" if raw in PROGRESS_ALIASES else None


class LotteryQueries:
    def __init__(self, queries, *, bot_username):
        self.queries = queries
        self.username = bot_username
        self.sessions = getattr(queries, "_session_factory", None)

    async def message(self, message, text):
        kind = query_kind(text, self.username)
        if kind is None:
            return False
        owner = message.from_user.id if message.from_user and not message.sender_chat else None
        context = {
            "kind": kind,
            "owner": owner,
            "chat_id": message.chat.id,
            "scope": None if message.chat.type == "private" else message.chat.id,
            "request_id": message.message_id,
            "thread_id": message.message_thread_id,
        }
        await self._queue(context)
        return True

    async def _queue(self, context):
        key = f"lottery_query:{self.username}:{context['chat_id']}:{context['request_id']}"
        async with self.sessions() as session, session.begin():
            await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(key))))
            if await session.scalar(
                select(TelegramOutbox.id).where(TelegramOutbox.business_key == key)
            ):
                return
            identifier = uuid4()
            if await _rate_limited(session, context):
                text, rows = self._limited_view(context), self._home_rows()
            else:
                text, rows = await self.view(context, key=identifier.hex)
            payload = context | {
                "text": text,
                "reply_markup": row_keyboard(rows).model_dump(mode="json"),
            }
            if context["kind"] == "progress":
                payload["delete_after_seconds"] = 20
            row = add_outbox(
                session,
                operation="send_lottery_query",
                aggregate_type="lottery_query",
                aggregate_id=str(identifier),
                recipient_chat_id=context["chat_id"],
                business_key=key,
                payload=payload,
            )
            row.id = identifier

    async def view(self, context, *, key, page=0, section=0):
        if context["kind"] == "progress" and context["owner"] is None:
            return (
                "📊 抽奖进度\n请切换个人身份或私聊机器人查询。\n🕒 本回复20秒后删除。",
                self._home_rows(),
            )
        try:
            return await self._view(context, key=key, page=page, section=section)
        except (ValueError, LookupError, SQLAlchemyError):
            logging.getLogger(__name__).exception("lottery query projection failed")
            suffix = "\n🕒 本回复20秒后删除。" if context["kind"] == "progress" else ""
            return "⚠️ 暂时无法获取抽奖进度或活动，请稍后重试。" + suffix, self._home_rows()

    async def _view(self, context, *, key, page, section):
        personal = context["kind"] == "progress"
        if not personal and context["scope"] is None:
            return (
                "🎁 请在群内发送 /lottery 查看本群抽奖；本人进度可从下方查询。",
                self._home_rows(),
            )
        campaigns = await query_campaigns(
            self.sessions, chat_id=context["scope"], user_id=context["owner"] if personal else None
        )
        if not personal:
            text, page, pages = discovery(campaigns, username=self.username, page=page)
            return text, page_buttons(key, page=page, pages=pages) + self._home_rows()
        if not campaigns:
            scope = "本群关联的" if context["scope"] else "进行中的"
            return (
                f"📊 抽奖进度\n📭 暂无{scope}本人抽奖进度。\n🕒 本回复20秒后删除。",
                self._home_rows(),
            )
        page = min(max(page, 0), len(campaigns) - 1)
        campaign = campaigns[page]
        parts = await personal_progress(self.queries, campaign, user_id=context["owner"])
        section = min(max(section, 0), len(parts) - 1)
        text = (
            f"📊 抽奖进度 · 第{page + 1}/{len(campaigns)}场 · 第{section + 1}/{len(parts)}段\n"
            + html.escape(parts[section])
            + "\n🕒 本回复20秒后删除，完整进度可在机器人查看。"
        )
        rows = page_buttons(
            key, page=page, pages=len(campaigns), section=section, sections=len(parts)
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="📊 完整进度", url=activity_url(self.username, campaign.public_ref)
                )
            ]
        )
        return text, rows + self._home_rows()

    @staticmethod
    def _limited_view(context):
        suffix = "\n🕒 本回复20秒后删除。" if context["kind"] == "progress" else ""
        return "📊 抽奖进度\n⏳ 查询较频繁，请稍候几秒再试。" + suffix

    def _home_rows(self):
        return [
            [
                InlineKeyboardButton(
                    text="🤖 查看我全部活动进度",
                    url=f"https://t.me/{self.username}?start=myprogress",
                ),
                InlineKeyboardButton(
                    text="📋 我的参与记录", url=f"https://t.me/{self.username}?start=myrecords"
                ),
            ]
        ]

    async def callback(self, query):
        if not (query.data or "").startswith("lq:"):
            return False
        try:
            _, key, page, section = query.data.split(":")
            identifier, page, section = UUID(hex=key), int(page), int(section)
        except (ValueError, TypeError):
            await query.answer("查询入口无效，请重新查询。", show_alert=True)
            return True
        async with self.sessions() as session, session.begin():
            await session.execute(select(func.pg_advisory_xact_lock(func.hashtext("lq:" + key))))
            source = await session.get(TelegramOutbox, identifier)
            if not self._valid_source(source, query):
                await query.answer("请发送抽奖进度查看自己的进度。", show_alert=True)
                return True
            if await _expired(session, source):
                await query.answer("本次查询已过期，请重新发送抽奖进度。", show_alert=True)
                return True
            text, rows = await self.view(source.payload, key=key, page=page, section=section)
            if await _expired(session, source):
                await query.answer("本次查询已过期，请重新查询。", show_alert=True)
                return True
            await edit_panel(
                query.message, text, parse_mode="HTML", reply_markup=row_keyboard(rows)
            )
        return True

    @staticmethod
    def _valid_source(source, query):
        return (
            source is not None
            and source.operation == "send_lottery_query"
            and source.state == OutboxState.CONFIRMED
            and source.recipient_chat_id == query.message.chat.id
            and source.remote_message_id == query.message.message_id
            and (
                source.payload["kind"] == "lottery" or source.payload["owner"] == query.from_user.id
            )
        )


async def _rate_limited(session, context):
    key = f"lottery_rate:{context['chat_id']}:{context['owner']}"
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(key))))
    return (
        await session.scalar(
            select(TelegramOutbox.id)
            .where(
                TelegramOutbox.operation == "send_lottery_query",
                TelegramOutbox.recipient_chat_id == context["chat_id"],
                TelegramOutbox.payload["owner"].as_string() == str(context["owner"]),
                TelegramOutbox.created_at >= utcnow() - timedelta(seconds=5),
            )
            .limit(1)
        )
        is not None
    )


async def _expired(session, source):
    from tg_lottery.domain.enums import DeliveryCertainty
    from tg_lottery.infrastructure.db import DeliveryAttempt

    if source.payload["kind"] != "progress":
        return False
    confirmed = await session.scalar(
        select(DeliveryAttempt.finished_at)
        .where(
            DeliveryAttempt.outbox_id == source.id,
            DeliveryAttempt.certainty == DeliveryCertainty.CONFIRMED,
        )
        .order_by(DeliveryAttempt.attempt_number.desc())
        .limit(1)
    )
    return confirmed is None or utcnow() >= confirmed + timedelta(seconds=20)
