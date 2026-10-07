from datetime import timedelta

from sqlalchemy import select

from tg_lottery.application.bot_relationships import relationship_observation
from tg_lottery.application.records import utcnow
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.infrastructure.db import QualificationCheck


async def require_current_platform_entry(session, campaign, participant):
    policy = campaign.rules.get("platform_policy")
    if not policy:
        return
    relation = await relationship_observation(
        session, policy["bot_id"], user_id=participant.user_id
    )
    check = await session.scalar(
        select(QualificationCheck)
        .where(
            QualificationCheck.participant_id == participant.id,
            QualificationCheck.phase == "registration",
        )
        .order_by(QualificationCheck.observed_at.desc(), QualificationCheck.id.desc())
        .limit(1)
    )
    if relation["state"] != "eligible":
        raise UserFacingError("请先从本场入口启动机器人、解除屏蔽并验证本人参与条件")
    if check is None or not _current_check(check, policy=policy, relation=relation):
        raise UserFacingError("本人平台条件需要重新验证, 请从本场详情验证后获取新邀请入口")


def _current_check(check, *, policy, relation):
    if check.observed_at < utcnow() - timedelta(seconds=policy["max_observation_age_seconds"]):
        return False
    if check.state.value != "eligible" or check.observations.get("bot_relation") != relation:
        return False
    passed = {
        item["chat_id"]
        for item in check.observations.get("chats", [])
        if item["state"] == "eligible"
    }
    return {item["chat_id"] for item in policy["channels"]} <= passed
