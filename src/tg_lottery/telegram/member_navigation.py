from dataclasses import dataclass, replace

from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, User

from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.presentation.participation_rules import complete_rule_pages
from tg_lottery.presentation.target_identity import (
    distinct_group_names,
)
from tg_lottery.telegram.member_activity_landing import _rules_payload
from tg_lottery.telegram.member_bonus_context import actual_bonus_context, bonus_eligibility_payload
from tg_lottery.telegram.member_bonus_permissions import inspect_bonus_permissions
from tg_lottery.telegram.member_bonus_read import bonus_read_payload, uses_legacy_bonus
from tg_lottery.telegram.member_bonus_views import (
    build_bonus_category,
    build_bonus_group_detail,
    build_bonus_overview,
)
from tg_lottery.telegram.member_callbacks import safe_answer
from tg_lottery.telegram.member_condition_check import condition_check_payload
from tg_lottery.telegram.member_gate_views import gate_payload
from tg_lottery.telegram.member_navigation_context import (
    READ_PREFIXES,
    MemberPageContext,
    scoped_keyboard,
)
from tg_lottery.telegram.member_navigation_views import participant_detail_view
from tg_lottery.telegram.member_record_delivery import private_record_message
from tg_lottery.telegram.member_records import _invitation_keyboard, _invite_groups, _record_line
from tg_lottery.telegram.member_region import region_view, set_region
from tg_lottery.telegram.member_window_guidance import window_guidance
from tg_lottery.telegram.panel_pagination import PAGE_CONTENT_UNITS, text_pages

RECORD_GROUP_PAGE_SIZE = 6


@dataclass(frozen=True)
class MemberMessageTarget:
    from_user: User
    message: Message
    edit: bool = False


class MemberNavigation:
    def __init__(
        self, *, bot, queries, cipher, tokens, profiles, bot_username: str, relationships=None
    ):
        self._bot, self._queries, self._cipher = bot, queries, cipher
        self._tokens, self._profiles, self._bot_username = tokens, profiles, bot_username
        self._relationships = relationships

    async def open_message(self, message: Message, reference: str, *, rules_only: bool = False):
        if message.from_user is None:
            raise UserFacingError("无法确认本人身份，请重新打开机器人")
        target = MemberMessageTarget(message.from_user, message)
        context = MemberPageContext(
            f"m:rules:{reference}:0" if rules_only else f"m:detail:{reference}"
        )
        if rules_only:
            await self._rules(target, context)
        else:
            await self._detail(target, context)

    async def handle_route(self, query: CallbackQuery, *, data: str, personal) -> bool:
        if not data.startswith(READ_PREFIXES) or data.startswith("nav:"):
            if not data.startswith("nav:joined:"):
                return False
            data = "m:detail:" + data.removeprefix("nav:joined:")
        context = MemberPageContext(data)
        await self._dispatch(query, context, personal=personal)
        if (query.data or "").startswith(("m:", "member:", "nav:joined:")):
            await safe_answer(
                query,
                "已更新本人页面" if query.message.chat.type == "private" else "已在本人私聊显示",
                alert=False,
            )
        return True

    async def recover(self, query, payload, *, personal):
        route = str(payload["route"])
        if not route.startswith(READ_PREFIXES):
            raise UserFacingError("旧入口无法恢复，请重新打开本人活动列表")
        context = MemberPageContext(
            route, tuple(payload["trail"]), int(payload.get("content_page", 0))
        )
        await self._dispatch(query, context, personal=personal)

    async def handle_grant(self, query, grant, *, personal):
        if grant.action == "member:region_set":
            await set_region(self._profiles, query, grant)
        route = str(grant.payload["route"])
        if not route.startswith(READ_PREFIXES):
            raise UserFacingError("这个按钮不是只读的本人导航入口")
        context = MemberPageContext(
            route, tuple(grant.payload["trail"]), int(grant.payload.get("content_page", 0))
        )
        await self._dispatch(query, context, personal=personal)

    async def _dispatch(self, query, context: MemberPageContext, *, personal):
        route = context.route
        if route.startswith("nav:"):
            await personal.handle_callback(query.model_copy(update={"data": route}), route)
            return
        if route.startswith(("m:detail:", "member:tickets:")):
            await self._detail(query, context)
            return
        if route.startswith(("m:rules:", "member:rules:")):
            await self._rules(query, context)
            return
        if route.startswith("m:region:"):
            text, keyboard = await region_view(
                self._profiles, self._tokens, user_id=query.from_user.id, context=context
            )
            await self._render(query, context, pages=(text,), keyboard=keyboard)
            return
        if route.startswith("m:conditions:"):
            await self._conditions(query, context)
            return
        if route.startswith(("m:gate:", "m:gg:")):
            await self._gate(query, context)
            return
        if route.startswith(("m:bo:", "m:id:", "m:ad:", "member:bonus_")):
            await self._bonus(query, context)
            return
        await self._records(query, context)

    async def _detail(self, query, context):
        parts = context.route.split(":")
        reference = parts[2]
        if len(parts) > 3:
            context = replace(
                context,
                route=f"m:detail:{reference}",
                trail=(f"nav:join_at:{reference}:{parts[3]}:{parts[4]}",),
            )
        detail = await self._queries.participant_campaign_detail(query.from_user.id, reference)
        if detail is None:
            await self._rules(query, replace(context, route=f"m:rules:{reference}:0"))
            return
        pages, keyboard = participant_detail_view(
            detail, user_id=query.from_user.id, bot_username=self._bot_username
        )
        await self._render(query, context, pages=pages, keyboard=keyboard)

    async def _rules(self, query, context):
        parts = context.route.split(":")
        campaign = await self._campaign(parts[2])
        prizes = await self._queries.prizes(campaign.id)
        pages = complete_rule_pages(_rules_payload(self, campaign, prizes))
        number = int(parts[3]) if len(parts) > 3 else 0
        selected = min(max(0, number), len(pages) - 1)
        detail = await self._queries.participant_campaign_detail(
            query.from_user.id, campaign.public_ref
        )
        issues = await self._queries.operational_issues(campaign.id)
        rows = self._rule_rows(campaign, detail, blocked=bool(issues))
        navigation = _page_buttons("m:rules:" + campaign.public_ref, selected, len(pages))
        if navigation:
            rows.append(navigation)
        rows.append([_button("返回活动详情", f"m:detail:{campaign.public_ref}")])
        text = (
            f"规则版本：{campaign.config_revision} · 第{selected + 1}/{len(pages)}页\n"
            + pages[selected]
            + "\n"
            + window_guidance(campaign, CampaignRules.model_validate(campaign.rules))
        )
        await self._render(
            query, context, pages=(text,), keyboard=_keyboard(rows), parse_mode="HTML"
        )

    def _rule_rows(self, campaign, detail, *, blocked: bool):
        ref = campaign.public_ref
        rows = [[_button("查看加入与资料要求", f"m:conditions:{ref}:0")]]
        if (
            campaign.state in {CampaignState.RUNNING, CampaignState.SCHEDULED}
            and utcnow() <= campaign.cutoff_at
            and (detail is not None or not blocked)
        ):
            action = (
                _button("登记参与", f"member:join:{ref}")
                if detail is None
                else _button("查看本人进度", f"m:detail:{ref}")
            )
            rows.insert(0, [action])
            rows.append(
                [
                    InlineKeyboardButton(
                        text="分享活动卡片",
                        url=f"https://t.me/{self._bot_username}?start=share_{ref}",
                    )
                ]
            )
        rows.append([_button("返回首页", "nav:index")])
        return rows

    async def _bonus(self, query, context):
        parts = context.route.split(":")
        reference = parts[2]
        campaign = await self._campaign(reference)
        detail = await self._queries.participant_campaign_detail(query.from_user.id, reference)
        if detail is None:
            raise UserFacingError("请先登记本场活动；已有本人记录始终可读，不要求重新报名")
        rules = CampaignRules.model_validate(campaign.rules)
        if parts[1] == "bo" and uses_legacy_bonus(rules):
            text, keyboard = await bonus_read_payload(
                self, campaign, rules, user_id=query.from_user.id
            )
            await self._render(query, context, pages=(text,), keyboard=keyboard)
            return
        targets = await self._queries.qualification_targets(campaign.id)
        unavailable = await inspect_bonus_permissions(self._bot, rules, targets=targets)
        options = dict(
            user_id=query.from_user.id,
            queries=self._queries,
            cipher=self._cipher,
            bot_username=self._bot_username,
            unavailable=unavailable,
        )
        if parts[1] == "bo":
            text, keyboard = await build_bonus_overview(campaign, **options)
        elif parts[1] in {"id", "ad"}:
            text, keyboard = await build_bonus_group_detail(
                campaign,
                category="invites" if parts[1] == "id" else "activity",
                chat_id=int(parts[3]),
                return_page=int(parts[4]),
                **options,
            )
        else:
            text, keyboard = await build_bonus_category(
                campaign,
                category="invites" if parts[1] == "bonus_invites" else "activity",
                page=int(parts[3]),
                **options,
            )
        text += "\n\n" + window_guidance(campaign, rules)
        text, keyboard = bonus_eligibility_payload(detail, text=text, keyboard=keyboard)
        context = actual_bonus_context(context, campaign=campaign, rules=rules)
        await self._render(query, context, pages=(text,), keyboard=keyboard)

    async def _conditions(self, query, context):
        campaign = await self._campaign(context.route.split(":")[2])
        text, keyboard = await condition_check_payload(
            query,
            relationships=self._relationships,
            bot_username=self._bot_username,
            campaign=campaign,
            queries=self._queries,
            bot=self._bot,
            profiles=self._profiles,
            cipher=self._cipher,
        )
        await self._render(query, context, pages=(text,), keyboard=keyboard)

    async def _gate(self, query, context):
        reference = context.route.split(":")[2]
        campaign = await self._campaign(reference)
        detail = await self._queries.participant_campaign_detail(query.from_user.id, reference)
        if detail is None:
            raise UserFacingError("请先明确登记本场活动，查看规则不会开始统计")
        rules = CampaignRules.model_validate(campaign.rules)
        if rules.admission is not None:
            text, keyboard = await gate_payload(
                self._queries,
                campaign=campaign,
                detail=(query.from_user.id, detail),
                rules=rules,
                targets=await self._queries.qualification_targets(campaign.id),
                route=context.route,
                cipher=self._cipher,
            )
            await self._render(query, context, pages=(text,), keyboard=keyboard)
            return
        text = (
            "本场门槛 · 逐群进度\n\n"
            + detail.base_progress
            + "\n"
            + window_guidance(campaign, rules)
        )
        rows = []
        targets = await self._queries.qualification_targets(campaign.id)
        active = _accepts_registration(campaign)
        if not active and campaign.state is not CampaignState.PAUSED:
            text += "\n本场已停止新增统计，以下为本人已有记录。"
        rows = self._gate_actions(campaign, rules=rules, targets=targets, reference=reference)
        rows.append([_button("返回活动详情", f"m:detail:{reference}")])
        await self._render(query, context, pages=(text,), keyboard=_keyboard(rows))

    def _gate_actions(self, campaign, *, rules, targets, reference):
        rows = []
        if _can_speak(campaign, rules):
            rows.extend(self._speech_rows(targets))
        elif (
            campaign.state in {CampaignState.RUNNING, CampaignState.SCHEDULED, CampaignState.PAUSED}
            and utcnow() <= campaign.cutoff_at
            and rules.admission
            and rules.admission.type == "invite"
        ):
            rows.append([_button("我的邀请链接", f"member:bonus_invites:{reference}:0")])
        return rows

    def _speech_rows(self, targets):
        rows = []
        for target in targets:
            url = _target_url(target, self._cipher)
            if target.kind == "participation_group" and url:
                rows.append([InlineKeyboardButton(text="去「" + target.title + "」发言", url=url)])
        return rows

    async def _records(self, query, context):
        parts = context.route.split(":")
        reference = parts[2]
        if parts[1] != "ir":
            await self._record_groups(query, context)
            return
        if parts[1] == "ir":
            options = dict(
                chat_id=int(parts[3]),
                group_page=int(parts[4]),
                status_filter=parts[5],
                page=int(parts[6]),
            )
        else:
            options = dict(page=int(parts[3]), chat_id=None, group_page=0, status_filter="all")
        records = await self._queries.campaign_invitation_records(
            query.from_user.id, reference, **options
        )
        if records is None:
            raise UserFacingError("没有本人的邀请记录，请先核对已登记活动")
        lines = [_record_line(row, records.timezone) for row in records.rows]
        text = (
            f"邀请记录 · {records.campaign_name}\n{records.group_title}\n"
            f"已确认{records.valid} · 待核验{records.pending} · 未计入{records.invalid}\n"
            f"第{records.page + 1}/{records.total_pages}页\n\n" + "\n\n".join(lines)
        )
        await self._render(query, context, pages=(text,), keyboard=_invitation_keyboard(records))

    async def _record_groups(self, query, context):
        parts = context.route.split(":")
        if len(parts) > 4:
            context = replace(
                context,
                route=":".join(parts[:4]),
                trail=(f"nav:invites_at:{parts[2]}:" + ":".join(parts[4:]),),
            )
        campaign = await self._campaign(parts[2])
        rules = CampaignRules.model_validate(campaign.rules)
        groups = distinct_group_names(_invite_groups(campaign, rules))
        progress = await self._queries.group_bonus_progress(campaign.id, query.from_user.id)
        if progress is None:
            raise UserFacingError("没有本人的参与记录，不能读取邀请明细")
        pages = max(1, (len(groups) + RECORD_GROUP_PAGE_SIZE - 1) // RECORD_GROUP_PAGE_SIZE)
        selected = min(max(0, int(parts[3])), pages - 1)
        visible = groups[
            selected * RECORD_GROUP_PAGE_SIZE : (selected + 1) * RECORD_GROUP_PAGE_SIZE
        ]
        counts = {item.chat_id: item for item in progress.invitations}
        lines = [f"{campaign.name} · 邀请记录群列表", f"第{selected + 1}/{pages}页"]
        rows = []
        for group in visible:
            item = counts.get(int(group["id"]))
            count = f"已确认{item.valid_invites}人" if item is not None else "进度待核查"
            lines.append(f"{group['title']}：{count}")
            rows.append(
                [
                    _button(
                        str(group["title"]),
                        f"m:ir:{campaign.public_ref}:{group['id']}:{selected}:all:0",
                    )
                ]
            )
        navigation = _page_buttons("member:invites:" + campaign.public_ref, selected, pages)
        if navigation:
            rows.append(navigation)
        rows.append([_button("返回活动详情", f"m:detail:{campaign.public_ref}")])
        await self._render(query, context, pages=("\n".join(lines),), keyboard=_keyboard(rows))

    async def _campaign(self, reference):
        campaign = await self._queries.campaign_by_reference(reference)
        if campaign is None:
            raise UserFacingError("活动不存在或已失效")
        return campaign

    async def _render(self, query, context, *, pages, keyboard, parse_mode=None):
        private = (
            query.message.chat.type == "private" and query.message.chat.id == query.from_user.id
        )
        message = private_record_message(
            query.message, self._bot, user_id=query.from_user.id, user=query.from_user
        )
        if parse_mode is None:
            pages = tuple(
                part for page in pages for part in text_pages(page, limit=PAGE_CONTENT_UNITS)
            )
        selected = min(max(0, context.content_page), len(pages) - 1)
        shown = pages[selected]
        if len(pages) > 1:
            shown = (
                f"{pages[0].split(chr(10), 1)[0][:100]} · 内容第{selected + 1}/{len(pages)}段\n\n"
                + shown
            )
        rows = list(keyboard.inline_keyboard)
        navigation = _page_buttons("m:page", selected, len(pages))
        if navigation:
            rows.append(navigation)
        scoped = await scoped_keyboard(
            self._tokens, _keyboard(rows), context=context, user_id=query.from_user.id
        )
        if private and not isinstance(query, MemberMessageTarget):
            await message.edit_text(shown, reply_markup=scoped, parse_mode=parse_mode)
        else:
            try:
                await message.answer(shown, reply_markup=scoped, parse_mode=parse_mode)
            except TelegramForbiddenError as exc:
                raise UserFacingError(
                    "私聊暂不可达，请先打开机器人并点击开始，再查看本人记录"
                ) from exc


def _button(text: str, route: str):
    return InlineKeyboardButton(text=text, callback_data=route)


def _keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _page_buttons(prefix: str, page: int, total: int):
    rows = []
    if page:
        rows.append(_button("上一页", f"{prefix}:{page - 1}"))
    if page + 1 < total:
        rows.append(_button("下一页", f"{prefix}:{page + 1}"))
    return rows


def _target_url(target, cipher) -> str | None:
    if target.public_join_url:
        return target.public_join_url
    if target.revoked_at is not None or target.encrypted_join_url is None:
        return None
    return cipher.decrypt(target.encrypted_join_url, context=target.secret_context)


def _accepts_registration(campaign) -> bool:
    return (
        campaign.state in {CampaignState.RUNNING, CampaignState.SCHEDULED}
        and utcnow() <= campaign.cutoff_at
    )


def _can_speak(campaign, rules):
    return (
        campaign.state is CampaignState.RUNNING
        and utcnow() <= campaign.cutoff_at
        and utcnow() >= campaign.preheat_at
        and rules.admission
        and rules.admission.type == "message"
    )
