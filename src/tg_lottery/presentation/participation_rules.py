import html
from collections.abc import Mapping
from datetime import datetime, timedelta

from tg_lottery.domain.rules import CampaignRules
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
                f"每条至少 {rule.min_text_length} 个有效字符"
            )
        else:
            value = f"已确认有效新成员 {rule.required_valid_invites} 人"
        lines.append(f"{names.get(key, key)}: {value}")
    lines.append("先明确登记并通过成员等条件再统计；本场完成一次即可，每日发言加成另算。")
    return tuple(lines)


def condition_blocks(payload: Mapping) -> tuple[str, ...]:
    rules = CampaignRules.model_validate(payload["rules"])
    chats = tuple(payload["presentation"].get("participation_groups") or []) + tuple(
        payload["presentation"].get("required_chats") or []
    )
    link = activity_link(payload)
    blocks = ["参与条件（必须全部满足）"]
    if rules.username_required:
        blocks.append("必须设置 Telegram 用户名")
    if rules.allowed_regions:
        blocks.append("归属地区: " + html.escape("、".join(rules.allowed_regions)))
    blocks.extend(_chat_condition(chat, link) for chat in chats)
    blocks.extend(html.escape(line) for line in gate_lines(rules, chats))
    blocks.append("额外抽奖次数（不替代门槛）")
    blocks.extend(html.escape(line) for line in per_group_bonus_lines(rules, chats))
    if not rules.bonus_types:
        blocks.append("两种加成均关闭")
    if rules.invitation_enabled:
        deadline = datetime.fromisoformat(str(payload["cutoff_at"])) - timedelta(
            hours=rules.invite.retention_hours
        )
        blocks.extend(
            [
                "有效邀请要求: " + retention_text(rules.invite.retention_hours),
                "最晚有效入群: " + format_public_time(deadline, rules.timezone),
            ]
        )
    return tuple(blocks)


def _chat_condition(chat, link):
    action = "关注" if chat.get("type") == "channel" else "加入"
    url = str(link if chat.get("private") else chat.get("join_url") or link)
    return f'{action} <a href="{html.escape(url, quote=True)}">{html.escape(chat_title(chat))}</a>'


def public_rule_blocks(payload: Mapping) -> tuple[str, ...]:
    timezone = str(payload["timezone"])
    blocks = [
        f"🎰 <b>{html.escape(str(payload['name']))}</b>",
        "开奖时间: " + format_public_time(payload["draw_at"], timezone),
        "报名及行为统计截止: " + format_public_time(payload["cutoff_at"], timezone),
        *condition_blocks(payload),
        "奖品:",
    ]
    blocks.extend(
        f"{html.escape(str(item['name']))} × {item['winner_count']} 人"
        for item in payload.get("prizes") or []
    )
    description = payload["presentation"].get("description")
    if description:
        blocks.append(html.escape(str(description)))
    blocks.append(
        f'<a href="{html.escape(activity_link(payload), quote=True)}">查看条件并参与本场抽奖</a>'
    )
    return tuple(blocks)


def activity_link(payload: Mapping) -> str:
    username = str(payload["bot_username"]).removeprefix("@")
    return f"https://t.me/{username}?start=join_{payload['campaign_ref']}"


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
    text = "\n\n".join(blocks)
    limit = CAPTION_UNITS if payload["presentation"].get("cover_file_id") else TEXT_PAGE_UNITS
    if utf16_units(text) <= limit:
        return text, ()
    pages = split_rule_blocks(blocks)
    summary = (
        f"🎰 {html.escape(str(payload['name']))}\n"
        f"参与条件见本群完整规则消息（共 {len(pages)} 页）。\n"
        f"截止: {format_public_time(payload['cutoff_at'], str(payload['timezone']))}\n"
        f"开奖: {format_public_time(payload['draw_at'], str(payload['timezone']))}\n"
        f"本场链接: {html.escape(activity_link(payload))}"
    )
    return summary, pages
