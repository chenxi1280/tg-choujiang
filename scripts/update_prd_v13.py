# ruff: noqa: E501, RUF001

from pathlib import Path

from docx import Document
from docx.document import Document as DocumentType
from docx.table import _Cell
from docx.text.paragraph import Paragraph

SOURCE = Path("doc/TG群抽奖机器人PRD_V1.2_数据流转设计版.docx")
TARGET = Path("doc/TG群抽奖机器人PRD_V1.3_实现对齐版.docx")

PARAGRAPH_REPLACEMENTS = {
    "每次 callback_query 都必须调用 answerCallbackQuery；后台处理较长时先确认按钮，再展示最终结果。": (
        "每次 callback_query 到达后直接尽快调用 answerCallbackQuery；该交互 ACK 不进入业务 "
        "Outbox，也不代表业务成功。发布、邀请链接与开奖通知等可审计副作用仍必须走 Outbox。"
    ),
    "生产至少具备 PostgreSQL 备份、Alembic 记录、app/worker heartbeat、失败 Job 与 UNKNOWN delivery 告警。": (
        "生产每次迁移前生成 PostgreSQL custom-format 备份并执行保留策略；同时保留 Alembic "
        "记录、app/worker heartbeat、失败 Job、失败 Update 与 UNKNOWN delivery 告警。"
    ),
    "回调按钮的 answerCallbackQuery 通过 outbox 发送；交互提示不替代数据库业务事务和后续远端完成事实。": (
        "回调按钮先直接发送 answerCallbackQuery 交互 ACK，避免排队超时；ACK 不替代数据库业务事务。"
        "群卡片、邀请链接、开奖结果和中奖私聊仍通过 Outbox 留存远端完成事实。"
    ),
    "worker 只用 FOR UPDATE SKIP LOCKED 领取 telegram_outbox/scheduled_jobs，并以数据库时钟、lease_owner、lease_until 和 attempt_no 控制并发。": (
        "worker 只用 FOR UPDATE SKIP LOCKED 领取 telegram_outbox/scheduled_jobs，并以数据库时钟、"
        "lease_owner、lease_until 和 attempt_no 控制并发；迟到 Worker 必须再次校验 owner/state，"
        "不得覆盖租约接管后的状态。"
    ),
    "调用前先提交 DeliveryAttempt.gateway_started_at 与 request_hash；调用后另起事务追加 confirmed/failed/unknown 结果。": (
        "调用前先在 Outbox 提交 gateway_started_at 与 request_hash；调用后先用独立事务持久化净化后的 "
        "DeliveryAttempt=confirmed/failed/unknown。CONFIRMED 再执行幂等业务投影；投影失败保持远端成功事实且禁止重发，"
        "由 confirmed/unprojected 队列重放并告警。邀请链接明文不得进入 DeliveryAttempt。"
    ),
    "系统完成有效性校验后，才把邀请奖励写入邀请人的票数账本。": (
        "系统完成有效性校验且被邀请人不是邀请人本人后，才把邀请奖励写入邀请人的票数账本；"
        "成员使用自己的链接离群重入不得获得邀请票。"
    ),
    "截止任务先把活动转为结算中，排空截止前 Update、完成邀请及频道复核；条件全部通过后才冻结候选人、资格、票数和规则版本，生成不可变快照与 snapshot_hash，并转为可开奖。": (
        "截止任务先把活动转为结算中，只排空路由到本活动或目标群的截止前 Update，再完成邀请及频道复核；"
        "其他群的失败 Update 不得阻断本活动。条件全部通过后才冻结候选人、资格、票数和规则版本，"
        "生成不可变快照与 snapshot_hash，并转为可开奖。"
    ),
    "机器人必须是目标群管理员，并具备发送消息、管理邀请链接及接收成员状态所需权限；发布前和关键操作前均重新检查权限。": (
        "机器人必须是目标群管理员，并具备发送消息、管理邀请链接及接收成员状态所需权限；"
        "chat_member/my_chat_member 事件同步管理员缓存和 Bot 撤权状态，发布、领奖登记等关键操作仍实时重新检查权限。"
    ),
    "long polling 的 allowed_updates 显式包含 message、callback_query、chat_member、my_chat_member、chat_join_request。chat_member 默认不会随空列表自动订阅。": (
        "long polling 的 allowed_updates 显式请求 aiogram 当前支持的全部 UpdateType，避免被过滤类型造成 update_id 假缺口；"
        "Inbox 同时保存 routing_chat_id/routing_campaign_id，用于按群和活动隔离排空与故障。"
    ),
}


def paragraphs(container: DocumentType | _Cell):
    yield from container.paragraphs
    for table in container.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from paragraphs(cell)


def replace_paragraph(paragraph: Paragraph, text: str) -> None:
    if not paragraph.runs:
        paragraph.add_run(text)
        return
    paragraph.runs[0].text = text
    for run in paragraph.runs[1:]:
        run.text = ""


def update_document() -> None:
    document = Document(SOURCE)
    containers = [document]
    for section in document.sections:
        containers.extend((section.header, section.footer))
    for container in containers:
        for paragraph in paragraphs(container):
            replacement = PARAGRAPH_REPLACEMENTS.get(paragraph.text)
            if replacement:
                replace_paragraph(paragraph, replacement)
            for run in paragraph.runs:
                run.text = run.text.replace("V1.2", "V1.3")
    document.save(TARGET)


if __name__ == "__main__":
    update_document()
