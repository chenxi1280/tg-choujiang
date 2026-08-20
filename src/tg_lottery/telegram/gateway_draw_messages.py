from typing import Any


def draw_commitment_text(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"🔐 「{payload['campaign_name']}」开奖随机承诺",
            f"承诺算法: {payload['algorithm_version']}",
            f"承诺哈希: {payload['commitment_hash']}",
            f"承诺时间: {payload['committed_at']}",
            "开奖时将公开随机种子，可按公告公式复核哈希。",
        ]
    )
