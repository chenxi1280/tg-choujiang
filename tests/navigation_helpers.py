class PassthroughNavigation:
    """Routing test double: leave legacy route assertions to their tested handler."""

    async def handle_route(self, query, *, data, personal) -> bool:
        return False


class PassthroughAdmin:
    async def suspend_draft(self, user_id):
        return None
