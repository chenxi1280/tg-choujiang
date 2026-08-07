# V1.3 实现对齐审计

审计日期：2026-08-08

## 已通过

- Durable Inbox 在推进 polling offset 前提交，并以 `(bot_id, update_id)` 防重。
- 报名资格和邀请校验先落 `checking + request_hash`，Telegram 调用后再写明确或 UNKNOWN 结果。
- 票数以 append-only `ticket_ledger` 为唯一真相；缓存只允许按账本重建并记录审计。
- 截止、快照、开奖和通知分事务留痕；Winner 在构造 Outbox 幂等键前完成 flush。
- ScheduledJob 与 TelegramOutbox 使用 owner/state/lease fencing，迟到 Worker 无权覆盖接管后的状态。
- callback ACK 直接及时发送；活动卡片、邀请链接、开奖与私聊通知仍通过 Outbox 记录远端事实。
- 每次生产迁移前备份 PostgreSQL；健康检查区分服务健康、失败 Update/Job、UNKNOWN delivery 和 polling 连续性。
- 30 项 PostgreSQL 集成/并发/恢复测试、Ruff、代码度量、迁移往返及漂移检查通过。

## 尚未在线证明

- `BOT_TOKEN` 与目标测试群尚未提供；`BOT_ID` 可由 Token 严格解析，管理员身份由 Telegram 群成员事实同步并实时复核。
- 未推送 `release`，因此马来西亚服务器上的 deployed SHA、容器、迁移和 Bot API 连通性均为未证明。
- 未执行真实 Telegram 报名、发言计票、邀请归因、截止排空、开奖及群内/私聊送达闭环。

上述未证明项不得由本地测试、GitHub Actions、服务器 Docker 健康或数据库迁移结果替代。
