from tg_lottery.application.campaign_targets import uses_multi_group_contract
from tg_lottery.domain.errors import AuthorizationDenied
from tg_lottery.telegram.admin_authorization import verify_chat_admin


async def verify_campaign_admin(bot, queries, *, campaign_id, user_id: int, require_invites=False):
    campaign = await queries.campaign(campaign_id)
    if campaign is None:
        raise AuthorizationDenied("活动不存在，不能执行管理操作")
    targets = await queries.qualification_targets(campaign_id)
    participation = tuple(target for target in targets if target.kind == "participation_group")
    if not participation:
        await _verify_single(
            bot, queries, campaign=campaign, user_id=user_id, require_invites=require_invites
        )
        return
    failures = []
    for target in participation:
        try:
            await verify_chat_admin(bot, target.chat_id, user_id, require_invites=require_invites)
        except AuthorizationDenied as exc:
            failures.append(f"{target.title}：{exc}")
    if failures:
        raise AuthorizationDenied("全部参与群的当前管理员权限未通过：\n" + "\n".join(failures))


async def _verify_single(bot, queries, *, campaign, user_id: int, require_invites: bool):
    if uses_multi_group_contract(campaign):
        raise AuthorizationDenied("多群活动的参与群身份不完整，请核查活动记录")
    group = await queries.campaign_group(campaign.id)
    await verify_chat_admin(bot, group.chat_id, user_id, require_invites=require_invites)
