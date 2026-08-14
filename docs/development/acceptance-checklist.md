# TG 群抽奖机器人分层开发验收清单（V1.9）

当前权威合同为 `docs/development/v1.9-lottery-bonus-prd.md`。A-G 节保留 V1.3 基线证据，H 节保留 V1.7 历史实现证据，I 节保留 V1.8 修复证据，J 节是当前 V1.9 发布门禁；A-I 的 `[x]` 不得覆盖 J 节未通过项。线上证据不能由本地测试或 Actions 替代。

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
- [x] 取消活动后吸收剩余生命周期 Job；已发送或延迟确认的活动卡片以 message_id 幂等编辑为“已取消”，移除报名入口。

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
- [x] 任意 update_id gap 均进入完整性复核；所有非 `ok` continuity 状态同时阻断 app 健康和结算快照。
- [x] InboxProcessor 严格按 update_id 消费；最早未完成项阻断后续项，FAILED 退避可重放，attempts fencing 拒绝旧 owner 覆盖。
- [x] ScheduledJob 使用租约、执行期续租和 `FOR UPDATE SKIP LOCKED`；该语义不用于账本、快照或开奖读取。
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
- [x] 生产私聊 `/start` 已从 Telegram Update 持久化、Inbox 自动重放到客户端收到“请选择操作”完成闭环；重建 Update 时统一绑定 Bot 实例。
- [ ] 真实 Telegram 报名、计票、邀请、开奖、群公告和中奖私聊仍需目标测试群完成 E2E。

## H. V1.7 LotteryBestBot 功能对标

> 历史状态说明：2026-08-10 第二轮审计确认，本节若干 `[x]` 只证明局部代码或自动化存在，不能证明真实安全回调、运行中多群复核、满人并发和 Telegram 客户端闭环。V1.8 起不再使用本节作为发布结论，当前修复门禁统一见 I 节。

- [x] `/start` 公开 `/index`、`/new`、`/join`、`/region`、`/myid` 与按钮入口。
- [x] `/index` 由 Campaign 权威状态统计累计、进行中、已开奖、已取消，并分别列出三类活动。
- [x] 管理首页使用当前 Bot 用户名生成添加到群组、添加到频道深链接，不使用占位 URL。
- [x] `/join` 显示活动标题、状态、资格、账本总票数与开奖时间；空列表明确返回无参抽记录。
- [x] `/region` 实现首次 5 分钟编辑窗口、30 天锁定、选择时间与旧值/新值审计。
- [x] 当前创建草稿与上次配置均执行 24 小时有效期；发布后保存受支持字段并清理当前草稿。
- [x] 创建预览与主按钮覆盖标题、封面、用户/地区限制、参与条件、奖品、说明、人品加成、开奖方式、发奖人员、消息提示。
- [x] 封面只接受 Telegram 图片 `file_id`；正式卡支持图片说明，取消时按卡片类型编辑正文或 caption。
- [x] 用户名与最多 3 个省级地区限制在报名持久化前校验；缺失地区明确引导 `/region`。
- [x] 额外参与条件支持公开用户名或数字 ID，发布前解析并验证 Bot 管理员权限，冻结 ID、标题、类型和用户名。
- [x] **历史 V1.7 实现（V1.8 新活动废止）**：每位合格报名者获得 1 张幂等基础票；关闭加成不发活跃/邀请票，标准/高活跃继续使用票账本加权。
- [x] 定时开奖和满人开奖共享排空、复核、快照和 `sha256-weighted-v2`；满人模式有 30 天兜底。
- [x] **历史 V1.7 实现（V1.8 语义纠正）**：消息提示开关仅控制扩展说明；V1.8 改为参与完成提示的延迟删除策略。
- [x] Campaign 冻结 presentation 与扩展规则；新增地区审计、报名票源及票源列宽迁移。
- [x] V1.7 全量 81 项自动化、Ruff、代码度量、Alembic 往返、模型漂移与 DOCX 40 页逐页视觉验收全部通过。
- [x] V1.7 生产 deployed SHA `bf27e9f256430cd560be430049b7543fb5298759`、迁移 `2a4e91b6f703 (head)` 与 app/worker running+healthy 已由独立 Production Health 回读。
- [ ] V1.7 容器 RestartCount 精确计数未出现在本次发布/健康日志；不得由容器健康状态推断。
- [ ] V1.7 Telegram 命令、全部创建按钮和“不发布”草稿 E2E 因当前客户端锁屏未证明；不得由本地测试代替。

## I. V1.8 LotteryBestBot 修复门禁

本节以 `docs/development/v1.8-lotterybest-repair-prd.md` 为权威合同。所有项初始为未证明；只有自动化、数据库与指定 Telegram E2E 层级均完成后才能勾选。

### I.1 P0 发布阻断

- [x] R-P0-01：创建动作使用单一注册表；全部 16 个缺失动作走通安全回调；旧页面不能修改新草稿。
- [x] R-P0-02：开奖前复核主群、legacy 频道和全部额外群；INELIGIBLE 优先 UNKNOWN；UNKNOWN 阻断快照。
- [x] R-P0-03：所有新草稿/新规则默认人品关闭，历史冻结活动不改写。
- [x] R-P0-04：每个必加群/频道均有可执行加入路径，失败页指出具体条件，私有链接不泄漏。
- [x] R-P0-05：满人门槛命中与唯一 Job 同事务；系统 actor 触发；第 30 天活动时区中午 12:00 兜底。
- [x] R-P0-06：发布绑定 draft revision 幂等；跨事务失败重试不重复创建 Campaign、任务或发布卡。
- [x] R-P0-07：报名收集全部资格观察后聚合，不在首个 UNKNOWN 处短路。

### I.2 P1 完整业务能力

- [x] R-P1-01：参与条件一次一个、重复追加，支持去重、删除单项、清空和显式上限。
- [x] R-P1-02：发奖人员按 ID/用户名解析并冻结，所有结果文案始终显示冻结事实。
- [x] R-P1-03：消息提示实现同步删除命令开关、指定延迟和不删除；必要消息不被抑制。
- [ ] R-P1-04：独立人品账本、日上限、查询/排行/规则、兑换票与管理员调整审计闭环。
- [x] R-P1-05：`/index` 分类、列表、活动详情和状态动作可点击，`/menu` 不维护第二套管理逻辑。
- [x] R-P1-06：查看规则零副作用，报名必须明确触发；两者使用不同深链和 handler。
- [ ] R-P1-07：客户等级、导航/精选门槛规则、申请、审核、投放状态与审计具备可配置能力。
- [x] R-P1-08：截止、开奖、取消后更新原发布卡并移除报名入口，旧深链服务端拒绝副作用。
- [x] R-P1-09：预览、管理页、个人列表、正式卡、规则页、结果和错误提示使用统一公开投影；状态/资格为穷尽中文映射，查询携带权威时区，公开时间有业务时区标签；新深链/callback 使用随机 Campaign 公开引用，旧 UUID 深链只读兼容且入箱解析回内部 Campaign 路由，邀请链接名称使用 durable 冻结随机标签；未开始 Gateway 的 legacy 邀请 Outbox 在领取锁内净化并重算哈希，已开始/UNKNOWN/CONFIRMED 记录不伪造远端改名；不回显异常原文、枚举、UUID、内部 ID、IANA 名称、Python 格式串、数据库术语或完整哈希；内部日志、快照与 Outbox 审计事实保持完整。
- [ ] R-P1-10：多奖品连续添加、顺位、删除、清空和发布冻结完整可用。

### I.3 P2 交互一致性

- [ ] R-P2-01：用户名/地区限制使用内联互斥和勾选，地区最多 3 个。
- [ ] R-P2-02：说明/封面可清空和返回，`/region` 提供关闭当前页面。
- [ ] R-P2-03：发布校验按字段反馈并提供修复入口，错误不丢草稿。
- [ ] R-P2-04：`/start` 指南图片、页面文案与线上 Bot 命令菜单同源并完成回读。

### I.4 质量与真实业务证据

- [x] Ruff、格式、代码度量、60 秒全量测试和 PostgreSQL 并发/故障回归通过。
- [x] Alembic 空库/生产形状升级、downgrade/upgrade、模型漂移和历史活动兼容通过。
- [ ] Telegram 私聊逐条命令和“不发布”草稿全部按钮、返回、错误与旧页面 E2E 通过。
- [ ] 受控测试群完成公私参与条件、报名、规则零副作用、退群复核、满人、人品、删除和卡片更新 E2E。
- [ ] 受控多奖品开奖核对 Ledger、资格观察、Snapshot、Winner、群结果和中奖私聊。
- [ ] 发布后 deployed SHA、迁移 head、app/worker、RestartCount 与 Telegram E2E 分层记录，未执行项保持 `unproven`。

## J. V1.9 创建预览与抽奖加成门禁

本节以 `docs/development/v1.9-lottery-bonus-prd.md` 为权威合同。

- [x] J-01：点击创建抽奖或发送 `/new` 后直接显示抽奖预览，不再强制先填写奖品；多活动群时可从预览选择活动群。
- [x] J-02：标题、封面、用户限制、参与条件、奖品、说明、抽奖加成、开奖方式和发奖人员均从同一预览面板进入设置。
- [x] J-03：参与条件允许一次添加一个或多个群聊/频道，不设固定数量上限；按解析后的 chat ID 去重并在发布前冻结可验证事实。
- [x] J-04：全部公开文案使用“抽奖加成”，不再把旧“人品加成”作为新活动配置名称。
- [x] J-05：邀请加成支持“累计有效邀请人数 -> 累计增加次数”阶梯和总上限；命中新阶梯只补发差额，重放不重复发放。
- [x] J-06：发言加成支持配置每日有效消息阈值和当日增加次数；按活动时区逐自然日独立结算，每日最多结算一次。
- [x] J-07：抽奖加成继续写入追加式 `ticket_ledger` 并增加抽奖权重，不允许直接编辑参与者总次数。
- [x] J-08：历史无 `bonus_types` 的冻结活动继续按旧 `luck_mode` 运行，新活动使用显式加成类型；不回填或重算历史账本。
- [x] J-09：定向与全量测试、Ruff、格式、代码度量、Compileall、Compose 和 `git diff --check` 通过。
- [ ] J-10：Telegram 真实客户端完成创建预览、混合群/频道条件、邀请阶梯和跨日发言 E2E；未执行前保持 `unproven`。
- [x] J-11：邀请留存核验覆盖主群和全部冻结额外条件并保存逐项证据；截止前退出任一必需群/频道会按阶梯冲正，首次失败后补条件或重新加入均重新计算完整留存期；结算只消费 `event_at <= cutoff_at` 的 Durable Update，不用截止后的当前状态反推历史状态。
- [x] J-12：邀请阶梯最后一档必须等于总上限；配置错误在创建阶段显式拒绝。
- [x] J-13：发言只统计主活动群；主活动载体为频道时发布前拒绝开启发言加成。
- [x] J-14：恢复上次抽奖时额外参与条件可见，添加、删除和清空同步更新展示、解析和冻结字段，不发布隐藏旧条件。
- [x] J-15：只有开启邀请加成才创建专属邀请链接并展示对应报名提示；只开发言或关闭加成不创建链接。
- [x] J-16：创建输入和冻结规则共同限制加成数值；零值、重复类型和超范围配置即时拒绝，发言加成按结算差额写一条账本而非逐次写行。
- [x] J-17：参与条件创建列表分页并支持按编号精确删除；正式卡使用单一条件入口，私聊逐项导航并区分“关注频道/加入群聊”。
- [x] J-18：参与条件列表的“添加参与条件”进入独立输入提示页，明确输入格式并可返回列表，不以无变化的重复渲染代替操作反馈。
