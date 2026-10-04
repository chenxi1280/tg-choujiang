from typing import Any

from tg_lottery.telegram.creator_base_activity import base_rules_text
from tg_lottery.telegram.creator_behavior_views import admission_summary
from tg_lottery.telegram.creator_views import preview_text
from tg_lottery.telegram.draft_config import rules_from_data
from tg_lottery.telegram.member_condition_views import condition_instruction, human_rule

PAGE_UTF16_UNITS = 3500


def rule_review_pages(data: dict[str, Any]) -> tuple[str, ...]:
    rules = rules_from_data(data)
    chats = tuple(data.get("participation_groups") or []) + tuple(data.get("required_chats") or [])
    overview = preview_text(data).replace("🎰 抽奖预览", "📋 发布前完整规则")
    overview = overview.replace("（本页面为预览文案，不是正式效果）", "")
    gate = (
        admission_summary(data)
        if data.get("participation_rules_version")
        else base_rules_text(data)
    )
    sections = [overview, gate, "逐项参与条件（必须全部满足）"]
    sections.extend(condition_instruction(chat, rules) for chat in chats)
    sections.extend(
        [
            human_rule(rules, chats),
            f"领奖方式：{data.get('claim_instructions') or '联系发奖人员'}",
            f"组合分享链接：{data.get('folder_link') or '未设置'}",
            "以上仅供核对；返回发布确认后再确认发布，阅读不会修改规则。",
        ]
    )
    return _split_pages("\n\n".join(sections))


def _split_pages(text: str) -> tuple[str, ...]:
    pages, current = [], []
    units = 0
    for character in text:
        width = len(character.encode("utf-16-le")) // 2
        if units + width > PAGE_UTF16_UNITS:
            pages.append("".join(current))
            current, units = [], 0
        current.append(character)
        units += width
    if current:
        pages.append("".join(current))
    return tuple(pages)


def review_navigation(position: int, count: int) -> list[list[tuple[str, str]]]:
    navigation = []
    if position > 0:
        navigation.append(("← 上一页", "draft_publish_rules_prev"))
    if position + 1 < count:
        navigation.append(("下一页 →", "draft_publish_rules_next"))
    rows = [navigation] if navigation else []
    return [*rows, [("返回发布确认", "draft_publish")]]
