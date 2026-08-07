# V1.4 实现对齐审计

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

## 每期多奖品与随机开奖

- 每期独立配置一个或多个奖品，每个奖品使用明确的 `winner_count`；添加顺序即抽取和候选不足时的分配优先级。
- 发布时冻结 `prize_plan_hash`，结算快照再次保存；结算和开奖都校验当前奖品方案、Campaign 与 DrawSnapshot 三者一致。
- `sha256-weighted-v2` 使用确定性拒绝采样；每个奖品名额形成唯一 `prize_slot_no`，默认中奖者从全期后续槽位移除。
- 候选人数不足时不重复中奖补足，后续 DrawRound 标记 `undistributed` 并在群公告披露；中奖私聊携带本奖品领取说明。
- 新迁移 head 为 `a42b9c7d3e10`，42 项 PostgreSQL/并发/恢复测试、Ruff、代码度量、迁移往返与模型漂移检查通过。

## V1.4 全量代码复审修复

- Inbox 以最早未完成 Update 阻断后续项，FAILED 明确退避；完成/失败写入使用 attempts fencing，旧处理者不能覆盖接管后的结果。
- ScheduledJob 与 Outbox 在执行期间持续续租；Job 续租同步刷新 WorkerHeartbeat，长资格复核不会被误接管或误报 worker 失联。
- 活动消息和邀请归因统一使用 `[preheat_at, cutoff_at]`；Campaign 进入 SETTLING 后仍处理排空中的截止前事件。
- 延迟报名观察只在报名期且观察时间不晚于 cutoff 时更新 Participant；不会覆盖 draw 资格或在结算后创建邀请链接。
- Campaign、Invitation、Participant 相关路径统一 Campaign-first 锁序；发布时重新检查时间线，避免过期草稿进入生命周期任务。
- `member:verify` 使用实际点击用户和点击时刻，不再误用 Bot 消息发送者；选择群开始创建时实时复核 Telegram 管理员。
- 现有 Alembic head 无需变化；54 项测试、Ruff 和代码度量已通过，迁移往返及生产发布证据在本轮提交后重新核验。

## 当前线上证据

- `@TGCCJBOT` Token 已配置并通过 `getMe`；Bot ID 为 `8713586647`。
- 马来西亚生产已部署 `f43e16ee73ffcc58760e9f0079b280ef998807b4`，app/worker healthy、RestartCount=0，迁移 head 为 `a42b9c7d3e10`。
- Python Checks run `31214772183`、Deploy Production run `31214953849` 与独立 Production Health run `31215223592` 已通过。
- 线上 `POLL_TIMEOUT_SECONDS=30`、`POLL_LEASE_SECONDS=60`；UNKNOWN、confirmed/unprojected、失败 Job、失败/未处理 Update 计数均为 0。
- 目标测试群尚未加入，生产库 groups=0、campaigns=0；真实报名、发言计票、邀请归因、截止排空、多奖品开奖和 Telegram 送达闭环仍未证明。

上述未证明项不得由本地测试、GitHub Actions、服务器 Docker 健康或数据库迁移结果替代。
