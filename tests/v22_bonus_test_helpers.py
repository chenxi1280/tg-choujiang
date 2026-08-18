from datetime import UTC, datetime
from types import SimpleNamespace

from aiogram.types import Chat, Message, User


def picker_query() -> object:
    message = Message(
        message_id=9,
        date=datetime(2026, 8, 19, tzinfo=UTC),
        chat=Chat(id=201, type="private"),
    )
    return SimpleNamespace(
        from_user=User(id=201, is_bot=False, first_name="Creator"),
        message=message,
    )


def draft_data() -> dict[str, object]:
    return {
        "chat_id": -1001,
        "timezone": "Asia/Shanghai",
        "participation_group_ids": [-1001, -1002],
        "participation_groups": [
            {"id": -1001, "title": "A群", "timezone": "Asia/Shanghai"},
            {"id": -1002, "title": "B群", "timezone": "Asia/Shanghai"},
        ],
        "bonus_types": [],
        "luck_mode": "off",
        "group_invite_rules": {},
        "group_activity_rules": {},
        "invite_preset": "standard",
        "invite_reward_mode": "ratio",
        "valid_invites_per_ticket": 2,
        "invite_bonus_cap": 3,
        "activity_message_threshold": 10,
        "activity_reward_tickets": 1,
    }
