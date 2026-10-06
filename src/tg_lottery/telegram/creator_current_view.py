from tg_lottery.telegram.creator_sections import current_section, section_view
from tg_lottery.telegram.creator_views import dashboard_rows, preview_text


def current_draft_view(data: dict):
    section = current_section(data)
    if section == "dashboard":
        return preview_text(data), dashboard_rows(data)
    return section_view(data, section)
