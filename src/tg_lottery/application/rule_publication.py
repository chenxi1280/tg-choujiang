from uuid import UUID

from sqlalchemy import select

from tg_lottery.application.records import add_outbox
from tg_lottery.infrastructure.db import Campaign, TelegramOutbox
from tg_lottery.presentation.participation_rules import rule_manifest

MANIFEST_KEY = "publication_rule_manifest"


def prepare_rule_publication(
    session: object, *, campaign: object, chat_id: int, payload: dict
) -> tuple[dict, bool]:
    text, pages = rule_manifest(payload)
    payload = payload | {"rendered_card_text": text}
    remember_card_text(campaign, chat_id, text=text)
    if not pages:
        return payload, False
    manifests = dict(campaign.presentation.get(MANIFEST_KEY) or {})
    key = str(chat_id)
    if key in manifests:
        return payload, True
    manifests[key] = {"pages": list(pages), "card_payload": dict(payload), "remote_ids": {}}
    campaign.presentation = campaign.presentation | {MANIFEST_KEY: manifests}
    _queue_page(session, campaign, chat_id=chat_id, manifest=manifests[key], index=0)
    return payload, True


def remember_card_text(campaign, chat_id, *, text):
    texts = dict(campaign.presentation.get("publication_card_texts") or {}) | {str(chat_id): text}
    campaign.presentation = campaign.presentation | {"publication_card_texts": texts}


def _queue_page(session: object, campaign: object, *, chat_id: int, manifest: dict, index: int):
    text = f"完整规则 {index + 1}/{len(manifest['pages'])}\n" + manifest["pages"][index]
    add_outbox(
        session,
        operation="send_campaign_rules",
        aggregate_type="campaign",
        aggregate_id=str(campaign.id),
        recipient_chat_id=chat_id,
        business_key=f"publish_rules:{campaign.id}:{campaign.config_revision}:{chat_id}:{index}",
        payload={"campaign_id": str(campaign.id), "page_index": index, "text": text},
    )


async def project_rule_page(session: object, row: object) -> bool:
    if row.operation != "send_campaign_rules":
        return False
    campaign = await session.get(Campaign, UUID(row.aggregate_id), with_for_update=True)
    if campaign is None or row.remote_message_id is None:
        raise ValueError("rule publication requires a campaign and confirmed message id")
    manifests = dict(campaign.presentation[MANIFEST_KEY])
    chat_key = str(row.recipient_chat_id)
    manifest = dict(manifests[chat_key])
    index = int(row.payload["page_index"])
    remote_ids = dict(manifest["remote_ids"]) | {str(index): row.remote_message_id}
    manifest = manifest | {"remote_ids": remote_ids}
    manifests[chat_key] = manifest
    campaign.presentation = campaign.presentation | {MANIFEST_KEY: manifests}
    await _advance_publication(session, campaign, row=row, manifest=manifest, index=index)
    return True


async def _advance_publication(
    session: object, campaign: object, *, row: object, manifest: dict, index: int
):
    from tg_lottery.domain.enums import CampaignState

    if campaign.state != CampaignState.PUBLISHING:
        queue_rule_states(session, campaign, state=campaign.state.value)
        return
    if index + 1 < len(manifest["pages"]):
        key = (
            f"publish_rules:{campaign.id}:{campaign.config_revision}:"
            f"{row.recipient_chat_id}:{index + 1}"
        )
        if not await session.scalar(
            select(TelegramOutbox.id).where(TelegramOutbox.business_key == key)
        ):
            _queue_page(
                session, campaign, chat_id=row.recipient_chat_id, manifest=manifest, index=index + 1
            )
        return
    key = f"publish:{campaign.id}:{campaign.config_revision}:{row.recipient_chat_id}"
    if await session.scalar(select(TelegramOutbox.id).where(TelegramOutbox.business_key == key)):
        return
    payload = dict(manifest["card_payload"])
    chat_id = str(row.recipient_chat_id)
    first_id = manifest["remote_ids"]["0"]
    if chat_id.startswith("-100"):
        payload["rendered_card_text"] += (
            f'\n<a href="https://t.me/c/{chat_id[4:]}/{first_id}">本群完整规则</a>'
        )
    remember_card_text(campaign, row.recipient_chat_id, text=payload["rendered_card_text"])
    add_outbox(
        session,
        operation="send_campaign_card",
        aggregate_type="campaign",
        aggregate_id=str(campaign.id),
        recipient_chat_id=row.recipient_chat_id,
        business_key=key,
        payload=payload,
    )


def queue_rule_states(session: object, campaign: object, *, state: str) -> None:
    if campaign.rules.get("participation_rules_version") != "v3.0":
        return
    labels = {
        "cancelled": "已取消",
        "drawn": "已开奖",
        "settling": "已截止, 复核中",
        "ready_to_draw": "已截止, 待开奖",
        "drawing": "开奖中",
        "paused": "已暂停",
        "running": "进行中",
        "archived": "已归档",
    }
    emitted = set(campaign.presentation.get("rule_state_intents") or [])
    for chat_id, manifest in (campaign.presentation.get(MANIFEST_KEY) or {}).items():
        for index, message_id in manifest["remote_ids"].items():
            key = f"rules_state:{campaign.id}:{state}:{chat_id}:{index}"
            if key in emitted:
                continue
            emitted.add(key)
            add_outbox(
                session,
                operation="edit_campaign_rules",
                aggregate_type="campaign",
                aggregate_id=str(campaign.id),
                recipient_chat_id=int(chat_id),
                business_key=key,
                payload={
                    "message_id": message_id,
                    "text": f"当前状态: {labels[state]}\n" + manifest["pages"][int(index)],
                },
            )

    campaign.presentation = campaign.presentation | {"rule_state_intents": sorted(emitted)}
