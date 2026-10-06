from sqlalchemy import func, select


async def campaign_page_position(session, statement, *, reference, ordering, page_size, requested):
    if reference is None:
        return requested, False
    ranked = (
        statement.add_columns(func.row_number().over(order_by=ordering).label("list_position"))
        .order_by(None)
        .subquery()
    )
    position = await session.scalar(
        select(ranked.c.list_position).where(ranked.c.public_ref == reference)
    )
    if position is None:
        return requested, True
    return (int(position) - 1) // page_size, False
