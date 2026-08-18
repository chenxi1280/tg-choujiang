from __future__ import annotations

from typing import Any
from uuid import UUID

from aiogram import Bot
from aiogram.types import CallbackQuery, Message, User

from tg_lottery.application.admin_sessions import AdminSessionService
from tg_lottery.application.campaigns import CampaignService, DraftSource
from tg_lottery.application.queries import QueryService
from tg_lottery.application.records import utcnow
from tg_lottery.application.user_profiles import UserProfileService
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.creator_bonus_permissions import (
    verify_publication_bonus_permissions,
)
from tg_lottery.telegram.creator_group_selection import (
    configured_activity_chat_ids,
    resolve_participation_groups,
    resolve_requirements,
    selected_chat_ids,
)
from tg_lottery.telegram.creator_renderer import ButtonRows, CreatorRenderer
from tg_lottery.telegram.draft_config import campaign_input, presentation_from_data
from tg_lottery.telegram.issuer import resolve_issuer


class CreatorPublisher:
    def __init__(
        self,
        *,
        bot: Bot,
        bot_username: str,
        sessions: AdminSessionService,
        campaigns: CampaignService,
        queries: QueryService,
        profiles: UserProfileService,
        renderer: CreatorRenderer,
    ) -> None:
        self._bot = bot
        self._bot_username = bot_username
        self._sessions = sessions
        self._campaigns = campaigns
        self._queries = queries
        self._profiles = profiles
        self._renderer = renderer

    async def confirm(self, query: CallbackQuery, _: object) -> None:
        data = await self._required_draft(query.from_user.id)
        if data.get("published_campaign_id"):
            await self._finish(query, data, UUID(str(data["published_campaign_id"])))
            return
        now = utcnow()
        campaign_input(data, now=now)
        resolved = await self._resolve(data, query.from_user)
        campaign_input(resolved, now=now)
        reviewed = resolved | {"publish_reviewed_at": now.isoformat()}
        await self._sessions.set(query.from_user.id, "draft:publish_confirm", reviewed)
        text, rows = confirmation_view(reviewed)
        await self._renderer.render(
            _message(query), user_id=query.from_user.id, text=text, rows=rows
        )

    async def publish(self, query: CallbackQuery, _: object) -> None:
        data = await self._required_confirmation(query.from_user.id)
        if data.get("published_campaign_id"):
            await self._finish(query, data, UUID(str(data["published_campaign_id"])))
            return
        now = utcnow()
        draft, prizes = campaign_input(data, now=now)
        source = DraftSource(str(data["draft_id"]), int(data["draft_revision"]))
        campaign = await self._campaigns.create_draft(
            draft, prizes, query.from_user.id, source=source
        )
        progress = data | {
            "published_campaign_id": str(campaign.id),
            "publish_stage": "campaign_created",
        }
        await self._sessions.set(query.from_user.id, "draft:publishing", progress)
        await self._finish(query, progress, campaign.id)

    async def _finish(self, query: CallbackQuery, data: dict[str, Any], campaign_id: UUID) -> None:
        await self._campaigns.publish(campaign_id, query.from_user.id, self._bot_username)
        accepted = data | {"publish_stage": "publication_accepted"}
        await self._sessions.set(query.from_user.id, "draft:publishing", accepted)
        await self._profiles.save_last_draft(
            query.from_user.id, accepted | presentation_from_data(accepted), utcnow()
        )
        await self._sessions.clear(query.from_user.id)
        await _message(query).edit_text(
            "🎉 发布请求已提交。全部参与群活动卡确认送达后活动才正式开始；"
            "请在「抽奖管理」查看已确认、失败和远端未知状态。"
        )

    async def _resolve(self, data: dict[str, Any], creator: User) -> dict[str, Any]:
        participation_groups, requirements = await self._review_targets(data, creator.id)
        issuer = await self._resolve_issuer(data, creator)
        return _resolved_data(data, participation_groups, requirements) | issuer

    async def _review_targets(
        self, data: dict[str, Any], creator_id: int
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        participation_groups = []
        requirements = []
        errors = []
        try:
            participation_groups = await resolve_participation_groups(
                self._bot,
                chat_ids=selected_chat_ids(data),
                admin_user_id=creator_id,
                activity_enabled="activity" in data.get("bonus_types", []),
                activity_chat_ids=configured_activity_chat_ids(data),
            )
        except UserFacingError as exc:
            errors.append(exc.public_message)
        try:
            await verify_publication_bonus_permissions(self._bot, creator_id, data)
        except UserFacingError as exc:
            errors.append(exc.public_message)
        try:
            requirements = await resolve_requirements(self._bot, data, creator_user_id=creator_id)
        except UserFacingError as exc:
            errors.append(exc.public_message)
        if errors:
            raise UserFacingError("发布前发现以下问题：\n" + "\n".join(errors))
        return participation_groups, requirements

    async def _resolve_issuer(self, data: dict[str, Any], creator: User) -> dict[str, Any]:
        issuer_ref = str(data.get("issuer") or "")
        candidates = (
            await self._queries.user_ids_by_username(issuer_ref)
            if issuer_ref.startswith("@")
            else ()
        )
        return await resolve_issuer(self._bot, issuer_ref, creator, candidate_user_ids=candidates)

    async def _required_draft(self, user_id: int) -> dict[str, Any]:
        session = await self._sessions.get(user_id)
        if session is None or not session.state.startswith("draft:"):
            raise UserFacingError("创建草稿已过期，请发送 /new 重新创建")
        return dict(session.data)

    async def _required_confirmation(self, user_id: int) -> dict[str, Any]:
        session = await self._sessions.get(user_id)
        if session is None or session.state not in {"draft:publish_confirm", "draft:publishing"}:
            raise UserFacingError("发布确认已失效，请返回预览后重新复核")
        return dict(session.data)


def confirmation_view(data: dict[str, Any]) -> tuple[str, ButtonRows]:
    invite_rules = _enabled_rules(data, "invite")
    activity_rules = _enabled_rules(data, "activity")
    invite_unlimited = sum(rule["cap_mode"] == "unlimited" for rule in invite_rules)
    activity_unlimited = sum(rule["daily_cap_mode"] == "unlimited" for rule in activity_rules)
    text = (
        "发布前确认\n\n"
        f"参与群：{len(selected_chat_ids(data))} 个\n"
        f"邀请加成：{len(invite_rules)} 个群开启，其中 {invite_unlimited} 个不设上限\n"
        f"发言加成：{len(activity_rules)} 个群开启，其中 {activity_unlimited} 个每日不设上限\n"
        "权限异常：0 项\n\n"
        "确认后规则将冻结，并向全部参与群投递同一活动。"
    )
    rows = [
        [("查看全部规则", "draft_rules")],
        [("确认发布", "draft_publish_confirm")],
        [("返回修改", "draft_back")],
    ]
    return text, rows


def _enabled_rules(data: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [
        rule for rule in (data.get(f"group_{kind}_rules") or {}).values() if rule.get("enabled")
    ]


def _resolved_data(
    data: dict[str, Any],
    participation_groups: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
) -> dict[str, Any]:
    compatibility_anchor = participation_groups[0]
    return data | {
        "required_chat_ids": [item["id"] for item in requirements],
        "required_chats": requirements,
        "participation_group_ids": [item["id"] for item in participation_groups],
        "participation_groups": participation_groups,
        "main_chat": compatibility_anchor,
    }


def _message(query: CallbackQuery) -> Message:
    if not isinstance(query.message, Message):
        raise ValueError("callback message is no longer accessible")
    return query.message
