import html
from collections.abc import Mapping
from datetime import datetime, timedelta

from tg_lottery.domain.rules import CampaignRules
from tg_lottery.presentation.campaign_card import (
    bonus_block,
    campaign_link,
    card_blocks,
    condition_chats,
    description_blocks,
    footer_blocks,
    membership_blocks,
    prize_blocks,
    time_blocks,
)
from tg_lottery.presentation.mentions import html_utf16_length
from tg_lottery.telegram.bonus_rule_text import chat_title, per_group_bonus_lines
from tg_lottery.telegram.creator_invite_retention import retention_text
from tg_lottery.telegram.public_projection import format_public_time

TEXT_PAGE_UNITS = 3500
CAPTION_UNITS = 900


def utf16_units(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def gate_lines(rules: CampaignRules, chats: tuple) -> tuple[str, ...]:
    gate = rules.admission
    if gate is None:
        return ()
    names = {str(item["id"]): chat_title(item) for item in chats}
    title = "发言数" if gate.type == "message" else "邀请好友"
    lines = [f"本场门槛: {title}（所选群必须分别全部达标）"]
    for key, rule in gate.groups.items():
        if gate.type == "message":
            value = (
                f"连续 {rule.consecutive_days} 天，每天 {rule.daily_messages} 条，"
                "文字、图片、表情均计数"
            )
        else:
            value = f"已确认有效新成员 {rule.required_valid_invites} 人"
        lines.append(f"{names.get(key, key)}: {value}")
    lines.append("先明确登记并通过成员等条件再统计；本场完成一次即可，每日发言加成另算。")
    return tuple(lines)


def condition_blocks(payload: Mapping) -> tuple[str, ...]:
    rules = CampaignRules.model_validate(payload["rules"])
    chats = condition_chats(payload)
    blocks = list(membership_blocks(payload))
    blocks.extend(html.escape(line) for line in gate_lines(rules, chats))
    blocks.extend(html.escape(line) for line in message_count_blocks(rules))
    blocks.append("额外抽奖次数（不替代门槛）")
    blocks.extend(html.escape(line) for line in per_group_bonus_lines(rules, chats))
    if not rules.bonus_types:
        blocks.append("两种加成均关闭")
    blocks.extend(invitation_requirement_blocks(rules, payload))
    return tuple(blocks)


def invitation_requirement_blocks(rules: CampaignRules, payload: Mapping) -> tuple[str, ...]:
    if not rules.invitation_enabled:
        return ()
    deadline = datetime.fromisoformat(str(payload["cutoff_at"])) - timedelta(
        hours=rules.invite.retention_hours
    )
    return (
        "有效邀请要求: " + retention_text(rules.invite.retention_hours),
        "最晚有效入群: " + format_public_time(deadline, rules.timezone),
    )


def message_count_blocks(rules: CampaignRules) -> tuple[str, ...]:
    if rules.participation_rules_version != "v3.0":
        return ()
    if not rules.message_gate_rules and not rules.activity_bonus_enabled:
        return ()
    return (
        "发言按北京时间自然日统计，00:00 开始新一天；已取得的本场门槛资格和加成保留。",
        "有效发言：本人发送的文字、图片、表情、贴纸及 GIF 表情均计数，不限制字数；"
        "图片无需文字说明。命令、机器人、服务消息、转发及空白消息不计。",
        f"重复内容：同一用户在同一群 {rules.activity.duplicate_window_minutes} 分钟内"
        "大小写及空白归一后相同的文字，或文件标识及说明相同的媒体，"
        "只计第一条有效消息；不同群分别判断。",
        "统计起点：明确登记且首次通过成员等条件后才开始，登记前不补采，"
        "报名及行为统计截止后不再累计。",
    )


def public_rule_blocks(payload: Mapping) -> tuple[str, ...]:
    return (
        f"🎰 <b>{html.escape(str(payload['name']))}</b>",
        *condition_blocks(payload),
        *prize_blocks(payload),
        *description_blocks(payload),
        bonus_block(payload),
        *time_blocks(payload),
        *footer_blocks(payload),
    )


def activity_link(payload: Mapping) -> str:
    return campaign_link(payload)


def split_rule_blocks(blocks: tuple[str, ...]) -> tuple[str, ...]:
    pages, current = [], ""
    for block in blocks:
        if utf16_units(block) > TEXT_PAGE_UNITS:
            raise ValueError("单条条件或规则超过 Telegram 消息容量，请调整该项内容")
        candidate = current + "\n\n" + block if current else block
        if utf16_units(candidate) > TEXT_PAGE_UNITS:
            pages.append(current)
            current = block
        else:
            current = candidate
    if current:
        pages.append(current)
    return tuple(pages)


def rule_manifest(payload: Mapping) -> tuple[str, tuple[str, ...]]:
    blocks = public_rule_blocks(payload)
    text = "\n\n".join(card_blocks(payload))
    limit = CAPTION_UNITS if payload["presentation"].get("cover_file_id") else TEXT_PAGE_UNITS
    pages = split_rule_blocks(blocks)
    if html_utf16_length(text) <= limit:
        return text, ()
    summary = (
        f"🎰 <b>{html.escape(str(payload['name']))}</b>\n\n"
        f"📮 <b>参与条件：</b>正文较长，请查看完整规则（共 {len(pages)} 页）。\n\n"
        f"{bonus_block(payload)}\n\n" + "\n\n".join(time_blocks(payload)) + "\n\n"
        f"本场链接: {html.escape(activity_link(payload))}"
    )
    return summary, pages
