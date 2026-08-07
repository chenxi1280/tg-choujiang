# TG 群抽奖机器人 V1.3 开发验收清单

本清单以 `doc/TG群抽奖机器人PRD_V1.3_实现对齐版.docx` 为合同。`[x]` 表示已有当前代码与自动化验证证据；线上证据不能由本地测试或 Actions 替代。

## A. 产品与权限

- [x] 一个 Bot 可服务多个群；同一群同一时刻最多一个非终态活动。
- [x] 群主/授权运营管理员仅能管理授权群，发布、暂停、截止、开奖、取消均复核 Telegram 管理员身份。
- [x] 管理员通过 Bot 私聊按钮完成创建、预览、发布、查看、暂停、提前截止、开奖和领奖登记。
- [x] 成员通过活动深链启动私聊后报名、验证资格、查询票数、查看邀请记录。
- [x] callback 先直接 ACK；管理动作 token 短期、一次性并绑定 owner/chat/campaign/action，同一 Durable Update 重放除外。

## B. 活动和规则

- [x] Campaign 状态覆盖 draft、publishing、scheduled、running、paused、settling、ready_to_draw、drawing、drawn、archived、cancelled。
- [x] 时间存为 timestamptz，活动保存 timezone；默认 Asia/Shanghai，暂停不顺延截止。
- [x] 发布后冻结规则；影响票数的规则不可原地修改。
- [x] 支持奖品名称、数量、领取说明；按奖品顺序开奖，默认同人一期最多中奖一次。
- [x] 预热期允许报名和资格校验但不累计人气票；running 才产生新票。

## C. 报名、资格和活跃票

- [x] 报名要求私聊启动；Participant 与资格 checking/request_hash 先持久化，再调用 Telegram。
- [x] 可选频道关注校验在报名和开奖前各执行一次；无法校验为 pending/unknown，不默认通过。
- [x] 只统计已报名成员在 running 期间的新群消息。
- [x] 排除机器人、服务消息、转发消息、纯表情和去空白后少于 5 字符文本。
- [x] `(chat_id, message_id)` 防重；编辑消息不新增；5 分钟内相同规范化文本只计一次。
- [x] 默认自然日 10 条有效消息发 1 票，每日最多 1 票；只保存内容指纹，不保存正文。

## D. 邀请票

- [x] 为参与者创建本期专属、机器人自建、截止时过期的邀请链接。
- [x] join request 仅形成归因候选；chat_member 实际入群后才进入留存验证。
- [x] 已在群成员、Bot、无归因入群不发票；同一期 invitee 只归因首个邀请人。
- [x] 退出再入不换邀请人，连续留存从重入时间重新计算。
- [x] 默认连续留群 24 小时且在 cutoff 前完成；每个有效邀请默认发 1 票。
- [x] 截止/取消后数据库停止新归因，创建撤销链接 Outbox，远端撤销结果单独核验。

## E. 账本、结算和开奖

- [x] `ticket_ledger` 只追加/冲正，`business_key` 唯一；总票数只能聚合或重建缓存。
- [x] 截止先进入 settling；只处理 `event_at <= cutoff_at` 的已接收事件。
- [x] 默认 5 分钟排空期；Inbox 积压、邀请 unknown、资格复核未完成时禁止快照。
- [x] 生成唯一不可变 DrawSnapshot/Entries，保存 config revision、ledger watermark、weight 和 snapshot hash。
- [x] 开奖锁 Campaign 和唯一 DrawRun；同一事务写 DrawRound、Winner、通知 Outbox 和 drawn 状态。
- [x] 保存 algorithm_version、随机输入、抽取区间；候选少于奖品份数时标记未派出。
- [x] 中奖事实不因 Telegram 通知失败回滚；领奖状态可由管理员更新。

## F. 数据流与恢复

- [x] DF-01 自定义 TelegramPollingAdapter：批次 Inbox 与 `last_durable_update_id` 同事务提交后再推进 offset。
- [x] PostgreSQL runtime lease 保证同一 Bot token 只有一个 polling 进程。
- [x] InboxProcessor 按 update_id 消费 received/failed，业务异常保留并可重放。
- [x] ScheduledJob 使用租约和 `FOR UPDATE SKIP LOCKED`；该语义不用于账本、快照或开奖读取。
- [x] Outbox 在业务事务内创建；Bot API 调用前提交 gateway_started，调用后追加 DeliveryAttempt/远端事实。
- [x] confirmed 保存远端对象/message_id；明确限流可重试；权限/参数错误 pre_accept_failed；调用后超时进入 unknown_hold 且禁止盲重发。
- [x] Telegram CONFIRMED 先独立提交净化后的远端事实，再执行可重放投影；投影失败不重发，confirmed/unprojected 由 worker 重放并触发健康告警。
- [x] 权威事实分别落在 Inbox、Participant、Ledger、Invitation、Snapshot、Winner、DeliveryAttempt，并溯源到 Durable Update。

## G. 安全、运维与交付

- [x] BOT_TOKEN、DATABASE_URL 等只来自环境变量/Actions Secrets，不写入源码或日志。
- [x] 日志带 update_id/campaign_id/job_id/outbox_id，保留 Telegram 方法、错误码、送达确定性和 release 标识。
- [x] Alembic 迁移先于 app/worker 启动；迁移失败禁止切换 release；迁移前生成 PostgreSQL 备份。
- [x] 分别提供数据库、polling freshness/continuity、worker heartbeat 和未决事实健康检查。
- [x] PostgreSQL 16 集成测试覆盖迁移、唯一约束、并发领取、时区、重放、截止竞态、重复开奖和 unknown delivery。
- [x] 发布路径固定为 master -> release -> GitHub Actions -> 马来西亚服务器 releases/current；production Environment 只允许 release 分支。
- [x] 已证明 deployed SHA、迁移版本、app/worker 健康、Bot API 连通和独立 Production Health。
- [ ] 真实 Telegram 报名、计票、邀请、开奖、群公告和中奖私聊仍需目标测试群完成 E2E。
