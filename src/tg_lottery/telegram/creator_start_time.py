from datetime import datetime, timedelta

from tg_lottery.application.records import utcnow
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.draft_config import parse_draw_at


async def edit(flow, query):
    data = await flow._set_state(query, "draft:start")
    from tg_lottery.telegram.public_projection import format_public_time

    current = (
        format_public_time(data["start_at"], "Asia/Shanghai")
        if data.get("start_at")
        else "发布完成后立即开始"
    )
    await flow._render(
        query,
        data=data,
        text=f"⏰ 开始时间（北京时间）\n当前：{current}\n"
        "发送 YYYY-MM-DD HH:MM 设置预约开始；必须早于报名截止。",
        rows=[[("立即开始", "draft_start_now")], [("返回", "draft_back")]],
    )


async def immediate(flow, query):
    await flow._update(query, {"start_at": ""})


async def receive(flow, message, data):
    start = parse_draw_at(message.text or "", "Asia/Shanghai")
    if start <= utcnow():
        raise UserFacingError("预约开始时间必须晚于当前时间，请重新设置或选择立即开始")
    if data.get("draw_at"):
        cutoff = datetime.fromisoformat(data["draw_at"]) - timedelta(minutes=5)
        if start >= cutoff:
            raise UserFacingError("开始时间必须早于报名截止（开奖前5分钟）")
    from tg_lottery.telegram.creator_gate_schedule import validate_gate_time

    updated = data | {"start_at": start.isoformat()}
    validate_gate_time(updated)
    await flow._dashboard_from_message(message, updated)
