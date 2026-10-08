"""Native Telegram announcement layout shared by publication and sharing."""

import html
from collections.abc import Mapping
from datetime import datetime
from zoneinfo import ZoneInfo

from tg_lottery.domain.condition_targets import frozen_condition_targets
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.presentation.rule_pages import escaped_text_blocks
from tg_lottery.telegram.bonus_rule_text import chat_title
from tg_lottery.telegram.public_projection import BEIJING_TIMEZONE

CARD_TIME_FORMAT = "%Y年%m月%d日 %H时%M分%S秒"


def campaign_link(payload: Mapping, action: str = "join") -> str:
    username = str(payload["bot_username"]).removeprefix("@")
    return f"https://t.me/{username}?start={action}_{payload['campaign_ref']}"


def condition_chats(payload: Mapping) -> tuple[Mapping, ...]:
    presentation = payload["presentation"]
    rules = CampaignRules.model_validate(payload["rules"])
    values = frozen_condition_targets(presentation, platform_policy=rules.platform_policy)
    by_id = {str(chat["id"]): chat for chat in values}
    gate_keys = rules.admission.groups if rules.admission else rules.base_activity_rules
    for key in gate_keys:
        if key not in by_id:
            by_id[key] = {"id": int(key), "type": "supergroup"}
    return tuple(by_id.values())


def chat_condition(chat: Mapping, *, payload: Mapping, rules: CampaignRules) -> str:
    action = "关注" if chat.get("type") == "channel" else "加入"
    position = next(
        index
        for index, item in enumerate(condition_chats(payload), 1)
        if str(item["id"]) == str(chat["id"])
    )
    url = campaign_link(payload, "condition") + f"_{position}"
    if not chat.get("private") and chat.get("join_url"):
        url = str(chat["join_url"])
    title = html.escape(chat_title(chat))
    link = html.escape(url, quote=True)
    status = " (加入入口已关闭)" if chat.get("invite_revoked_at") else ""
    lines = [f'🤝 {action}-<a href="{link}">{title}</a>{status}']
    if chat.get("type") != "channel":
        lines.extend(group_gate_lines(str(chat["id"]), rules))
    return "\n".join(lines)


def group_gate_lines(key: str, rules: CampaignRules) -> tuple[str, ...]:
    if rules.admission and rules.admission.type == "invite":
        gate = rules.admission.groups.get(key)
        if gate is None:
            return ()
        return (f"└ 🚪 有效邀请满 <b>{gate.required_valid_invites}</b> 人",)
    gate = rules.message_gate_rules.get(key)
    if gate is not None:
        period = "单日" if gate.consecutive_days == 1 else f"连续 {gate.consecutive_days} 天，每天"
        return (f"└ 🚪 {period}有效发言满 <b>{gate.daily_messages}</b> 条",)
    threshold = rules.message_thresholds.get(key)
    if threshold:
        return (f"└ 🚪 累计发言至少 <b>{threshold}</b> 条",)
    return ()


def membership_blocks(payload: Mapping) -> tuple[str, ...]:
    rules = CampaignRules.model_validate(payload["rules"])
    blocks = ["🧭 <b>参与条件（必须全部满足）：</b>"]
    if rules.platform_policy:
        url = html.escape(campaign_link(payload), quote=True)
        blocks.append(f'🤖 <a href="{url}">启动本抽奖机器人</a>，并关注以下三个官方频道')
    if rules.username_required:
        blocks.append("🪪 必须设置 Telegram 用户名")
    if rules.allowed_regions:
        blocks.append("🌏️ 归属地：" + html.escape("、".join(rules.allowed_regions)))
    blocks.extend(
        chat_condition(chat, payload=payload, rules=rules) for chat in condition_chats(payload)
    )
    if len(blocks) == 1:
        blocks.append("无额外成员条件")
    if rules.message_gate_rules:
        blocks.append("门槛按北京时间自然日统计，各群分别达标；先登记再累计，本场完成一次即可。")
    elif rules.invite_admission_enabled:
        blocks.append("各群有效邀请分别达标；先登记，再通过本场本人邀请入口邀请新成员。")
    return tuple(blocks)


def prize_block(payload: Mapping) -> str:
    return "\n".join(prize_blocks(payload))


def prize_blocks(payload: Mapping) -> tuple[str, ...]:
    prizes = (
        f"📦 {html.escape(str(prize['name']))} × <b>{prize['winner_count']}</b>"
        for prize in payload.get("prizes") or []
    )
    return ("🎁 <b>奖品内容：</b>", *prizes)


def description_blocks(payload: Mapping) -> tuple[str, ...]:
    description = payload["presentation"].get("description")
    if not description:
        return ()
    first, *remaining = escaped_text_blocks(str(description))
    return ("📝 <b>抽奖说明：</b>\n" + first, *remaining)


def bonus_block(payload: Mapping) -> str:
    rules = CampaignRules.model_validate(payload["rules"])
    if rules.bonus_policy_version == "unlimited-v1":
        return unlimited_bonus_block(payload, rules=rules)
    return _bonus_lines(payload, rules=rules, unlimited=False)


def unlimited_bonus_block(payload: Mapping, *, rules: CampaignRules) -> str:
    return _bonus_lines(payload, rules=rules, unlimited=True)


def _bonus_lines(payload: Mapping, *, rules: CampaignRules, unlimited: bool) -> str:
    url = html.escape(campaign_link(payload, "rules"), quote=True)
    rule_link = f'<a href="{url}">规则</a>'
    activity = (
        ("活跃加成，不设上限" if unlimited else "活跃加成，按本场规则")
        if rules.activity_bonus_enabled
        else "未开启"
    )
    invite = ("不设上限" if unlimited else "按本场规则") if rules.invite_bonus_enabled else "未开启"
    lines = [
        f"🌱 <b>人品加成：</b>{activity}　{rule_link}",
        f"🤝 <b>邀请加成：</b>{invite}　{rule_link}",
    ]
    if rules.activity_bonus_enabled:
        lines.append("└ 群内输入：<b>人品</b> · <b>人品排行</b> · <b>人品规则</b>")
    return "\n".join(lines)


def time_blocks(payload: Mapping) -> tuple[str, ...]:
    timezone = str(payload["timezone"])
    cutoff = card_time(payload["cutoff_at"], timezone)
    if payload["rules"].get("draw_mode") == "full":
        draw = (
            f"🎊 <b>满人开奖：</b>\n达到 {payload['rules']['participant_target']} 人参与后立刻开奖"
        )
    else:
        moment = card_time(payload["draw_at"], timezone)
        draw = "📅 <b>开奖日期：</b>（北京时间）\n" + moment
    return ("⏳ <b>报名及行为统计截止：</b>（北京时间）\n" + cutoff, draw)


def card_time(value: str, timezone: str) -> str:
    zone = ZoneInfo(timezone)
    moment = datetime.fromisoformat(str(value))
    localized = moment.replace(tzinfo=zone) if moment.tzinfo is None else moment.astimezone(zone)
    return localized.astimezone(ZoneInfo(BEIJING_TIMEZONE)).strftime(CARD_TIME_FORMAT)


def footer_blocks(payload: Mapping) -> tuple[str, ...]:
    issuer = payload["presentation"].get("issuer_name")
    blocks = [f"🧑‍💼 发奖人员：{html.escape(str(issuer))}"] if issuer else []
    blocks.append(
        "🔗 点击链接参与抽奖（长按本消息可转发）：\n" + html.escape(campaign_link(payload))
    )
    return tuple(blocks)


def card_blocks(payload: Mapping) -> tuple[str, ...]:
    return (
        f"✨ <b>{html.escape(str(payload['name']))}</b>",
        "\n".join(membership_blocks(payload)),
        prize_block(payload),
        *description_blocks(payload),
        bonus_block(payload),
        *time_blocks(payload),
        *footer_blocks(payload),
    )
