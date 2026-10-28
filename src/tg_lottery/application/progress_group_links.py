"""Attach each progress label to the correct frozen participation group."""

import re

from sqlalchemy import select

from tg_lottery.domain.condition_targets import frozen_condition_targets
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import CampaignParticipationGroup, Group

_MARKER = re.compile(r"\x02(\d+)\x03")
_USERNAME = re.compile(r"[A-Za-z0-9_]{1,32}\Z")


async def progress_group_links(session, campaign):
    rules = CampaignRules.model_validate(campaign.rules)
    targets = frozen_condition_targets(campaign.presentation, platform_policy=rules.platform_policy)
    positions = {str(item["id"]): index for index, item in enumerate(targets, 1)}
    rows = await session.execute(
        select(CampaignParticipationGroup, Group.chat_id)
        .join(Group, Group.id == CampaignParticipationGroup.group_id)
        .where(CampaignParticipationGroup.campaign_id == campaign.id)
    )
    links = {}
    for target, current_chat_id in rows:
        key = str(target.chat_id_snapshot)
        url = _group_url(target, current_chat_id)
        if url:
            links[key] = {"url": url}
        elif key in positions:
            links[key] = {"condition_position": positions[key]}
    return links


def _group_url(target, current_chat_id):
    return _public_group_url(target) or _published_group_url(target, current_chat_id)


def _public_group_url(target):
    if target.private:
        return None
    username = (target.username_snapshot or "").removeprefix("@")
    if _USERNAME.fullmatch(username):
        return f"https://t.me/{username}"
    candidate = target.public_join_url or ""
    prefix = "https://t.me/"
    if candidate.startswith(prefix) and _USERNAME.fullmatch(candidate.removeprefix(prefix)):
        return candidate
    return None


def _published_group_url(target, current_chat_id):
    chat_id = str(target.chat_id_snapshot)
    if (
        target.publication_state == "confirmed"
        and target.published_message_id
        and target.published_message_id > 0
        and current_chat_id == target.chat_id_snapshot
        and chat_id.startswith("-100")
        and chat_id[4:].isdecimal()
        and int(chat_id[4:]) > 0
    ):
        return f"https://t.me/c/{chat_id[4:]}/{target.published_message_id}"
    return None


class ProgressGroupLabels:
    def __init__(self, links):
        self._links = links
        self._labels = []

    def label(self, key, title):
        index = len(self._labels)
        self._labels.append((title, self._links.get(key)))
        return f"\x02{index}\x03"

    def resolve(self, template):
        parts, spans, cursor, offset = [], [], 0, 0
        for match in _MARKER.finditer(template):
            before = template[cursor : match.start()]
            title, link = self._labels[int(match.group(1))]
            offset += len(before)
            parts.extend((before, title))
            if link and title:
                spans.append({"offset": offset, "length": len(title), **link})
            offset += len(title)
            cursor = match.end()
        parts.append(template[cursor:])
        return "".join(parts), spans
