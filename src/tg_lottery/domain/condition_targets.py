"""One stable order for public links and private condition pages."""


def frozen_condition_targets(presentation, *, platform_policy=None):
    participation = list(presentation.get("participation_groups") or [])
    main = presentation.get("main_chat")
    if not participation and isinstance(main, dict):
        participation.append(main)
    official = [item.record() for item in platform_policy.channels] if platform_policy else []
    unique = {}
    for item in [*official, *participation, *(presentation.get("required_chats") or [])]:
        if not isinstance(item, dict):
            continue
        key = int(item["id"])
        if key in unique:
            sources = list(
                dict.fromkeys([*unique[key].get("sources", []), *item.get("sources", [])])
            )
            unique[key] = unique[key] | item | {"sources": sources}
        else:
            unique[key] = dict(item)
    return tuple(unique.values())
