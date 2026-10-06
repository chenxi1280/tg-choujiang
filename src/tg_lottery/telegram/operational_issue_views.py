from tg_lottery.telegram.publication_error_text import publication_error_text


def operational_issue_text(issues) -> str:
    if not issues:
        return ""
    lines = ["运行权限阻断（与人工暂停分别处理）："]
    for issue in issues:
        lines.append(f"{issue.title}：{publication_error_text(issue.details or issue.error_code)}")
    lines.append(
        "新报名与开奖等待原阻断解除；本人历史进度和奖励保留。"
        "请创建者修复对应群/频道权限并重新检测；权限恢复不解除人工暂停，也不顺延截止。"
    )
    return "\n".join(lines)
