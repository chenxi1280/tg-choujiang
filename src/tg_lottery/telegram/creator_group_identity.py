from tg_lottery.domain.errors import UserFacingError

MAP_FIELDS = frozenset(
    {
        "group_invite_rules",
        "group_activity_rules",
        "base_activity_rules",
        "chat_message_thresholds",
        "groups",
    }
)
ID_LIST_FIELDS = frozenset(
    {"participation_group_ids", "required_chat_ids", "copy_targets", "pending_group_removal_ids"}
)
ID_FIELDS = frozenset({"id", "chat_id", "bonus_group_chat_id"})


def remap_group_references(value, aliases: dict[str, str], *, field=""):
    if isinstance(value, dict):
        return {
            (
                aliases.get(str(key), str(key)) if field in MAP_FIELDS else key
            ): remap_group_references(item, aliases, field=str(key))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        if field in ID_LIST_FIELDS:
            return [_mapped_id(item, aliases) for item in value]
        return [remap_group_references(item, aliases) for item in value]
    return _mapped_field(value, aliases, field=field)


async def normalize_draft_groups(queries, data):
    aliases, identities = {}, {}
    for item in data.get("participation_groups") or []:
        group = await queries.group_by_chat_id(int(item["id"]))
        if group is None:
            raise UserFacingError(f"参与群「{item['title']}」身份未核实，请重新选择该群")
        if item.get("group_id") not in {None, "None", str(group.id)}:
            raise UserFacingError("参与群身份已变化，请重新核对，不能把旧规则套到另一群")
        aliases[str(item["id"])] = str(group.chat_id)
        identities[group.chat_id] = str(group.id)
    updated = remap_group_references(data, aliases)
    updated["participation_groups"] = [
        item | {"group_id": identities[int(item["id"])]}
        for item in updated.get("participation_groups") or []
    ]
    return updated


def filter_groups(groups, search: str):
    term = search.strip().casefold()
    return [
        group
        for group in groups
        if not term or term in group.title.casefold() or term in str(group.chat_id)
    ]


def _mapped_id(item, aliases):
    mapped = aliases.get(str(item), str(item))
    return int(mapped) if isinstance(item, int) else mapped


def _mapped_field(value, aliases, *, field):
    if field in ID_FIELDS and value is not None:
        return _mapped_id(value, aliases)
    return value
