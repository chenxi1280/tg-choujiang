from __future__ import annotations

from typing import Any
from uuid import UUID

from aiogram import Bot
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, User

from tg_lottery.application.admin_sessions import AdminSessionService
from tg_lottery.application.campaigns import CampaignService, DraftSource
from tg_lottery.application.queries import QueryService
from tg_lottery.application.records import utcnow
from tg_lottery.application.user_profiles import UserProfileService
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.platform_policy import PlatformPolicy
from tg_lottery.domain.rules import canonical_hash
from tg_lottery.telegram.campaign_authorization import verify_campaign_admin
from tg_lottery.telegram.creator_bonus_permissions import (
    verify_publication_bonus_permissions,
)
from tg_lottery.telegram.creator_bonus_policy import current_bonus_policy
from tg_lottery.telegram.creator_group_identity import normalize_draft_groups
from tg_lottery.telegram.creator_group_selection import (
    configured_activity_chat_ids,
    resolve_participation_groups,
    resolve_requirements,
    selected_chat_ids,
)
from tg_lottery.telegram.creator_invite_retention import summary as retention_summary
from tg_lottery.telegram.creator_public_preview import send_public_preview
from tg_lottery.telegram.creator_renderer import ButtonRows, CreatorRenderer
from tg_lottery.telegram.creator_rule_review import review_navigation, rule_review_pages
from tg_lottery.telegram.creator_share import publication_receipt
from tg_lottery.telegram.draft_config import campaign_input, presentation_from_data
from tg_lottery.telegram.issuer import resolve_issuer
from tg_lottery.telegram.panel_edit import edit_panel
from tg_lottery.telegram.platform_requirements import (
    attach_platform_policy,
    editable_requirement_data,
    frozen_requirement_data,
    inspect_official_channels,
    platform_summary,
)
from tg_lottery.telegram.public_projection import campaign_state_text
from tg_lottery.telegram.ui_style import style_keyboard


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
        if await self._recover_existing(query, data):
            return
        data = await normalize_draft_groups(self._queries, data)
        _require_fixed_draw(data)
        now = utcnow()
        campaign_input(data, now=now)
        resolved = await self._resolve(data, query.from_user)
        campaign_input(resolved, now=now)
        reviewed = resolved | {"publish_reviewed_at": now.isoformat()}
        await send_public_preview(
            self._bot, _message(query), data=reviewed, bot_username=self._bot_username
        )
        text, rows = confirmation_view(reviewed)
        panel = await _message(query).answer(text)
        await self._sessions.set(
            query.from_user.id,
            "draft:publish_confirm",
            reviewed | {"panel_message_id": panel.message_id},
        )
        await self._renderer.render(panel, user_id=query.from_user.id, text=text, rows=rows)

    async def publish(self, query: CallbackQuery, _: object) -> None:
        data = await self._required_confirmation(query.from_user.id)
        if await self._recover_existing(query, data):
            return
        _require_fixed_draw(data)
        policy = getattr(self._campaigns, "platform_policy", None)
        if data.get("platform_policy") != (policy.model_dump(mode="json") if policy else None):
            await self.confirm(query, None)
            return
        resolved = await self._resolve(data, query.from_user)
        if canonical_hash(presentation_from_data(resolved)) != canonical_hash(
            presentation_from_data(data)
        ):
            await _message(query).answer("参与条件或身份信息已变化，请核对更新后的预览。")
            await self.confirm(query, None)
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

    async def _recover_existing(self, query: CallbackQuery, data: dict) -> bool:
        campaign = await self._queries.campaign_for_draft(query.from_user.id, str(data["draft_id"]))
        if campaign is None:
            if data.get("published_campaign_id"):
                raise UserFacingError("原发布活动的身份暂未核实，请从我发起的抽奖核查")
            return False
        await self.resume_message(
            _message(query), user_id=query.from_user.id, data=data, campaign=campaign
        )
        return True

    async def show_rules(self, query: CallbackQuery, grant: object) -> None:
        data = await self._required_confirmation(query.from_user.id)
        pages = rule_review_pages(data)
        shifts = {"draft_publish_rules_prev": -1, "draft_publish_rules_next": 1}
        page = int(data.get("publish_rules_page", 0)) + shifts.get(grant.action, 0)
        page = 0 if grant.action == "draft_publish_rules" else page
        page = min(max(0, page), len(pages) - 1)
        await self._sessions.set(
            query.from_user.id, "draft:publish_confirm", data | {"publish_rules_page": page}
        )
        await self._renderer.render(
            _message(query),
            user_id=query.from_user.id,
            text=f"全部规则 · 第 {page + 1}/{len(pages)} 页\n\n{pages[page]}",
            rows=review_navigation(page, len(pages)),
        )

    async def resume_message(self, message: Message, *, user_id: int, data: dict, campaign) -> None:
        await verify_campaign_admin(
            self._bot, self._queries, campaign_id=campaign.id, user_id=user_id
        )
        restored = data | {"published_campaign_id": str(campaign.id)}
        await self._sessions.set(user_id, "draft:publishing", restored)
        if campaign.state in {CampaignState.DRAFT, CampaignState.PUBLISHING}:
            await self._verify_frozen_platform(campaign)
            await self._campaigns.publish(campaign.id, user_id, self._bot_username)
        campaign = await self._queries.campaign(campaign.id)
        if campaign is None:
            raise UserFacingError("原活动状态暂未核实，请从我发起的抽奖核查")
        text = (
            f"已恢复同一活动：{campaign.name}\n状态：{campaign_state_text(campaign.state)}\n"
            "不会创建第二场。发布请求与各目标实际送达分开核对，请查看当前状态。"
        )
        keyboard = style_keyboard(
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="查看当前活动状态",
                            callback_data=f"nav:campaign:{campaign.public_ref}",
                        )
                    ],
                    [InlineKeyboardButton(text="我发起的抽奖", callback_data="nav:created")],
                    [InlineKeyboardButton(text="返回首页", callback_data="nav:index")],
                ]
            )
        )
        await edit_panel(message, text, reply_markup=keyboard)

    async def _finish(self, query: CallbackQuery, data: dict[str, Any], campaign_id: UUID) -> None:
        current = await self._queries.campaign(campaign_id)
        if current and current.state in {CampaignState.DRAFT, CampaignState.PUBLISHING}:
            await self._verify_frozen_platform(current)
        await self._campaigns.publish(campaign_id, query.from_user.id, self._bot_username)
        accepted = data | {"publish_stage": "publication_accepted"}
        await self._sessions.set(query.from_user.id, "draft:publishing", accepted)
        await self._profiles.save_last_draft(
            query.from_user.id, accepted | presentation_from_data(accepted), utcnow()
        )
        campaign = await self._queries.campaign(campaign_id)
        if campaign is None:
            raise UserFacingError("找不到已创建的活动，请在抽奖管理中核对")
        text, keyboard = publication_receipt(campaign, self._bot_username)
        await edit_panel(_message(query), text, reply_markup=keyboard)
        await self._sessions.clear(query.from_user.id)

    async def _verify_frozen_platform(self, campaign):
        frozen = campaign.rules.get("platform_policy")
        await inspect_official_channels(
            self._bot, PlatformPolicy.model_validate(frozen) if frozen else None
        )

    async def _resolve(self, data: dict[str, Any], creator: User) -> dict[str, Any]:
        policy = getattr(self._campaigns, "platform_policy", None)
        await inspect_official_channels(self._bot, policy)
        data = editable_requirement_data(data, policy)
        participation_groups, requirements = await self._review_targets(data, creator.id)
        issuer = await self._resolve_issuer(data, creator)
        resolved = _resolved_data(data, participation_groups, requirements)
        return frozen_requirement_data(resolved, requirements, policy) | issuer

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
        return attach_platform_policy(
            current_bonus_policy(dict(session.data)),
            getattr(self._campaigns, "platform_policy", None),
        )

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
    cover = "已设置（将随公告发送）" if data.get("cover_file_id") else "未设置（公告无照片）"
    text = (
        "发布前确认\n\n"
        f"{platform_summary(data)}"
        f"参与群：{len(selected_chat_ids(data))} 个\n"
        f"封面：{cover}\n"
        f"邀请加成：{len(invite_rules)} 个群开启，其中 {invite_unlimited} 个不设上限\n"
        f"{retention_summary(data)}"
        f"活跃加成（人品）：{len(activity_rules)} 个群开启，"
        f"其中 {activity_unlimited} 个每日不设上限\n"
        "权限异常：0 项\n\n"
        "确认后规则将冻结，并向全部参与群投递同一活动。"
    )
    rows = [
        [("查看全部规则", "draft_publish_rules")],
        [("确认发布", "draft_publish_confirm")],
        [("返回修改", "draft_back")],
    ]
    if data.get("cover_file_id"):
        rows.insert(0, [("预览封面", "draft_cover_preview")])
    return text, rows


def _enabled_rules(data: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    if data.get("participation_rules_version") == "v3.0" and kind not in data.get(
        "bonus_types", []
    ):
        return []
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


def _require_fixed_draw(data: dict[str, Any]) -> None:
    if data.get("draw_mode") == "full":
        raise UserFacingError("满人开奖已取消，请返回开奖方式设置定时开奖")
