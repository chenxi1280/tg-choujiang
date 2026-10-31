"""Group shortcuts query activity rewards without registration or ledger writes."""

import html
import time
from collections import OrderedDict

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.presentation.campaign_card import group_gate_lines
from tg_lottery.presentation.daily_bonus import daily_bonus_progress_text, daily_bonus_rule_text
from tg_lottery.presentation.mentions import html_utf16_length
from tg_lottery.telegram.bonus_rule_text import global_bonus_lines
from tg_lottery.telegram.member_bonus_text import activity_cap, campaign_bonus_completed
from tg_lottery.telegram.member_record_text import current_effective_count
from tg_lottery.telegram.panel_edit import edit_panel
from tg_lottery.telegram.public_projection import qualification_state_text
from tg_lottery.telegram.ui_style import style_keyboard

COMMANDS = {"人品": "self", "人品规则": "rules", "人品排行": "rank"}
SELECTION_SIZE = 8


class GroupReputation:
    def __init__(self, *, queries, bot_username):
        self._queries = queries
        self._bot_username = bot_username
        self._recent = OrderedDict()

    async def message(self, message, text):
        action = COMMANDS.get(text)
        if action is None:
            return False
        user = message.from_user
        if user is None or user.is_bot:
            return True
        if self._duplicate((message.chat.id, user.id, action)):
            await message.answer("刚刚已查询，请等10秒后刷新；查询不会增加抽奖次数。")
            return True
        await self._select(message, user.id, action=action)
        return True

    async def callback(self, query):
        if not (query.data or "").startswith("rep:"):
            return False
        action, reference, owner, raw_page = _parse_button(query.data)
        message = query.message
        if message.chat.type not in {"group", "supergroup"} or str(query.from_user.id) != owner:
            await message.answer("请在原群发送人品、人品排行或人品规则，查看自己的查询。")
            return True
        if self._duplicate((message.chat.id, owner, query.data)):
            return True
        if reference == "list":
            await self._select(message, int(owner), action=action, page=int(raw_page), edit=True)
            return True
        campaigns = await self._campaigns(message.chat.id)
        campaign = next((item for item in campaigns if item.public_ref == reference), None)
        if campaign is None:
            await edit_panel(message, "原活动已停止统计，请从本人私聊记录查看历史。")
            return True
        await self._show(
            message, campaign, int(owner), action=action, page=int(raw_page), edit=True
        )
        return True

    def _duplicate(self, key):
        now = time.monotonic()
        previous = self._recent.pop(key, None)
        self._recent[key] = now
        while len(self._recent) > 2048:
            self._recent.popitem(last=False)
        return previous is not None and now - previous < 10

    async def _select(self, message, user_id, *, action, page=0, edit=False):
        campaigns = await self._campaigns(message.chat.id)
        if not campaigns:
            await message.answer("本群当前没有可查询的活动；历史加成可从本人私聊记录查看。")
            return
        if len(campaigns) == 1:
            await self._show(message, campaigns[0], user_id, action=action, edit=edit)
            return
        pages = (len(campaigns) + SELECTION_SIZE - 1) // SELECTION_SIZE
        page = min(max(0, page), pages - 1)
        rows = [
            [_button(item.name[:48], action, item.public_ref, user_id=user_id)]
            for item in campaigns[page * SELECTION_SIZE : (page + 1) * SELECTION_SIZE]
        ]
        rows.extend(_pages(action, "list", user_id=user_id, page=page, pages=pages))
        await _reply(
            message,
            f"本群有多场活动，请选择要查询的活动（{page + 1}/{pages}）。",
            rows=rows,
            edit=edit,
        )

    async def _campaigns(self, chat_id):
        values = await self._queries.active_campaigns_by_chat(chat_id)
        return tuple(
            item
            for item in values
            if item.state
            not in {
                CampaignState.DRAFT,
                CampaignState.PUBLISHING,
                CampaignState.CANCELLED,
                CampaignState.DRAWN,
                CampaignState.ARCHIVED,
            }
        )

    async def _show(self, message, campaign, user_id, *, action, page=0, edit=False):
        rules = CampaignRules.model_validate(campaign.rules)
        if action == "rank":
            text, rows = await self._ranking(campaign, user_id, page=page)
        elif action == "rules":
            text = _rules(campaign, rules, message.chat.id)
            rows = []
        else:
            text = await self._personal(campaign, rules, user_id=user_id, chat_id=message.chat.id)
            rows = []
        rows.append(
            [
                InlineKeyboardButton(
                    text="查看本场详情/本人进度",
                    url=f"https://t.me/{self._bot_username}?start=join_{campaign.public_ref}",
                )
            ]
        )
        if html_utf16_length(text) > 3500:
            raise ValueError("群内查询显示内容超出容量")
        await _reply(message, text, rows=rows, edit=edit)

    async def _personal(self, campaign, rules, *, user_id, chat_id):
        title = html.escape(campaign.name)
        detail = await self._queries.participant_campaign_detail(user_id, campaign.public_ref)
        if detail is None:
            return (
                f"{title} · 我的抽奖加成票(发言)\n尚未登记，本次查询不会自动报名。"
                "请进入本场详情登记。"
            )
        rule = rules.activity_rule_for(chat_id)
        if rule is None:
            if rules.activity_bonus_enabled and not rules.group_activity_rules:
                return await self._legacy(
                    campaign, rules, user_id=user_id, chat_id=chat_id, detail=detail
                )
            return (
                f"{title} · 我的抽奖加成票(发言)\n本场本群未开启抽奖加成票(发言)。\n"
                + _personal_totals(detail)
            )
        progress = await self._queries.group_bonus_progress(campaign.id, user_id)
        if rules.daily_activity_bonus:
            names = {
                str(g["id"]): g["title"]
                for g in campaign.presentation.get("participation_groups", [])
            }
            text = daily_bonus_progress_text(progress.daily_bonus, names=names)
            return f"{title}\n{html.escape(text)}\n{_personal_totals(detail)}"
        item = next((row for row in progress.activities if row.chat_id == chat_id), None)
        if item is None:
            raise ValueError("本人抽奖加成票(发言)进度缺少冻结目标")
        needed = rule.messages_per_cycle - item.valid_messages % rule.messages_per_cycle
        notice = _next_reward_notice(
            campaign, rule, needed=needed, awarded_today=item.awarded_tickets
        )
        return (
            f"{title} · 我的抽奖加成票(发言)（本群）\n今日有效发言：{item.valid_messages} 条\n"
            f"本群抽奖加成票(发言)累计已入账：{item.total_awarded_tickets} 次\n"
            f"{notice}\n"
            f"{_personal_totals(detail)}"
        )

    async def _legacy(self, campaign, rules, *, user_id, chat_id, detail):
        progress = await self._queries.legacy_activity_progress(campaign.id, user_id)
        if progress is None:
            raise ValueError("本人旧活动进度不可读")
        count = dict(progress.group_counts).get(chat_id, 0)
        return (
            f"{html.escape(campaign.name)} · 我的抽奖加成票(发言)（原活动规则）\n"
            f"今日本群有效发言：{count}/{rules.activity.message_threshold} 条\n"
            "冻结的每日上限与全部群达标要求继续有效。\n" + _personal_totals(detail)
        )

    async def _ranking(self, campaign, user_id, *, page):
        result = await self._queries.reputation_ranking(campaign.id, page=page)
        lines = [
            f"{html.escape(campaign.name)} · 抽奖加成票(发言)排行 {result.page + 1}/{result.pages}",
            "按本场抽奖加成票(发言)已入账次数排序（含暂存），同分并列；排名不代表开奖资格。",
        ]
        lines.extend(
            f"{row.rank}. {html.escape(row.name[:40])}：{row.tickets} 次" for row in result.rows
        )
        if not result.rows:
            lines.append("暂无抽奖加成票(发言)入账记录。")
        return "\n".join(lines), _pages(
            "rank", campaign.public_ref, user_id=user_id, page=result.page, pages=result.pages
        )


def _rules(campaign, rules, chat_id):
    lines = [f"{campaign.name} · 抽奖加成票(发言)规则（本群）"]
    rule = rules.activity_rule_for(chat_id)
    lines.extend(_activity_rule_lines(rules, rule))
    lines.extend(global_bonus_lines(rules))
    invite = rules.invite_rule_for(chat_id)
    if invite:
        cap = (
            "不设上限"
            if invite.cap_mode == "unlimited"
            else f"本场最多{invite.max_bonus_tickets}次"
        )
        lines.append(f"本群每{invite.invites_per_cycle}个有效邀请增加1次；{cap}。")
    if rules.invitation_enabled:
        lines.append(
            f"本场邀请留存要求：入群满{rules.invite.retention_hours}小时并核验；同群同人一次。"
        )
    lines.extend(
        [
            "抽奖加成票(发言)就是本场发言奖励，没有永久积分或兑换扣分。",
            "先明确登记且通过成员等条件再计数；命令、查询、机器人、转发、空白不计数。",
            f"有效文字/图片/表情按北京时间自然日统计，相同内容在"
            f"{rules.activity.duplicate_window_minutes}分钟内只计第一条。",
            "邀请规则与抽奖加成票(发言)独立；同群同一人只能计一次。",
            "两类加成不替代门槛，旧活动保持本场冻结参数。",
        ]
    )
    gate = "\n".join(group_gate_lines(str(chat_id), rules))
    return "\n".join(html.escape(line) for line in lines) + ("\n" + gate if gate else "")


def _next_reward_notice(campaign, rule, *, needed, awarded_today):
    if campaign_bonus_completed(campaign):
        return "活动已截止，不再统计新增发言。"
    if (
        rule.daily_cap_mode == "limited"
        and awarded_today >= rule.reward_tickets * rule.daily_max_cycles
    ):
        return "今日抽奖加成票(发言)奖励已达本场冻结上限，明日可继续；本场累计奖励保留。"
    return f"下一轮还需 {needed} 条，达标增加 {rule.reward_tickets} 次；{activity_cap(rule)}。"


def _button(text, action, reference, *, user_id, page=0):
    return InlineKeyboardButton(
        text=text, callback_data=f"rep:{action}:{reference}:{user_id}:{page}"
    )


def _pages(action, reference, *, user_id, page, pages):
    row = []
    if page > 0:
        row.append(_button("← 上一页", action, reference, user_id=user_id, page=page - 1))
    if page + 1 < pages:
        row.append(_button("下一页 →", action, reference, user_id=user_id, page=page + 1))
    return [row] if row else []


async def _reply(message, text, *, rows, edit):
    sender = (
        (lambda text, **options: edit_panel(message, text, **options)) if edit else message.answer
    )
    await sender(
        text,
        parse_mode="HTML",
        reply_markup=style_keyboard(InlineKeyboardMarkup(inline_keyboard=rows)),
    )


def _parse_button(data):
    parts = data.split(":")
    if (
        len(parts) != 5
        or parts[1] not in COMMANDS.values()
        or not all(value.isdecimal() for value in parts[3:])
    ):
        raise ValueError("invalid reputation query button")
    return parts[1:]


def _personal_totals(detail):
    lines = [
        f"本场抽奖加成票(发言)累计（所有群）：{detail.activity} 次",
        f"基础：{detail.registration} 次；邀请加成票：{detail.invitation} 次"
        "（不计入抽奖加成票(发言)排行）",
        f"当前参与资格：{qualification_state_text(detail.qualification)}；当前有效次数：{current_effective_count(detail)}",
    ]
    if not detail.base_qualified:
        lines.append("行为门槛未完成，加成暂存；先完成本场各群门槛。")
    lines.append("当前次数仍需通过开奖前复核，累计加成不保证中奖。")
    return "\n".join(lines)


def _activity_rule_lines(rules, rule):
    if rule is None:
        return (
            ["本群未开启抽奖加成票(发言)。"]
            if rules.group_activity_rules or not rules.activity_bonus_enabled
            else []
        )
    if rules.daily_activity_bonus:
        return [
            daily_bonus_rule_text(rules.activity_bonus_double_count),
            f"本群加成条数：每天{rule.messages_per_cycle}条，相关抽奖门槛也须通过。",
        ]
    return [
        f"抽奖加成票(发言)：每 {rule.messages_per_cycle} 条有效发言"
        f"增加 {rule.reward_tickets} 次；{activity_cap(rule)}。"
    ]
