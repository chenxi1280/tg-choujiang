from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.member_window_guidance import window_guidance

GATE_PAGE_SIZE = 6


async def gate_payload(queries, *, campaign, detail, rules, targets, route, cipher):
    lines = await queries.gate_group_lines(detail_user_id=detail[0], reference=campaign.public_ref)
    if lines is None:
        raise UserFacingError("没有本人的门槛记录，查看规则不会自动登记")
    context = {
        "campaign": campaign,
        "rules": rules,
        "targets": targets,
        "cipher": cipher,
        "lines": lines,
        "detail": detail[1],
    }
    groups = tuple(rules.admission.groups)
    if route.startswith("m:gg:"):
        parts = route.split(":")
        return _group_payload(context, chat_id=parts[3], page=int(parts[4]))
    if len(groups) == 1:
        return _group_payload(context, chat_id=groups[0], page=0)
    page = int(route.split(":")[3]) if len(route.split(":")) > 3 else 0
    return _list_payload(context, groups, page=page)


def _list_payload(context, groups, *, page):
    count = max(1, (len(groups) + GATE_PAGE_SIZE - 1) // GATE_PAGE_SIZE)
    page = min(max(0, page), count - 1)
    reference = context["campaign"].public_ref
    visible = groups[page * GATE_PAGE_SIZE : (page + 1) * GATE_PAGE_SIZE]
    lines = [f"本场门槛 · 逐群进度 · 第{page + 1}/{count}页"]
    rows = []
    for number, key in enumerate(visible, page * GATE_PAGE_SIZE + 1):
        text = context["lines"][key]
        lines.append(f"{number}. {text}")
        rows.append([_button(f"查看第{number}群任务", f"m:gg:{reference}:{key}:{page}")])
    paging = []
    if page:
        paging.append(_button("上一页", f"m:gate:{reference}:{page - 1}"))
    if page + 1 < count:
        paging.append(_button("下一页", f"m:gate:{reference}:{page + 1}"))
    if paging:
        rows.append(paging)
    rows.append([_button("返回活动详情", f"m:detail:{reference}")])
    return "\n\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _group_payload(context, *, chat_id, page):
    if chat_id not in context["lines"]:
        raise UserFacingError("该群不属于本场门槛，请重新选择")
    campaign, rules = context["campaign"], context["rules"]
    text = context["lines"][chat_id] + "\n\n" + window_guidance(campaign, rules)
    rows = []
    target = next(
        (item for item in context["targets"] if str(item.rule_chat_id or item.chat_id) == chat_id),
        None,
    )
    if _speaking_available(campaign, rules) and target is not None:
        url = _target_url(target, context["cipher"])
        if url:
            rows.append([InlineKeyboardButton(text="去本群发言", url=url)])
        else:
            text += "\n本群没有已确认可用的加入入口，请联系创建者。"
    if rules.admission.type == "invite":
        rows.append([_button("查看本群邀请任务", f"m:id:{campaign.public_ref}:{chat_id}:0")])
        rows.append(
            [_button("本群待核验记录", f"m:ir:{campaign.public_ref}:{chat_id}:0:pending:0")]
        )
    parent = (
        f"m:gate:{campaign.public_ref}:{page}"
        if len(context["lines"]) > 1
        else f"m:detail:{campaign.public_ref}"
    )
    rows.append([_button("返回门槛进度" if len(context["lines"]) > 1 else "返回活动详情", parent)])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _speaking_available(campaign, rules):
    return (
        rules.admission.type == "message"
        and campaign.state is CampaignState.RUNNING
        and campaign.preheat_at <= utcnow() <= campaign.cutoff_at
    )


def _target_url(target, cipher):
    if target.public_join_url:
        return target.public_join_url
    if target.revoked_at is None and target.encrypted_join_url is not None:
        return cipher.decrypt(target.encrypted_join_url, context=target.secret_context)
    return None


def _button(text, route):
    return InlineKeyboardButton(text=text, callback_data=route)
