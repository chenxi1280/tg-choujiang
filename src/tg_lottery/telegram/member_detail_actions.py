from aiogram.types import InlineKeyboardButton

from tg_lottery.domain.enums import CampaignState, QualificationState

TERMINAL_STATES = frozenset({CampaignState.DRAWN, CampaignState.ARCHIVED, CampaignState.CANCELLED})
REGISTRATION_STATES = frozenset({CampaignState.RUNNING, CampaignState.SCHEDULED})
INVITATION_STATES = REGISTRATION_STATES | {CampaignState.PAUSED}


def detail_task(detail, now):
    ref = detail.public_ref
    if detail.state in TERMINAL_STATES:
        return "", None
    if detail.cutoff_at is not None and now > detail.cutoff_at:
        return "本期已截止：只查看已保存进度、待核验事实和开奖状态，不再新增任务。", None
    if detail.state is CampaignState.PUBLISHING:
        return "发布尚未完成，尚未开放登记；已有本人记录保留。", _button(
            "查看发布中规则", f"m:rules:{ref}:0"
        )
    if detail.state not in INVITATION_STATES:
        return "本期正在结算或处理开奖；已有进度保留，不再新增任务。", None
    return _available_task(detail, now)


def _available_task(detail, now):
    ref = detail.public_ref
    if not detail.registration_completed and detail.state is CampaignState.PAUSED:
        return "⏸️ 活动暂停，报名尚未完成；恢复后可继续核验，原进度保留。", None
    if not detail.registration_completed:
        return "⏳ 报名待完成，请补齐条件后点击核验并报名。", _button(
            "核验并报名", f"member:join:{ref}"
        )
    if detail.operational_issues:
        return "请查看具体故障目标，历史奖励保留。", _button(
            "查看运行阻断与条件", f"m:conditions:{ref}:0"
        )
    if detail.qualification is not QualificationState.ELIGIBLE:
        label = (
            "核查参与条件"
            if detail.qualification is QualificationState.UNKNOWN
            else "查看并补齐参与条件"
        )
        return (
            "请先补齐入群、关注及资料条件并重新核验；前置条件未通过时发言不计数，"
            "此前发言不补算，历史门槛及奖励不会重置。"
        ), _button(label, f"m:conditions:{ref}:0")
    if not detail.base_qualified:
        return _gate_task(detail, now)
    if detail.bonus_enabled:
        return "门槛已完成，额外奖励自动入账；可查看当前可用任务和奖励来源。", _button(
            "加成明细", f"m:bo:{ref}"
        )
    return "当前条件合格，等待截止复核与自然开奖。", _button("刷新本人状态", f"m:detail:{ref}")


def _gate_task(detail, now):
    ref = detail.public_ref
    if detail.admission_type == "invite":
        if invitation_task_available(detail, now):
            return "本场邀请门槛未完成，请查看本群个人邀请任务。", _button(
                "我的邀请链接", f"member:bonus_invites:{ref}:0"
            )
        return "最晚计奖入群时间已过，仅等待此前邀请核验；从零开始已无可完成窗口。", _button(
            "查看待核验邀请记录", f"member:invites:{ref}:0"
        )
    notice = "本场发言门槛未完成，请查看各群进度及剩余时间。"
    if detail.state is CampaignState.PAUSED:
        notice = "本场人工暂停，发言停止新增计数；截止不顺延，已有门槛及奖励保留。"
    if detail.state is CampaignState.SCHEDULED:
        notice = "本场尚未运行，发言计数尚未开始；查看已有门槛规则和进度。"
    return notice, _button("查看门槛进度", f"m:gate:{ref}")


def invitation_task_available(detail, now) -> bool:
    return (
        detail.state in INVITATION_STATES
        and (detail.cutoff_at is None or now <= detail.cutoff_at)
        and (detail.invite_join_deadline is None or now <= detail.invite_join_deadline)
    )


def _button(label, route):
    return InlineKeyboardButton(text=label, callback_data=route)
