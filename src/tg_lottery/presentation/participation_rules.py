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
from tg_lottery.presentation.rule_pages import contextual_pages
from tg_lottery.telegram.bonus_rule_text import chat_title, per_group_bonus_lines
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
    return (*membership_blocks(payload), *detail_rule_blocks(payload))


def detail_rule_blocks(payload: Mapping) -> tuple[str, ...]:
    rules = CampaignRules.model_validate(payload["rules"])
    chats = condition_chats(payload)
    blocks = []
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
    return ("最晚有效入群: " + format_public_time(deadline, rules.timezone),)


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
        f"✨ <b>{html.escape(str(payload['name']))}</b>",
        *membership_blocks(payload),
        *prize_blocks(payload),
        *description_blocks(payload),
        bonus_block(payload),
        *time_blocks(payload),
        *footer_blocks(payload),
        "📖 完整统计与加成规则",
        *detail_rule_blocks(payload),
    )


def activity_link(payload: Mapping) -> str:
    return campaign_link(payload)


def split_rule_blocks(blocks: tuple[str, ...], *, limit=TEXT_PAGE_UNITS) -> tuple[str, ...]:
    pages, current = [], ""
    for block in blocks:
        if html_utf16_length(block) > limit:
            raise ValueError("单条条件或规则超过 Telegram 消息容量，请调整该项内容")
        candidate = current + "\n\n" + block if current else block
        if html_utf16_length(candidate) > limit:
            pages.append(current)
            current = block
        else:
            current = candidate
    if current:
        pages.append(current)
    return tuple(pages)


def complete_rule_pages(payload: Mapping, blocks=None) -> tuple[str, ...]:
    return contextual_pages(
        split_rule_blocks(blocks or public_rule_blocks(payload), limit=3200),
        name=str(payload["name"]),
        url=campaign_link(payload),
    )


def rule_manifest(payload: Mapping) -> tuple[str, tuple[str, ...]]:
    blocks = public_rule_blocks(payload)
    text = "\n\n".join(card_blocks(payload))
    limit = CAPTION_UNITS if payload["presentation"].get("cover_file_id") else TEXT_PAGE_UNITS
    pages = complete_rule_pages(payload, blocks)
    if html_utf16_length(text) <= limit:
        return text, ()
    compact = compact_card(payload)
    if html_utf16_length(compact) <= limit:
        return compact, pages
    return summary_card(payload, page_count=len(pages), limit=limit), pages


def compact_card(payload: Mapping) -> str:
    rule_url = html.escape(campaign_link(payload, "rules"), quote=True)
    return "\n\n".join(
        (
            f"✨ <b>{html.escape(str(payload['name']))}</b>",
            "\n".join(membership_blocks(payload)),
            "\n".join(prize_blocks(payload)),
            f'📝 <b>抽奖说明：</b>正文较长，<a href="{rule_url}">查看完整说明与规则</a>',
            bonus_block(payload),
            *time_blocks(payload),
            *footer_blocks(payload),
        )
    )


def summary_card(payload: Mapping, *, page_count: int, limit: int) -> str:
    prefix = (
        f"✨ <b>{html.escape(str(payload['name']))}</b>\n\n"
        f"🧭 <b>参与条件：</b>正文较长，完整清单见规则消息（共 {page_count} 页）。\n\n"
        "🎁 <b>奖品内容：</b>"
    )
    url = html.escape(campaign_link(payload, "rules"), quote=True)
    remainder = f'<a href="{url}">查看全部奖品、条件与说明</a>'
    tail = "\n\n".join((bonus_block(payload), *time_blocks(payload), *footer_blocks(payload)))
    visible = []
    total = len(payload.get("prizes") or [])
    base = "\n".join((prefix, *_remaining_prizes(total), remainder)) + "\n\n" + tail
    if html_utf16_length(base) > limit:
        tail = "\n\n".join(
            (bonus_block(payload), *time_blocks(payload), footer_blocks(payload)[-1])
        )
    for prize in prize_blocks(payload)[1:]:
        remaining = _remaining_prizes(total - len(visible) - 1)
        candidate = "\n".join((prefix, *visible, prize, *remaining, remainder)) + "\n\n" + tail
        if html_utf16_length(candidate) > limit:
            break
        visible.append(prize)
    visible.extend(_remaining_prizes(total - len(visible)))
    text = "\n".join((prefix, *visible, remainder)) + "\n\n" + tail
    if html_utf16_length(text) > limit:
        raise ValueError("公告摘要仍超过容量，请缩短活动名称或奖品设置")
    return text


def _remaining_prizes(count: int) -> tuple[str, ...]:
    return (f"另有 {count} 项奖品，完整名称与名额见规则。",) if count else ()
