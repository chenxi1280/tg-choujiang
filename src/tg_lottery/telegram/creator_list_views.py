from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.application.creator_campaign_pages import CREATOR_PAGE_SIZE
from tg_lottery.domain.enums import CampaignState
from tg_lottery.telegram.public_projection import campaign_state_text, format_public_time

CATEGORIES = (("进行中", "active"), ("已开奖", "drawn"), ("已取消", "cancelled"))


def creator_list_view(result) -> tuple[str, InlineKeyboardMarkup]:
    scope = "我可管理的活动（进入时实时核验全部参与群权限）" if result.managed else "我发起的抽奖"
    lines = [scope, "", f"共 {result.total} 场 · 第 {result.page + 1}/{result.page_count} 页"]
    prefix = "nav:managed" if result.managed else "nav:created"
    categories = CATEGORIES + ((("待核查", "unconfirmed"),) if result.unconfirmed else ())
    rows = _category_rows(categories, prefix=prefix, selected=result.category)
    lines.extend(_summary_lines(result.summary))
    lines.append("当前分类：" + dict((key, label) for label, key in categories)[result.category])
    if result.unconfirmed:
        lines.append(f"未确认（待核查）：{result.unconfirmed}")
    for number, campaign in enumerate(result.rows, result.page * CREATOR_PAGE_SIZE + 1):
        lines.append(
            f"{number}. {campaign.name}｜{_campaign_label(campaign, result.category)}\n"
            f"  开奖 {format_public_time(campaign.draw_at, campaign.timezone)}"
        )
        rows.append(
            [_button(f"{number}. {campaign.name}", _campaign_route(campaign.public_ref, result))]
        )
    if not result.rows:
        lines.append("当前分类暂无活动。可切换分类或创建新抽奖。")
    rows.extend(_list_pages(result, prefix))
    other = ("我发起的抽奖", "nav:created") if result.managed else ("我可管理的活动", "nav:managed")
    rows.extend(
        [[_button("创建新抽奖", "nav:new")], [_button(*other)], [_button("返回首页", "nav:index")]]
    )
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _button(label: str, callback: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=label, callback_data=callback)


def _campaign_route(reference, result):
    scope = "m" if result.managed else "c"
    category = {"active": "a", "drawn": "d", "cancelled": "x", "unconfirmed": "u"}[result.category]
    return f"nav:campaign:{reference}:{scope}:{category}:{result.page}"


def campaign_origin(reference: str):
    parts = reference.split(":")
    if len(parts) == 1:
        return reference, "nav:created"
    ref, scope, category, page = parts
    prefix = "nav:managed" if scope == "m" else "nav:created"
    category = {"a": "active", "d": "drawn", "x": "cancelled", "u": "unconfirmed"}[category]
    return ref, f"{prefix}:{category}:{int(page)}"


def _campaign_label(campaign, category):
    if category == "active":
        return campaign_state_text(campaign.state)
    label = {"drawn": "已开奖", "cancelled": "已取消", "unconfirmed": "结果待核查"}[category]
    return label + (" · 已归档" if campaign.state is CampaignState.ARCHIVED else "")


def _list_pages(result, prefix):
    pages = []
    if result.page:
        pages.append(_button("上一页", f"{prefix}:{result.category}:{result.page - 1}"))
    if result.page + 1 < result.page_count:
        pages.append(_button("下一页", f"{prefix}:{result.category}:{result.page + 1}"))
    return [pages] if pages else []


def _category_rows(categories, *, prefix, selected):
    return [
        [
            _button(("✅ " if category == selected else "") + label, f"{prefix}:{category}:0")
            for label, category in categories
        ]
    ]


def _summary_lines(summary):
    if summary is None:
        return []
    return [
        f"累计创建：{summary.total} · 进行中：{summary.active} · "
        f"已开奖：{summary.drawn} · 已取消：{summary.cancelled}"
    ]
