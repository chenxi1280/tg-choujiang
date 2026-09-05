from __future__ import annotations

from typing import Any

from aiogram.types import InlineKeyboardButton

from tg_lottery.application.campaign_targets import QualificationTarget
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import QualificationState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.telegram.bonus_rule_text import chat_title, per_group_bonus_lines
from tg_lottery.telegram.member_popups import failure_reply_text


def conditions(campaign: object) -> tuple[dict[str, object], ...]:
    presentation = getattr(campaign, "presentation", {}) or {}
    participation = presentation.get("participation_groups") or []
    main = presentation.get("main_chat")
    extras = presentation.get("required_chats") or []
    values = list(participation) + list(extras)
    if not participation and isinstance(main, dict):
        values.insert(0, main)
    unique = {int(item["id"]): item for item in values if isinstance(item, dict)}
    return tuple(unique.values())


def draw_rule(rules: CampaignRules) -> str:
    if rules.draw_mode == "full":
        return f"开奖方式: 满 {rules.participant_target} 人后进入结算，30 天中午兜底"
    return "开奖方式: 定时开奖"


def human_rule(rules: CampaignRules, chats: tuple[dict[str, object], ...] = ()) -> str:
    if not rules.bonus_types:
        return "抽奖加成: 关闭，仅计算基础抽奖次数"
    if rules.group_invite_rules or rules.group_activity_rules:
        lines = per_group_bonus_lines(rules, chats)
        return "抽奖加成（各群独立累计）:\n" + "\n".join(lines)
    parts = []
    if rules.invite_bonus_enabled:
        parts.append(invite_rule_text(rules))
    if rules.activity_bonus_enabled:
        parts.append(
            f"每个条件群每天有效发言 {rules.activity.message_threshold} 条，"
            f"全部达标+{rules.activity.reward_tickets} 次"
        )
    return "抽奖加成: " + "；".join(parts)


def invite_rule_text(rules: CampaignRules) -> str:
    if rules.invite.mode == "ratio":
        return (
            f"每邀请 {rules.invite.valid_invites_per_ticket} 个有效新成员+1次，"
            f"上限 {rules.invite.max_bonus_tickets} 次"
        )
    if not rules.invite.reward_tiers:
        return f"每个有效邀请+{rules.invite.reward_tickets}次"
    tiers = ", ".join(
        f"{tier.invite_count}人+{tier.total_bonus}次" for tier in rules.invite.reward_tiers
    )
    return f"邀请 {tiers}，上限 {rules.invite.max_bonus_tickets} 次"


def issuer_name(campaign: object) -> str:
    presentation = getattr(campaign, "presentation", {}) or {}
    return str(presentation.get("issuer_name") or "活动创建者")


def deletion_rule(campaign: object) -> str:
    presentation = getattr(campaign, "presentation", {}) or {}
    trigger = "删除报名命令" if presentation.get("delete_trigger_message") else "保留报名命令"
    seconds = presentation.get("completion_delete_after_seconds")
    if seconds is None:
        return f"消息删除: {trigger}; 完成提示不自动删除"
    return f"消息删除: {trigger}; 完成提示 {int(seconds)} 秒后删除"


def condition_summary(campaign: object) -> str:
    labels = []
    for chat in conditions(campaign):
        suffix = " (加入入口已关闭)" if chat.get("invite_revoked_at") else ""
        labels.append(f"{chat['title']}{suffix}")
    if len(labels) > 3:
        return f"参与条件: 共 {len(labels)} 项（请点击查看加入入口）"
    return "参与条件: " + ("、".join(labels) if labels else "无额外条件")


def condition_action(chat: dict[str, object]) -> str:
    return "关注频道" if chat.get("type") == "channel" else "加入群聊"


def condition_instruction(chat: dict[str, object], rules: CampaignRules) -> str:
    instruction = f"请{condition_action(chat)}「{chat_title(chat)}」"
    threshold = rules.message_thresholds.get(str(chat["id"]))
    if threshold and chat.get("type") != "channel":
        instruction += f"；报名需在本群累计发言至少 {threshold} 条"
    return instruction + "，完成加入后点击验证资格。"


def condition_navigation(
    campaign_ref: str, position: int, count: int
) -> list[InlineKeyboardButton]:
    buttons = []
    if position > 1:
        buttons.append(
            InlineKeyboardButton(
                text="← 上一项",
                callback_data=f"member:condition:{campaign_ref}:{position - 1}",
            )
        )
    if position < count:
        buttons.append(
            InlineKeyboardButton(
                text="下一项 →",
                callback_data=f"member:condition:{campaign_ref}:{position + 1}",
            )
        )
    return buttons


def with_target_kinds(
    observations: tuple[dict[str, Any], ...],
    target_kinds: tuple[tuple[int, str], ...],
) -> tuple[dict[str, Any], ...]:
    kinds = dict(target_kinds)
    return tuple(
        dict(item) | {"target_kind": kinds.get(int(item["chat_id"]), "requirement")}
        for item in observations
    )


def target_state(target: QualificationTarget, observations: dict[int, dict[str, object]]) -> str:
    observation = observations.get(target.chat_id)
    return str(observation.get("state")) if observation else "unknown"


def target_joinable(campaign: object, target: QualificationTarget) -> bool:
    if utcnow() > campaign.cutoff_at or target.revoked_at is not None:
        return False
    return bool(target.public_join_url or target.encrypted_join_url)


def target_button(target: QualificationTarget) -> str:
    action = "关注" if target.chat_type == "channel" else "加入"
    return f"{action} {target.title}"


def missing_targets_text(
    campaign: object,
    state: QualificationState,
    *,
    missing: list[QualificationTarget],
    pending: list[QualificationTarget],
    unknown: list[QualificationTarget],
    page: tuple[int, int],
) -> str:
    lines = [failure_reply_text(campaign, state)]
    if page[1] > 1:
        lines.append(f"缺失条件第 {page[0]}/{page[1]} 页")
    participation = [item for item in missing if item.kind == "participation_group"]
    requirements = [item for item in missing if item.kind == "requirement"]
    _append_target_lines(lines, "参与群（必须全部加入）", participation)
    _append_target_lines(lines, "准入条件（必须全部满足）", requirements)
    _append_pending_lines(lines, pending)
    _append_unknown_lines(lines, unknown)
    if not missing and not pending and not unknown:
        lines.append("\n资格事实已经变化，请点击重新验证。")
    return "\n".join(lines)


def _append_pending_lines(lines: list[str], targets: list[QualificationTarget]) -> None:
    if not targets:
        return
    lines.append("\n等待管理员批准：")
    lines.extend(f"- {item.title}（等待批准）" for item in targets)


def _append_unknown_lines(lines: list[str], targets: list[QualificationTarget]) -> None:
    if not targets:
        return
    lines.append("\n暂时无法核验：")
    lines.extend(f"- {item.title}" for item in targets)


def _append_target_lines(lines: list[str], title: str, targets: list[QualificationTarget]) -> None:
    if not targets:
        return
    lines.append(f"\n{title}：")
    for target in targets:
        action = "关注" if target.chat_type == "channel" else "加入"
        unavailable = not target.public_join_url and not target.encrypted_join_url
        suffix = "（入口不可用）" if unavailable else ""
        lines.append(f"- {action}「{target.title}」{suffix}")
