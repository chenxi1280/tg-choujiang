# TG 群抽奖机器人 V1.2 开发验收清单

本清单从 `doc/TG群抽奖机器人PRD_V1.2_数据流转设计版.docx` 提取，是代码、测试和生产验收的共同合同。

## A. 产品与权限

- [ ] 一个 Bot 可服务多个群；同一群同一时刻最多一个非终态活动。
- [ ] 群主/授权运营管理员仅能管理授权群，发布、暂停、截止、开奖、取消均复核 Telegram 管理员身份。
- [ ] 管理员全程通过 Bot 私聊按钮完成创建、预览、发布、查看、暂停、提前截止、开奖和领奖登记。
- [ ] 成员通过活动深链启动私聊后报名、验证资格、查询票数、查看邀请记录。
- [ ] callback 查询始终及时 answer；管理动作 token 短期、一次性、owner/chat/campaign/action 绑定。

## B. 活动和规则

- [ ] Campaign 状态完整覆盖 draft、publishing、scheduled、running、paused、settling、ready_to_draw、drawing、drawn、archived、cancelled。
- [ ] 时间存为 timestamptz，活动保存 timezone；默认 Asia/Shanghai，暂停不顺延截止。
- [ ] 发布后冻结规则；影响票数的规则不可原地修改，只能取消后复制新建。
- [ ] 支持奖品名称、数量、领取说明；按奖品顺序开奖，默认同人一期最多中奖一次。
- [ ] 预热期允许报名和资格校验但不累计人气票；running 才产生新票。

## C. 报名、资格和活跃票

- [ ] 报名要求私聊启动；同事务保存 Participant、规则版本及资格观察。
- [ ] 可选频道关注校验在报名和开奖前各执行一次；无法校验为 pending/unknown，不默认通过。
- [ ] 只统计已报名成员在 running 期间的新群消息。
- [ ] 排除机器人、服务消息、转发消息、纯表情和去空白后少于 5 字符文本。
- [ ] `(chat_id, message_id)` 防重；编辑消息不新增；5 分钟内相同规范化文本只计一次。
- [ ] 默认自然日 10 条有效消息发 1 票，每日最多 1 票；只保存内容指纹，不保存正文。

## D. 邀请票

- [ ] 为参与者创建本期专属、机器人自建、截止时过期的邀请链接。
- [ ] join request 仅形成归因候选；chat_member 实际入群后才进入留存验证。
- [ ] 已在群成员、Bot、无归因入群不发票；同一期 invitee 只归因首个邀请人。
- [ ] 退出再入不换邀请人，连续留存从重入时间重新计算。
- [ ] 默认连续留群 24 小时且在 cutoff 前完成；每个有效邀请发 N 票，默认 N=1。
- [ ] 截止/取消后数据库停止新归因，创建撤销链接 outbox，远端撤销结果单独核验。

## E. 账本、结算和开奖

- [ ] `ticket_ledger` 只追加/冲正，`business_key` 唯一；总票数只能聚合或重建缓存。
- [ ] 截止先进入 settling；只处理 `event_at <= cutoff_at` 的已接收事件。
- [ ] 默认 5 分钟排空期；Inbox 积压、邀请 unknown、资格复核未完成时禁止快照。
- [ ] 生成唯一不可变 DrawSnapshot/Entries，保存 config revision、ledger watermark、weight 和 snapshot hash。
- [ ] 开奖锁 Campaign 和唯一 DrawRun；同一事务写 DrawRound、Winner、通知 Outbox 和 drawn 状态。
- [ ] 保存 algorithm_version、随机输入、抽取区间；候选少于奖品份数时标记未派出。
- [ ] 中奖事实不因 Telegram 通知失败回滚；领奖状态可由管理员更新。

## F. 数据流与恢复

- [ ] DF-01 自定义 TelegramPollingAdapter：批次 Inbox 与 `last_durable_update_id` 同事务提交后再推进 offset。
- [ ] PostgreSQL runtime lease 保证同一 Bot token 只有一个 polling 进程。
- [ ] InboxProcessor 按 update_id 消费 received/failed，业务异常保留并可重放。
- [ ] ScheduledJob 使用租约和 `FOR UPDATE SKIP LOCKED`；该语义不得用于账本、快照或开奖读取。
- [ ] Outbox 在业务事务内创建；Bot API 调用前后分别提交 gateway_started 与 DeliveryAttempt/远端事实。
- [ ] confirmed 保存远端对象/message_id；明确限流可重试；权限/参数错误 pre_accept_failed；调用后超时进入 unknown_hold 且禁止盲重发。
- [ ] 权威事实分别落在 Inbox、Participant、Ledger、Invitation、Snapshot、Winner、DeliveryAttempt。

## G. 安全、运维与交付

- [ ] BOT_TOKEN、DATABASE_URL 等只来自环境变量/Actions secrets，不写入源码或日志。
- [ ] 日志带 update_id/campaign_id/job_id/outbox_id，保留 Telegram 方法、错误码和送达确定性。
- [ ] Alembic 迁移先于 app/worker 启动；迁移失败禁止切换 release。
- [ ] 分别提供数据库、polling freshness、worker heartbeat 健康检查；健康不等于业务履约。
- [ ] PostgreSQL 16 集成测试覆盖迁移、唯一约束、并发领取、时区、重放、截止竞态、重复开奖和 unknown delivery。
- [ ] 发布路径固定为本地 master -> release -> GitHub Actions -> 马来西亚服务器 releases/current。
- [ ] 最终分别证明 deployed SHA、迁移版本、app/worker 健康、Bot API 连通及真实 Telegram 报名/计票/邀请/开奖/通知事实。

