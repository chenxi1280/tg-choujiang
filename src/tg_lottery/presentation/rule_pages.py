"""Safe text segmentation and contextual public rule pages."""

import html

from tg_lottery.presentation.mentions import html_utf16_length


def escaped_text_blocks(text: str, *, units: int = 3000) -> tuple[str, ...]:
    chunks, current, width = [], [], 0
    for character in text:
        size = len(character.encode("utf-16-le")) // 2
        if width + size > units:
            chunks.append(html.escape("".join(current)))
            current, width = [], 0
        current.append(character)
        width += size
    if current:
        chunks.append(html.escape("".join(current)))
    return tuple(chunks)


def contextual_pages(pages, *, name: str, url: str):
    title = html.escape(name)
    link = html.escape(url, quote=True)
    result = tuple(
        f"📖 <b>{title}</b> · 完整规则 {index}/{len(pages)}\n\n{page}\n\n"
        f'<a href="{link}">返回本场活动</a>'
        for index, page in enumerate(pages, 1)
    )
    if any(html_utf16_length(page) > 4096 for page in result):
        raise ValueError("完整规则分页超过 Telegram 消息容量")
    return result
