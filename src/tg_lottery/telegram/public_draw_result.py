from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.public_refs import is_campaign_public_ref
from tg_lottery.presentation.draw_messages import draw_result_pages, draw_result_text
from tg_lottery.telegram.ui_style import style_keyboard


async def show_public_result(flow, message, reference: str) -> None:
    if not is_campaign_public_ref(reference):
        raise UserFacingError("结果编号无效，请从正式开奖通知打开")
    campaign = await flow._queries.campaign_by_reference(reference)
    if campaign is None:
        raise UserFacingError("找不到此编号对应的抽奖记录")
    outcome = await flow._queries.campaign_outcome(campaign.id)
    if outcome.kind != "drawn":
        text = (
            "本场活动已取消，没有完成的开奖结果。"
            if outcome.kind == "cancelled"
            else ("本场尚无完成的开奖记录，请以正式开奖通知为准。")
        )
        await message.answer(text)
        return
    rows = await flow._queries.winners(campaign.id)
    prizes = await flow._queries.prizes(campaign.id)
    payload = _result_payload(
        campaign, outcome, rows=rows, prizes=prizes, username=flow._bot_username
    )
    for page in draw_result_pages(payload):
        await message.answer(
            draw_result_text(page),
            parse_mode="HTML",
            reply_markup=style_keyboard(
                InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="查看本场活动", callback_data=f"m:detail:{reference}"
                            )
                        ]
                    ]
                )
            ),
        )


def _result_payload(campaign, outcome, *, rows, prizes, username):
    completed = outcome.completed_at.astimezone(ZoneInfo("Asia/Shanghai"))
    return {
        "campaign_name": campaign.name,
        "campaign_ref": campaign.public_ref,
        "bot_username": username,
        "completed_at_text": completed.strftime("%Y年%m月%d日 %H:%M:%S（北京时间）"),
        "winners": [_winner_payload(row) for row in rows],
        "undistributed": _unallocated(rows, prizes),
    }


def _winner_payload(row):
    return {
        "user_id": row.participant.user_id,
        "username": row.participant.username,
        "display_name": row.participant.display_name,
        "prize_name": row.prize.name,
        "prize_position": row.prize.position,
        "prize_slot_no": row.slot,
    }


def _unallocated(rows, prizes):
    missing = []
    for prize in prizes:
        count = prize.winner_count - sum(row.prize.id == prize.id for row in rows)
        if count > 0:
            missing.append(
                {"prize_position": prize.position, "prize_name": prize.name, "count": count}
            )
    return missing
