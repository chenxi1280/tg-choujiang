from tg_lottery.application.records import utcnow
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.creator_current_view import current_draft_view
from tg_lottery.telegram.creator_message_context import callback_message
from tg_lottery.telegram.creator_required_chats_data import (
    required_draft_data,
    restore_required_chats,
)
from tg_lottery.telegram.creator_reuse import reusable_v3_draft
from tg_lottery.telegram.draft_config import new_draft


class CreatorReuse:
    def __init__(self, *, sessions, profiles, renderer, bot):
        self._sessions = sessions
        self._profiles = profiles
        self._renderer = renderer
        self._bot = bot

    async def show(self, query, grant):
        await required_draft_data(self._sessions, query.from_user.id)
        last = await self._profiles.recent_last_draft(query.from_user.id, utcnow())
        if not last:
            raise UserFacingError("没有可载入的上次抽奖配置")
        await self._renderer.render(
            callback_message(query),
            user_id=query.from_user.id,
            text=f"确认复用上次配置？\n来源活动：{last.get('title') or '未命名活动'}\n\n"
            "将覆盖本场已填写的标题、奖品、群、条件和加成。"
            "本场草稿身份保留；旧活动和开奖时间不会复用，请重新设置本场开奖时间。",
            rows=[
                [("确认覆盖并复用", "draft_load_last_confirm")],
                [("保留当前草稿", "draft_back")],
            ],
        )

    async def confirm(self, query, grant):
        current = await required_draft_data(self._sessions, query.from_user.id)
        last = await self._profiles.recent_last_draft(query.from_user.id, utcnow())
        if not last:
            raise UserFacingError("上次配置已过期，本场草稿未覆盖")
        protected = {
            key: current[key]
            for key in ("draft_id", "draft_revision", "panel_message_id", "creator_name")
        }
        restored = await restore_required_chats(
            self._bot,
            reusable_v3_draft(_template_fields(last) | protected),
            user_id=query.from_user.id,
        )
        updated = {
            key: value
            for key, value in restored.items()
            if key
            not in {
                "published_campaign_id",
                "publish_stage",
                "publish_snapshot",
                "bonus_working_copy",
                "admission_editor",
                "pending_prize_name",
                "pending_prize_delete",
                "suspended_state",
                "content_pages",
                "content_rows",
            }
        }
        updated |= {
            "draw_at": "",
            "draw_mode": "fixed",
            "participant_target": None,
            "guided": False,
            "creator_path": ["dashboard"],
        }
        await self._sessions.set(query.from_user.id, "draft:idle", updated)
        text, rows = current_draft_view(updated)
        await self._renderer.render(
            callback_message(query), user_id=query.from_user.id, text=text, rows=rows
        )


def _template_fields(last):
    allowed = set(new_draft()) | {"issuer_user_id", "issuer_source", "issuer_username"}
    data = {key: value for key, value in last.items() if key in allowed}
    groups = data.get("participation_groups") or []
    clean = [
        {
            key: value
            for key, value in group.items()
            if key in {"id", "group_id", "title", "type", "timezone", "position"}
        }
        for group in groups
    ]
    return data | {"participation_groups": clean}
