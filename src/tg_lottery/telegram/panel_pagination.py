from tg_lottery.presentation.mentions import MESSAGE_UTF16_LIMIT

PAGE_CONTENT_UNITS = 3800


def text_pages(text: str, *, limit: int = MESSAGE_UTF16_LIMIT) -> tuple[str, ...]:
    pages, current, units = [], [], 0
    for char in text:
        size = len(char.encode("utf-16-le")) // 2
        if units + size > limit:
            pages.append("".join(current))
            current, units = [], 0
        current.append(char)
        units += size
    if current:
        pages.append("".join(current))
    return tuple(pages) or ("",)


def creator_page_rows(rows, *, page: int, total: int):
    navigation = []
    if page:
        navigation.append(("上一页", f"draft_content_page:{page - 1}"))
    if page + 1 < total:
        navigation.append(("下一页", f"draft_content_page:{page + 1}"))
    return [*rows, navigation] if navigation else rows


def titled_pages(text: str, *, title: str):
    pages = text_pages(text, limit=PAGE_CONTENT_UNITS)
    if len(pages) == 1:
        return pages
    return tuple(
        f"{title[:100]} · 第{i + 1}/{len(pages)}页\n\n{page}" for i, page in enumerate(pages)
    )
