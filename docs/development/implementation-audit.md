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

## 发布后补充修复

- Telegram CONFIRMED 与业务投影拆成两个事务；远端成功事实不会再因投影异常回滚为 UNKNOWN，confirmed/unprojected 可重放且纳入健康检查。
- 创建邀请链接的远端响应在写 DeliveryAttempt 前移除明文 URL；投影所需临时 secret 和长期 InviteLink 均使用上下文绑定密文，投影成功后清理临时副本。
- Durable Inbox 请求当前全部 Update 类型并记录群/活动路由，避免过滤 Update 制造假 gap，也避免其他群失败 Update 阻断本活动结算。
- 拒绝邀请人使用自己的专属链接给自己发票；管理员升降级和 Bot 撤权会同步缓存；领奖登记点击时重新核验 Telegram 管理员。
- 新迁移 head 为 `0d7f5bb6c9a1`，40 项 PostgreSQL/并发/恢复测试、Ruff、代码度量和迁移往返通过。

## 当前线上证据

- `@TGCCJBOT` Token 已配置并通过 `getMe`；Bot ID 为 `8713586647`。
- 马来西亚生产基线 `a18b7c143cd8e653469ca98b9092c644da8ce11d` 已部署，app/worker healthy、RestartCount=0，原迁移 head 为 `992c1dd08da2`。
- Production Health run `31207282220` 已通过；本轮补充修复尚待发布后更新线上 SHA 与迁移 head。
- 目标测试群尚未加入，生产库 groups=0；真实报名、发言计票、邀请归因、截止排空、开奖和 Telegram 送达闭环仍未证明。

上述未证明项不得由本地测试、GitHub Actions、服务器 Docker 健康或数据库迁移结果替代。
