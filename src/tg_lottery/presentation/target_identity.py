from collections import Counter


def duplicate_names(names):
    return {name for name, count in Counter(names).items() if count > 1}


def target_name(title, *, position, chat_id, duplicates):
    if title not in duplicates:
        return title
    return f"{position}. {title} · 群ID尾号{str(chat_id)[-6:]}"


def distinct_group_names(groups):
    duplicates = duplicate_names(str(group["title"]) for group in groups)
    return tuple(
        group
        | {
            "title": target_name(
                str(group["title"]),
                position=i,
                chat_id=group.get("display_chat_id") or group["id"],
                duplicates=duplicates,
            )
        }
        for i, group in enumerate(groups, 1)
    )
