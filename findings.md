# 审查发现

## 开发启动现状（2026-08-07）

- 当前目录只有三份 PRD 和规划文件，不是 Git 仓库，没有 Python 包、测试、Docker、迁移或 Actions。
- GitHub CLI 已登录 `chenxi1280`，具备 `repo` 与 `workflow` 权限；尚需确认 `tg-choujiang` 远端仓库是否存在。
- 本机 SSH 配置确认马来西亚服务器别名为 `prod-malaysia-admin` / `codex_kl_server`，root 别名为 `prod-malaysia-root`；上线前需只读确认目标目录、Docker/PostgreSQL 资源和端口占用。
- 同类 Telegram 项目的生产合同为本地 `master -> release -> push release`，由 `.github/workflows/deploy-production.yml` 调用 `deploy/release.sh`，服务器使用 releases/current 软链并保留回滚版本。
- 生产应用代码不得直接 SSH 修改；Actions 成功只证明发布，仍需单独验证容器健康、数据库迁移、Bot API 连通性及真实 Telegram 抽奖事实。
- 马来西亚主机当前 Docker 26.1.3、Compose 2.27.0，根盘约 31GB 可用；`infra_default`、共享 PostgreSQL 和 sing-box 已运行，可将新 app/worker 接入共享网络。
- GitHub 尚不存在 `chenxi1280/tg-choujiang`；GitHub CLI 当前身份有创建私有仓库及配置 workflow 的权限。
- 新项目没有可用 BOT_TOKEN 证据；不得复用正在运行的 `tggrouprobot` token，否则会造成同 token 双 polling。该缺口只影响最终 Telegram E2E，不改变应用、迁移、Actions 和服务部署实现。
- 应用、迁移和测试已在本地真实 PostgreSQL 16 验证；17 项测试通过，Alembic 无漂移，代码度量和 Ruff 均通过。
- 生产工作流已固定 Python 3.12 + PostgreSQL 16，release 推送后先 checks，再构建带 commit SHA 的 GHCR 镜像，最后通过不可变发布脚本部署；任何阶段失败均不会切换 `current`。
- V1.2 实现复审发现两个高风险并发缺口：租约过期后的迟到 Worker 可覆盖新 owner 状态；Winner 尚未 flush 就构造通知会生成含 `None` 的远端幂等键。两项均已修复并有 PostgreSQL 回归测试。
- 报名资格与邀请留存原先均在 Telegram 调用后才落库，无法区分“尚未调用、调用中、结果未知”；V1.3 改为先持久化 checking/request_hash，再追加明确或 UNKNOWN 结果。
- Polling 仅记录最后 durable update 仍不足以判断 24 小时断档；现增加 update_id gap 与中断时长诊断，出现组合风险时健康检查明确失败并要求人工完整性复核。
- 生产部署用户需通过 `sudo -n docker` 操作容器；所有部署、备份和健康脚本已统一解析 Docker 命令，迁移前执行 custom-format pg_dump 并保留 14 份。
- GitHub 定时生产健康工作流已建立，但在首个真实 release 验收前由 `PRODUCTION_ENABLED=false` 显式关闭，防止空 Bot 配置产生误报警或错误上线。
- `BOT_ID` 与 Bot Token 的数字前缀是重复配置，且空值会在 Compose/Pydantic 两层提前阻断；现由 Token 严格派生并在 `getMe` 中校验实际 Bot ID，显式不一致仍直接失败。
- 当前授权事实来自 `my_chat_member` 同步的群管理员和敏感动作前的 Telegram 实时复核；`ADMIN_IDS` 未参与授权判断，因此不再把它误列为生产启动必需项。
- 首次 release 暴露 Actions SSH 用户传递缺口：workflow 的 SSH config 为 `admin`，但 `release.sh` 默认目标为 `root@production-server`，显式用户覆盖了 config；工作流必须把 `PRODUCTION_USER` 传给 `--user`。
- 生产发布最终在 `a18b7c143cd8e653469ca98b9092c644da8ce11d` 成功；app/worker 使用同一 SHA 镜像，均 healthy、RestartCount=0，current 指向 `20260807182605_a18b7c1`。
- 生产 Alembic 为 `992c1dd08da2 (head)`；PollingOffset 绑定 bot `8713586647`，最近轮询成功且完整性为 `ok`，无 gap；worker 心跳新鲜，失败 update 和 unknown outbox 均为 0。
- 发布后真实暴露并修复四个工程问题：Compose 在 sudo Docker 下丢失 `.image.env`、旧 GHCR PAT 权限不足、空 `ADMIN_IDS` 在 Pydantic JSON 预解析阶段失败、重启中容器会让发布健康等待拖满超时。
- 独立 Production Health workflow `31207282220` 已通过且 `PRODUCTION_ENABLED=true`；真实群数量仍为 0，因此群权限、活动发布、报名、邀请、开奖和领奖 E2E 尚未证实。

## 发布后实现复审（2026-08-08）

- 高风险：`OutboxWorker._confirm()` 将 CONFIRMED DeliveryAttempt、Outbox 确认状态和业务投影放在同一事务；投影异常会回滚已经获得的 Telegram 成功事实，租约到期后又会把它错误恢复为 UNKNOWN。
- 高风险：`create_invite_link` 的 `GatewayResult.remote_result` 当前保存 Telegram 完整对象，其中包含明文 `invite_link`；这违反邀请链接只允许密文持久化的安全合同。
- 修复方向：先独立提交已净化的远端成功事实和密文远端 secret，再以可重放的 `projected_at` 投影；worker 启动和每轮处理都重试 confirmed/unprojected，健康检查显式报告未完成投影。
- 文档漂移：README 仍指向 V1.2，implementation audit 仍声称 Token/发布缺失，部署文档仍要求已不再使用的自定义 GHCR PAT；需与当前 V1.3 和 workflow token 合同同步。
- 高风险：邀请归因未排除 `invitee_user_id == inviter participant.user_id`；参与者退出后使用自己的专属链接重入，可在留存期后给自己发邀请票。
- 多群隔离风险：Settlement 的 `inbox_pending_before_cutoff` 查询扫描 Bot 全部未决 Update；其他群或无关私聊的一条旧失败 Update 会阻断所有活动快照。需结合 Durable Update 的业务溯源设计活动级未决判断，不能简单忽略失败事实。
- 权限风险：`mark_claimed` callback 未绑定 campaign，点击时没有 Telegram 实时管理员复核；10 分钟 token 有效期内被撤权的管理员仍可能登记领奖。
- 权限投影漂移：`chat_member` 中其他用户的管理员升降级未同步 `group_admins`，Bot 自身失去管理员时也未停用缓存；新管理员无法进入管理列表，旧管理员持续显示，虽多数敏感动作会被实时 API 拦截。
- 连续性误报：Polling 只请求 5 类 Update，却把 `update_id` 缺口作为全量连续性证据；未请求的 edited/channel/reaction 等 Update 会形成正常编号跳跃，中断超过 24 小时后可被误报为 `integrity_review_required`。入口应请求并持久化 aiogram 当前全部 Update 类型，业务层再忽略无关类型。

## 原始文档

- 原文共 7 页、116 个段落、15 张表，无批注或脚注；产品主线覆盖活动、报名、活跃票、邀请票、冻结开奖和领奖。
- 原文渲染后所有 7 页中文大面积缺字，仅英文、数字和标点可见；根因方向是正文/表格未正确声明可用的中文字体，现状不能作为正式交付。
- 原文所有章节段落基本使用 `Normal` 样式手工呈现，标题层级不是标准 Heading 样式；后续目录、导航与维护性较差。
- 原文存在规则歧义：人气票“达标奖励 1、每日上限 2”与推荐默认值“每日最多 1 张”冲突；邀请奖励写“V1 先实现其中一种可配置模式”，但未固定 V1 的唯一计算语义。

## 同类项目约定

- 当前 `tg-choujiang` 目录未发现既有代码、Git 状态或项目级 `AGENTS.md`，需要在 PRD 中直接写清工程基线。
- 同类 TG 项目反复采用分层证据边界：数据库持久化、调度/执行记录、Telegram 远端事实分别验收；健康检查、本地测试或部署成功不等于业务完成。
- PostgreSQL 验证不得被普通单测替代；涉及任务状态与 Telegram 结果时必须保留可追溯事件和幂等键。
- Telegram 官方约束：群普通消息依赖管理员身份或关闭 Privacy Mode；`chat_member` 默认不在全部更新内，需显式订阅且机器人须为管理员；邀请链接必须由机器人自己创建才能完整管理和撤销。
- Telegram `update_id` 用于重复 Update 去重和乱序恢复；Webhook 应校验 `X-Telegram-Bot-Api-Secret-Token`，数据库侧仍需唯一约束保障幂等。
- `ChatMemberUpdated.invite_link` 可作为专属链接归因事实；`getChatMember` 对其他用户仅在机器人为管理员时保证有效。

## 待修订项

- 增加 Telegram 能力前置条件、Webhook/Update 幂等、私聊可达性限制与权限漂移处理。
- 固化活动时间语义、暂停/截止/取消后的计票行为、规则变更边界和群时区。
- 将票数账本改为不可变流水 + 唯一业务键，补充活动快照与开奖事务/随机算法审计。
- 细化邀请归因冲突、重复入群、退群后再入群、已有群成员点邀请链接、链接撤销失败等状态。
- 增加 Python + PostgreSQL 模块、表、队列/定时任务、部署与验收边界。
- 修复中文字体和 Heading 样式后重新渲染全部页面。

## 修订版决策

- 工程基线对齐 `tg-reporter`：Python 3.12、aiogram 3、SQLAlchemy 2 async、asyncpg、Alembic、Pydantic Settings、pytest/pytest-asyncio、Ruff、Docker。
- V1 采用单实例 long polling 接收 Telegram Update；延时校验、截止和开奖由独立 worker 使用 PostgreSQL `scheduled_jobs` 表承载，暂不引入 Redis/Celery/FastAPI。
- 分层保持 `bot -> application service -> repository/unit of work -> PostgreSQL`；Bot handler 不直接写数据库，外部通知通过 outbox/delivery attempt 留痕。
- 票数唯一真相为不可变 `ticket_ledger`；所有发票场景必须有唯一 `business_key`，参与者总票数只能作为可重建缓存。
- 抽奖采用截止快照、版本化算法、快照哈希和随机输入记录；V1 明确为可复核审计，不宣称第三方不可操纵的密码学开奖。
- 文档采用 `compact_reference_guide` 预设、`memo_masthead` 首屏结构，并将中文字体统一覆盖为 `Arial Unicode MS` 以修复渲染缺字。

## 最终验收

- 修订版共 19 页、20 张表；已逐页检查中文、标题、列表编号、跨页表头、页眉页脚与页面边界，无缺字、重叠或裁切。
- 所有 20 张表通过宽度、缩进、网格和单元格宽度一致性检查；多行数据表均设置表头行。
- 文档包含 21 个一级标题、29 个二级标题；原手工标题已转换为可导航的 Heading 样式。
- 原始版已原样归档，修订版另存为 V1.1，未覆盖用户提供的源文件。
- 用户确认由 Codex 修复后，进一步消除第 11 节“待确认”与第 20 节“已冻结”的内部冲突：第 11 节及其参数表统一改为 V1 已冻结口径。
- 为全部 132 个表格行增加禁止跨页拆分属性，核心数据模型中的 `draw_snapshots / entries / winners` 行不再在两页间断裂。

## V1.2 数据流转复审

- V1.1 已写到模块、表、幂等和验收原则，但仍缺少从 Telegram Update 到数据库 Inbox、业务事务、Ledger、Job、Outbox、DeliveryAttempt、远端结果的逐步数据路径。
- 现有“数据流转”主要描述顺序，尚未为每条流固定触发源、输入标识、同事务写入、唯一键、状态变化、异步任务、输出事实和失败恢复。
- 需要分别设计七条主流程：活动创建/发布、成员报名/资格、群消息/人气票、邀请归因/留存、截止/快照/开奖、通知送达、查询/对账修复。
- 需要补充四类状态机：Campaign、Invitation、ScheduledJob、Outbox/DeliveryAttempt，并明确 UNKNOWN 不能直接重发。
- 跨项目经验继续适用：入口先持久化、业务状态与远端送达解耦、账本只追加、写后异步送达、使用稳定业务键和数据库唯一约束，且数据库/任务/Telegram 事实分别验收。

### 官方边界校准

- Telegram Update 最长仅在服务器保留 24 小时；`update_id` 可用于去重和恢复顺序，因此 long polling 必须先把 Update 持久化到 Inbox，再推进 offset，不能把内存 handler 当唯一入口。
- `chat_member` 必须由管理员机器人显式订阅；`ChatMemberUpdated.invite_link` 仅在通过邀请链接加入的事件中出现。`ChatJoinRequest.invite_link` 可用于入群请求归因，但其临时 `user_chat_id` 仅有有限可用窗口，不能当长期私聊地址。
- 专属邀请链接需要机器人具备 `can_invite_users` 并由机器人创建/撤销；管理员手工创建的其他链接不能作为本活动的可靠归因来源。
- PostgreSQL `SKIP LOCKED` 会跳过已锁行并产生不一致视图，只适用于 `scheduled_jobs`/`outbox` 等队列表领取，禁止用于票数汇总、候选人冻结或开奖快照查询。
- V1 可在 Read Committed 下使用明确行锁和唯一约束处理单活动事务；冻结/开奖必须锁定确定的 Campaign 行。若后续选择 Repeatable Read/Serializable，必须实现整事务重试。
- 同一群仅允许一个非终态活动应使用部分唯一索引；发票、邀请归因、任务和 outbox 均应使用组合唯一约束或唯一业务键，而不是依赖应用层“先查再写”。

### 同类项目落地校准

- `tg-reporter` 的当前实现以 `AppContainer` 注入 async engine、session factory、UnitOfWork 和应用服务，handler 不创建具体数据库依赖；本项目可直接沿用这一依赖方向。
- `tg-yunying` 的数据流转索引采用“触发入口 -> 主要链路 -> 数据/状态 -> 外部依赖 -> 测试/验收入口”结构，适合将本项目每条主流程固定为开发合同。
- 对抽奖机器人应吸收但适度简化“远端事实先行、投影收敛”思路：数据库业务事务先产生 outbox；Telegram 调用后先追加 DeliveryAttempt/remote fact，再投影 outbox 状态。投影失败可重放，但不得重新发送已出现 confirmed 或 unknown 远端事实的同一 mutation。
- V1 不需要复制大型任务中心的完整 obligation/projector 体系；保留 Update Inbox、append-only Ledger、ScheduledJob、Outbox、DeliveryAttempt、DrawSnapshot 七个关键边界即可。
- `SKIP LOCKED` 只用于 worker 领取 ScheduledJob/Outbox；Campaign、Invitation、Ledger 和 DrawSnapshot 使用确定行锁、唯一约束与事务提交保证一致性。

### aiogram long polling 入口结论

- aiogram 3.30 的 `_listen_updates` 在 `yield update` 返回后把下一次 `getUpdates.offset` 设置为 `update_id + 1`；默认 `handle_as_tasks=True` 会立即拉取下一批，风险更大。
- 进一步检查 `_process_update` 后确认：即使 `handle_as_tasks=False`，dispatcher 也会捕获 handler/middleware 异常并返回，生成器随后仍推进 offset。因此“串行 polling + middleware”不能证明 Inbox 提交前失败时不会丢 Update。
- V1.2 改为自定义 `TelegramPollingAdapter`：使用 aiogram Bot API client 调用 `getUpdates`，将返回批次与 `polling_offsets.last_durable_update_id` 同事务提交后才发起带新 offset 的下一次请求；业务 Router/ApplicationService 从 Durable Inbox 顺序消费。
- 同一 Bot token 只能运行一个 long-polling 进程；启动时还需以 PostgreSQL runtime lease 防止部署期间双实例短暂重叠。扩容到多 bot-app 时再迁移 webhook，不在 V1 隐式切换。

### V1.2 视觉复核（第 1–10 页）

- 完整状态表已补为草稿、发布中、预热、进行中、已暂停、结算中、可开奖、开奖中、已开奖、已归档、已取消，表格未断行或裁切。
- 首屏最后一条评审 bullet 被整体挤到第 2 页页首，需继续压缩为单行口径，避免孤立列表项。
- 旧版正文仍有“截止立即冻结”“开奖时冻结”“群公告为准”等与 V1.2 冲突的描述；必须统一为结算排空后生成快照、数据库 Winner 为事实、Telegram 送达独立验收。
- 原 DrawSnapshot 行错误混入“随机种子/结果”，应改为候选人、冻结票数、规则版本、账本水位和快照哈希；随机输入属于 DrawRun/DrawRound。

### V1.2 视觉复核（第 11–20 页）

- 页面边界、中文字体、表头重复和新增章节分页正常；第 20 页端到端主链路完整可读。
- 第 17 节仍保留“业务事务提交后才推进 polling offset”的旧表述，与最外层 Durable Inbox 的入口合同冲突；需改为 Inbox 独立提交后处理，业务失败由 replay worker 重放。
- 第 17 页旧核心表把 Snapshot、随机输入和 Winner 混在一行；已安排按快照输入与开奖结果分层重写字段职责。

### V1.2 视觉复核（第 21–31 页）

- 8 条数据流、事实源、事务边界、幂等键、恢复矩阵和冻结参数均已落页，表格无溢出或拆行。
- DF 各节目前沿用同一 decimal numbering，导致 DF-02 从 7 开始、DF-08 延续到 41；应让每条 DF 独立从 1 开始，便于开发验收引用。
- `handle_as_tasks=False + DurableUpdateMiddleware` 仍无法阻止 aiogram 在业务异常后推进 offset，是本轮最关键设计缺口；入口必须改为自定义 PollingAdapter，在数据库批次提交成功后才使用新 offset。

### V1.2 最终 QA

- 第 1–10 页复核通过首屏、完整状态表、结算后冻结、开奖事实与链接撤销分态；仅第 4 页原邀请规则仍有一处“链接立即失效”的绝对化表述待替换。
- 第 11–20 页复核通过；自定义 PollingAdapter 已同步到技术选型、核心数据模型、并发入口合同、问题修订表和端到端总链路。
- 第 21–31 页数据流、独立步骤编号、事务/约束、恢复矩阵与冻结参数均通过视觉复核；第 32 页仅剩两行结语，属于需消除的孤立尾页。
- 最终修订后结语回收到第 31 页，第 4 页绝对化链接失效表述改为数据库停止归因、创建撤销 outbox、远端结果独立核验；无剩余视觉阻断项。

## V1.3 发布后复审修复

- Telegram CONFIRMED 必须先独立提交净化后的远端事实，再做可重放业务投影；投影失败不能把远端成功回滚为 UNKNOWN，也不能触发重复发送。
- 创建邀请链接的明文 URL 不得进入 `DeliveryAttempt.remote_result`；仅允许在 Outbox 临时密文中跨事务传递，投影到 InviteLink 密文后立即清理。
- Durable Inbox 请求全部 aiogram UpdateType，并保存群/活动路由；结算只排空本活动或目标群 Update，其他群失败不能跨域阻断。
- 自己使用自己的活动链接离群重入不得奖励；管理员升降级和 Bot 撤权同步缓存，领奖登记仍需实时 Telegram 管理员复核。
- V1.3 PRD 已同步上述合同，并使用临时 QA 字体完整渲染 31 页；逐页检查中文、表格、分页、页眉页脚，无裁切、重叠或孤立尾页。

## 每期奖品与随机中奖专项复审（2026-08-08）

- 现有创建流程已允许每期依次添加多个 Prize，并按每个 Prize.quantity 创建相应抽奖轮次；每张票作为权重，`unique_winner=true` 时同一人一期最多中一次。
- 核心缺口：开奖时读取 live `prizes`，而 DrawSnapshot/snapshot_hash 未包含奖品方案；发布后若奖品名、顺序或名额漂移，群卡片、快照与开奖结果可不一致。
- `quantity` 同时容易被理解为库存数量和中奖人数；本轮应明确重命名为 `winner_count`，并在 Bot 交互中使用“中奖人数/名额”口径。
- `claim_instructions` 已采集并持久化，但中奖私聊 payload 未携带，配置事实没有到达中奖人。
- 候选人数不足时会创建 undistributed DrawRound，但群公告只列中奖者，不显示哪个奖品剩余多少未派出名额。
- 当前 `sha256(seed:round) % total + 1` 存在可避免的取模偏差；应升级为确定性拒绝采样并版本化算法，继续保存 seed、轮次、命中值和权重区间以便复算。
- DrawRound 只有全局 round_number，缺少奖品内 `prize_slot_no`；应增加每奖品名额序号和唯一约束，直接审计“第几个名额”。
- 数据库 prizes 仅有应用层正数校验，缺少 winner_count/position CHECK 与 `(campaign_id, position)` 唯一约束；需要迁移补强。
- 最终合同：奖品添加顺序即分配优先级；默认全期不重复中奖。当总中奖人数超过合格候选人数时，不复制中奖者补足，后续槽位写 `undistributed=true` 并在群公告中披露。
- 奖品方案哈希覆盖 Prize ID、顺位、名称、中奖人数和领取说明；Campaign、DrawSnapshot 与开奖时当前方案必须一致，任何发布后漂移均明确阻断。
- 每个计划名额都生成 DrawRound，`(draw_run_id, prize_id, prize_slot_no)` 唯一；Winner 仅在实际抽中时生成，群公告与中奖私聊作为独立 Telegram 送达事实。

## V1.4 全量代码复审（2026-08-08）

- 基线：master `e4b88e9`，生产代码 `c05ff3f`，工作树在复审计划记录前干净；当前生产仍无 Group/Campaign，真实 Telegram E2E 未证明。
- 审查重点锁定 6 个高风险面：Campaign 状态/冻结、账本与资格、邀请归因、开奖可复算、Durable Inbox/Polling、Outbox 远端事实与投影恢复；同时复核迁移和部署脚本。
- 静态概览：生产 Python 文件均未超过 500 行，但 `telegram/outbox.py` 已达 483 行，需重点检查职责和异常分支；显式广义异常捕获集中在 Job/Inbox/Outbox/Polling 边界，必须逐处确认都有失败事实而非吞错。
- Outbox 复审：远端调用前持久化 `gateway_started_at`，明确拒绝可重试/终止，网络歧义进入 UNKNOWN_HOLD，confirmed 先独立提交再投影；当前未发现吞错或 confirmed 自动重发。投影并发由行锁和 `projected_at` 幂等保护。
- 报名/邀请复审：邀请事务锁定 Invitation 与 Campaign，first-attribution、self-invite、validation_version 和 TicketLedger business_key 边界完整；需要继续验证“延迟报名资格回写”与结算/快照并发，当前 complete_registration 只锁 QualificationCheck/Participant，未重新锁定 Campaign。
- 已确认 P1：`ActivityService.record_message` 仅检查“当前 Campaign=RUNNING”和 `event_at <= cutoff_at`，没有检查 `event_at >= preheat_at`。Durable Inbox 若在活动开始后重放一条预热开始前的旧消息，会被错误计入活跃并可能发票。
- 已确认 P1：ScheduledJob claim 固定租约 120 秒且执行期间不续租；`qualification_review` 会逐成员串行调用 Telegram，参与者较多时可超过租约。第二 Worker 可回收同一 Job 并重复外部读取/资格写入，原 Worker 最终还会因 fencing 丢失抛错。应在 handler 执行期间续租，而不是简单放大超时。
- 结算与活动计票都锁定 Campaign，截止与消息发票能串行；Settlement 还要求目标活动/群 Inbox 排空、邀请/资格完成和 polling freshness，当前未发现部分快照路径。
- 已确认 P1：Inbox `_claim_next` 只按最小“可领取” update 排序，不阻止另一进程在较小 update 已是 PROCESSING 时领取更大的 update。因此双 app/故障重叠可并发、乱序处理同一 Bot 的 Update，违反 Durable Inbox 顺序合同。
- 已确认 P2：FAILED Update 立即再次可领取，永久性异常会每 0.5 秒热循环重试；应使用明确重试间隔，同时保持最早未完成 Update 阻断后续项，使故障可见而不打爆数据库/日志。
- 已确认 P1 配置缺口：`poll_timeout_seconds` 可配置到 50、`poll_lease_seconds` 可低至 20，未约束租约覆盖 long polling。正常 getUpdates 阻塞即可让租约过期并触发双 Poller/事务回滚；需显式验证 lease 大于 timeout 并保留安全余量。
- 已确认 P1：延迟返回的报名资格会在 Campaign 已进入结算后覆盖 Participant 的 draw 资格；极端时可把 draw check 已判不合格的参与者重新改为 ELIGIBLE 并纳入快照。修复为 Campaign-first 锁序，报名观察事实仍落库，但只在报名期且观察时间不晚于 cutoff 时更新当前资格/创建邀请链接。
- 已确认 P1：截止 Job 先把 Campaign 改为 SETTLING 后，Activity/Invitation 只接受 RUNNING/SCHEDULED，导致 Inbox 排空期间 `event_at <= cutoff_at` 的消息和入群事件被静默丢弃，与 V1.4 排空合同相反。修复为 SETTLING 继续按 `[preheat_at, cutoff_at]` 事件时间接纳。
- 已确认 P1：邀请校验原先先锁 Invitation 再锁 Campaign，而结算先锁 Campaign 再锁 Invitation，存在循环等待。统一为 Campaign -> Invitation 锁序。
- 已确认 P1：`member:verify` callback 把 Bot 发送的旧消息作为注册消息，`message.from_user` 实际是 Bot，不是点击按钮的用户；会验证/报名错误用户。修复为显式传入 `query.from_user` 和点击时刻，并增加回归测试。
- 已确认 P2：草稿创建时虽校验时间线，但延迟发布不会重新检查 cutoff；现已在发布事务中拒绝过期 cutoff，并统一要求 draw 严格晚于 cutoff。Prize 服务边界同时补齐 trim、名称 120 字和领取说明非空校验。
- 已确认 P1：Inbox 过期领取没有 fencing，旧处理者可在新处理者完成后把 PROCESSED 覆盖为 FAILED；现以 attempts 作为 claim fence，所有完成/失败写入都校验 PROCESSING + claimed_attempt。
- 已确认 P2：Outbox 外部调用与 ScheduledJob handler 执行期间均未续租。Outbox 虽不会自动重发 gateway_started mutation，但会把仍在执行的调用误判 UNKNOWN；两类 worker 现统一使用可取消的租约心跳，Job 心跳同时刷新 WorkerHeartbeat。

## V1.4 第三轮独立代码复审（2026-08-08）

- 基线：master `36287b9` 仅比生产/release `f43e16e` 多一笔发布证据文档提交；工作树在本轮计划记录前干净，生产 groups=0/campaigns=0。
- 已确认 P1：Campaign 取消只新增撤销邀请链接 Job，没有终止或吸收既有生命周期 Job。`campaign_start`、`campaign_settle`、`campaign_draw` 在 CANCELLED 上会持续 JobRetry，形成永久重试噪声；qualification_review 仍会做无意义 Telegram 资格读取。
- 已确认 P1：活动卡片 Outbox 与取消状态存在竞态。若发送已开始或确认投影晚于取消，Campaign 已为 CANCELLED 时 `_project_campaign_card` 永久失败为 confirmed/unprojected；即使投影成功后再取消，群内活动卡片也不会移除报名按钮或显示已取消。需要以 confirmed remote_message_id 为依据创建幂等补偿编辑，而不是把远端成功变回失败。
- 开奖/快照复核：Campaign 行锁、唯一 DrawRun、同事务 DrawRound/Winner/Outbox、奖品方案哈希和 `sha256-weighted-v2` 的拒绝采样边界仍闭合；本轮未发现新的可导致重复开奖或不可复算结果的路径。
- 发布脚本复核：新 release 在 current 切换前完成备份、迁移、容器启动和健康检查；若新容器启动失败不会自动恢复旧镜像。自动回滚属于新增生产 fallback，当前规则要求另行明确授权，本轮不静默加入；发布失败仍会明确暴露并保留旧 current 指针。
- 已确认 P1：Polling 在短时间内发现 update_id gap 时只写 `gap_observed`，app health 仅拒绝 `integrity_review_required`，Settlement 更完全不检查 continuity 状态。因此另一个 poller 抢走/遗漏 Update 后，只要 polling 时间新鲜，仍可能生成不完整开奖快照。任何 gap 都必须进入人工完整性复核，且非 `ok` continuity 必须同时阻断健康与结算。
- 第三轮修复结果：CANCELLED 生命周期任务改为幂等成功吸收；已确认或延迟确认的活动卡片都会生成唯一 `edit_campaign_card_cancelled` 补偿 Outbox，保留 Telegram success 事实并移除报名入口。Polling 任意 gap 统一升级为 `integrity_review_required`，health 和 settlement 对所有非 `ok` 状态失败关闭。
- 第三轮发布事实：release run `31216494106` 的 checks、镜像构建和 deploy 全部成功；服务器 current、`.image.env` 与两个运行容器均回读为完整 SHA `ca4c116cef0b8eb1cb082a051d4afd94480f34c1`，容器 RestartCount 均为 0，独立健康 run `31216745366` 成功。
- 第三轮业务事实：数据库回读为 groups=0、campaigns=0、未完成 Update=0、非 ok PollingOffset=0、blocked/failed_terminal Job=0、UNKNOWN_HOLD=0、confirmed/unprojected=0。运行状态通过，但因没有生产群和活动，真实 Telegram 报名、计票、邀请、随机开奖、群公告及中奖私聊仍为 `unproven`，不得由健康检查替代。

## 生产 `/start` 事件（2026-08-08）

- Telegram 已在 09:20 将 `/start` 作为 update `162314859` 送达，Polling 也已提交 PostgreSQL；故障发生在 Inbox 业务处理，不是 Token、网络、Polling 或 Telegram 客户端发送问题。
- 根因是持久化 JSON 经 `Update.model_validate` 重建时没有 `context={"bot": bot}`。aiogram 的 Message/CallbackQuery 因 `_bot=None` 无法执行快捷回复方法，Update 进入 FAILED 并按 5 秒间隔重试。
- 修复发布后同一 Update 自动重放成功，服务日志记录 `durable update processed`，客户端显示机器人 09:28 回复“请选择操作”。这证明生产私聊入口和回复已经闭环；群内管理员授权、活动创建、报名、计票、邀请与开奖仍需独立 E2E。
- 后续新增群暴露第二个阻塞：Telegram 的事件顺序可能先登记新超级群 chat_id，再发送旧基础群迁移消息。若旧 chat_id 不属于本系统，迁移应是无业务对象的成功 no-op，不能成为永久 FAILED poison pill；已登记旧群迁移仍必须保留行锁与审计字段更新。
- 生产 `fd73101` 回放后，poison pill 866、三条 Bot 管理员状态 867–869、旧 callback 870 和 `/start` 871 全部 PROCESSED。旧 callback ACK 因超过 Telegram 时限被拒绝，但 handler 业务完成；这是历史延迟事实，不是当前队列失败。

## 极简创建交互复审（2026-08-08）

- 用户所指参考产品可由 Telegram 当前界面定位为 `@LotteryBestBot`。其 `/new` 不使用长串线性向导，而是维护一个可反复返回的活动配置面板；标题、参与条件、奖品、说明、开奖方式等均可独立进入与返回。
- 参考产品每次只要求一个简单输入：标题只输入标题；奖品先输入名称、再输入正整数数量；时间只输入一个本地开奖时间。它不会要求用户输入三段时间或用 `|` 拼接多个技术字段。
- 本项目当前创建流程依次要求活动名、三段时间、`奖品名 | 人数 | 说明`、六段规则，操作负担和纠错成本明显过高；预览还直接显示 UTC 时间。
- V1.5 决策：仅学习面板式创建体验，不复制参考产品开奖机制。保留现有活跃票、邀请票、不可变账本、结算排空、奖品方案哈希与 `sha256-weighted-v2` 加权随机开奖。
- V1.5 的用户必填项收敛为活动标题、开奖时间、至少一个奖品；领取说明为活动级可选配置。技术时间由系统派生：发布即开始，开奖前 5 分钟截止并排空，配置面板明确展示该规则。
- 规则不再通过六字段文本输入。默认使用 `CampaignRules` 已冻结参数；配置面板展示简明摘要，并提供独立的活跃票、邀请票和关注频道设置入口。
- 草稿先保存在 `AdminSession.data`，点击发布时统一校验并创建数据库 Campaign/Prize，避免未完成向导制造持久化半成品。所有面板回调继续使用一次性、用户和私聊绑定的安全 token。
- 实现复审确认旧回调 token 固定 10 分钟，若直接复用会导致 24 小时草稿尚在但按钮全部失效。现将 `CallbackTokenService` 支持显式期限，创建面板使用 24 小时；旧管理菜单继续使用默认 10 分钟，不扩大敏感操作窗口。
- 活动卡片的 cutoff/draw 展示已改为 Campaign 冻结时区下的 `YYYY-MM-DD HH:MM`，新增测试直接校验 Outbox payload，不再用 UTC ISO 字符串替代用户本地时间。
- V1.5 生产发布事实：`d37bd0e` 已经由 release 分支和 GitHub Actions 部署，生产镜像、current release、容器和健康检查均回读一致；这证明代码已上线，但不替代 Telegram 按钮点击事实。
- 11:58 的真实 `/start` Update `162314882` 已由生产 Inbox 处理成功。由于 `show_main()` 只有在 Telegram `message.answer()` 返回后 handler 才能完成并写 PROCESSED，私聊菜单的服务器端回发链路闭合；桌面客户端的创建按钮点击链因可访问性树超时仍为 `unproven`，未静默写成通过。
- 用户反馈证明 V1.5 的“单配置面板 + 多个独立技术设置入口”仍然偏向后台配置器，并未达到极简机器人真正顺手的创建体验；本轮不能把“必填项减少”当作交互已对齐，必须重新核对每一屏和每一次点击。

## 极简创建第二轮逐屏实测（2026-08-08）

- `/new` 是直接创建入口，不先展示“请选择操作→创建抽奖→选择群”三级导航。若已有未完成草稿，`/new` 直接回到草稿预览，不强制清空重建。
- 中央页面是“抽奖成品文案预览”，不是后台状态摘要：标题、封面、用户限制、地区限制、参与条件、奖品、抽奖说明、人品加成、开奖方式、发奖人均按最终消息形态展示；未设置项统一显示 `【等待设置】`。
- 预览下方使用紧凑三列业务按钮：活动标题、上传封面、用户限制、参与条件、奖品内容、抽奖说明、开奖方式、发奖人员、人品加成；次级操作是消息提示、发布抽奖、清空配置、载入上次抽奖、返回首页。
- 奖品编辑不是“进入奖品列表再点添加”。进入后立即询问奖品名称；输入名称后同一业务屏询问正整数份数；完成后自动进入下一个必填业务步骤。每屏都显示当前值并提供删除、清空、返回上一层。
- 参与条件屏直接说明公开/私有群频道的录入方式，允许一次一个、多次录入；底部只保留“组合分享链接”和“返回上一层”，没有把数据库/算法参数暴露给用户。
- V1.5 最大偏差不是字段数量，而是信息架构：仍需先选群，主面板展示“当前状态/系统规则/技术时间”，按钮名是“活跃票/邀请票/关注频道”，像运维配置器；参考产品以最终抽奖卡片为中心，所有按钮均是用户理解的业务内容。

## V1.7 LotteryBestBot 完整功能对标（2026-08-08）

### 文档渲染环境

- `render_docx.py` 会把 `HOME`、XDG 配置和 LibreOffice 用户目录切换到临时目录；系统 `fc-match` 对 `PingFang SC` 会退回 Hiragino，LibreOffice 输出仍出现中文方框。
- `/System/Library/Fonts/STHeiti Medium.ttc` 虽提供 `Heiti SC`，但 LibreOffice 仍不显示汉字；该尝试判定失败。
- `/Library/Fonts` 另有 `Arial Unicode.ttf` 与 `NotoSansCJKsc-Regular-Codex.otf`，下一轮改用 LibreOffice 更易识别的独立字体文件验证。
- `Arial Unicode MS` 在 canonical renderer 的临时 `HOME` 下同样不显示汉字；问题已定位为隔离 `HOME` 破坏 macOS 中文字体加载，而不是 DOCX 正文缺失。
- 文档仍需使用 LibreOffice 独立 profile，但保留真实用户 `HOME` 进行转换，再以 Poppler 生成逐页 PNG 完成视觉验收。
- 真实用户 `HOME` + 独立 LibreOffice profile 已成功显示中文；V1.7 文档可进入最终逐页视觉验收。canonical renderer 的临时 `HOME` 结果保留为已知工具限制，不作为文档失败结论。

### 当前代码落点

- `CampaignRules` 仅有单一 `required_channel_id`；V1.7 必须显式加入用户名、地区、多个参与群/频道、开奖模式和满人阈值。
- `Campaign` 尚无展示配置，`Participant` 尚无用户地区资料；新增 `Campaign.presentation` 与独立 `UserProfile`，避免把展示字段混入结算事实。
- 私聊更新目前只识别 `/start`、`/menu`、`/new`；`/index`、`/join`、`/region`、`/myid` 应由个人/首页 flow 承担，创建器继续只负责草稿。
- 创建器已有安全 callback token、AdminSession、草稿恢复和发布服务；V1.7 在这些边界内扩展，不重写结算、票账本、快照和抽奖算法。
- V1.7 数据结构采用 `UserProfile`（地区与上次配置）、`Campaign.presentation`（封面/说明/发奖人等展示事实）和扩展 `CampaignRules`（用户名、地区、参与群、开奖模式/阈值）三层分离。
- 满人开奖不创建第二套抽奖器：Campaign 先保存 30 天兜底时间，达到合格报名阈值后调用既有 `early_cutoff`，继续资格复核、排空、快照与 `sha256-weighted-v2`。
- 报名边界现在按规则先校验用户名和 `/region`，再逐个调用 Telegram `getChatMember` 校验所有必加群/频道；只有 `eligible` 人数达到满人阈值才触发提前截止。
- 发布卡片将 `Campaign.presentation` 投影到群消息；有封面时使用 `sendPhoto`，取消活动时对应使用 `editMessageCaption`，无封面维持原 `sendMessage/editMessageText`。

### 当前已确认的一级入口

- `/index`：抽奖管理主菜单。
- `/index` 实测显示客户等级（当前为“普通客户”）、累计创建次数，以及已开奖/未开奖/取消三类数量。
- 管理按钮为：发起抽奖活动、进行中的抽奖、已开奖的抽奖、已取消的抽奖、添加到群组、添加到频道；后两项是 Telegram 外链添加入口。
- `/new`：创建一个抽奖活动；存在未完成草稿时恢复同一草稿并显示成品预览。
- 从 `/index`、`/region` 等页面切换后再次发送 `/new`，原草稿与已填奖品仍完整恢复；草稿是用户级持久状态，不绑定当前消息页。
- `/join`：查看我参加的抽奖。
- `/join` 实测为只读查询；空状态直接回复“您未参与任何抽奖”。有记录时的列表样式仍需从已参加账号或正式参与后验证。
- `/region`：设置我的归属地区。
- `/region` 实测为省级归属地选择页，覆盖大陆省市自治区及香港、澳门、台湾，并提供“关闭当前页面”。
- 地区是参与者资料，不是每个抽奖的创建字段：部分抽奖可限制归属地用户参与。参考规则为选择后 5 分钟内可修改，之后需等待 30 日再次修改。
- `/myid`：查看我自己的用户 ID。
- `/myid` 实测为单消息只读回复，文案是“你的用户ID: <数字>”，不附加菜单或其他资料。

### 创建预览的完整字段

- 抽奖标题、封面设置、用户限制、地区限制、参与条件、奖品内容、抽奖说明、人品加成、开奖方式、发奖人员。
- 参考 Bot 默认值：用户限制“用户名可为空”、地区“全国”、人品加成“关闭”；未设置字段显示“等待设置”。
- 用户限制入口是二级设置页，目标是防止作假号或低价值用户参与；当前提供“用户名必须设置 / 用户名可以为空”两个互斥选项，当前项用勾选标识。
- 同一用户限制页还包含“地区限制”，默认全国；因此地区限制虽未单列在主面板，仍是每期抽奖配置，并与参与者 `/region` 资料配合校验。
- 每期地区限制支持最多选择 3 个省级地区；默认勾选“全国”。页面明确以参与者归属地为校验对象，省级选项与 `/region` 列表一致，并可返回上层。
- 预览文案明确提示“本页面为预览文案，不是正式效果”。
- 可见的三列按钮至少包括：活动标题 / 上传封面 / 用户限制，以及参与条件 / 奖品内容 / 抽奖说明；下方仍有地区、人品、开奖相关操作待继续实测。
- 完整按钮布局已确认：活动标题 / 上传封面 / 用户限制；参与条件 / 奖品内容 / 抽奖说明；开奖方式 / 发奖人员 / 人品加成；消息提示 / 发布抽奖；清空配置 / 载入上次抽奖；返回首页。
- 预览虽然展示“地区限制：全国”，但创建面板没有地区限制按钮；参考产品更可能通过 `/region` 的用户归属资料参与地区筛选，需通过地区命令和参与端继续验证。

### 已确认的创建起步流程

- `/new` 首先采集奖品名称，再采集正整数奖品份数；随后进入参与群/频道录入。
- 参与条件允许公开群/频道用户名或 ID、私有群/频道 ID；一次录入一个、可多次录入。
- 奖品名称提示建议控制在 10 个字以内；当前测试草稿为“测试奖品 × 2”，不发布。
- 开奖方式是必填项，当前页提供 `满人开奖` 与 `定时开奖` 两个入口，并可返回上一层。这里只对标配置交互；本项目的候选池、票数权重、不可变快照和多奖项分配仍沿用 `sha256-weighted-v2`，不照搬参考 Bot 的开奖内核。
- `满人开奖` 进入后立即要求输入期望参与人数；满足人数后立刻开奖。参考 Bot 另设 30 天兜底：30 天仍未满时，在到期日中午 12 点自动开奖。该兜底需在本项目中作为显式冻结配置实现，不得成为静默降级路径。
- `定时开奖` 进入后立即要求输入一个开奖日期，格式为本地 `年-月-日 时:分`，示例使用当前本地年份；没有要求用户填写预热、截止、排空等技术时间。本项目继续由所选开奖模式派生冻结后的开始/截止/结算计划。
- `上传封面` 是非必填项。页面显示当前封面状态，说明封面用于抽奖活动顶端、增强展示效果，并直接等待用户发送图片文件；草稿应保存 Telegram `file_id`，发布时使用同一媒体事实，不落本地临时文件。
- `抽奖说明` 是非必填项，页面显示当前文案（空值明确显示 `[ 空 ]`），随后只等待一次纯文本输入；该字段应支持独立设置、清空和返回，不与奖品领取说明混为一谈。
- `发奖人员` 是非必填项，默认使用抽奖活动创建者；页面允许输入 Telegram 用户 ID 或用户名。实现时需解析并冻结展示名/用户标识，发布后不能因创建者后续改名而改变历史卡片事实。

### 当前代码差距（master `59364aa`）

- 私聊命令仅有 `/start`、`/menu`、`/new`，缺少 `/index`、`/join`、`/region`、`/myid`；首页仍是创建、活动、票数、邀请四个通用入口。
- V1.6 预览只有活动群、标题、奖品、开奖时间、领奖说明和中奖规则；按钮仍是两行三列的技术配置，未覆盖参考 Bot 的封面、用户/地区限制、参与条件、抽奖说明、发奖人员、人品加成、消息提示、载入上次抽奖。
- 当前 `Campaign` 没有封面、活动说明、发奖人等冻结字段；`CampaignRules` 只支持单个 `required_channel_id`。参与者也没有独立的归属地区资料表。
- 当前创建器 `campaign_creator.py` 已 400 余行，新增功能必须拆分状态处理、菜单/查询和资料服务，不能继续把所有回调塞入同一文件。
- 当前生命周期只支持固定时间开奖。V1.7 需新增显式 `draw_mode`：定时模式沿用现有计划；满人模式由参与人数达到阈值触发提前截止/结算/开奖，并保存 30 天兜底时刻。两种模式最后都进入同一不可变快照和 `sha256-weighted-v2` 分配流程。
## V1.7 测试数据库定位

- `tests/conftest.py` 默认连接独立数据库 `lottery_test`，端口 55432，并强制连接串包含 `test`；夹具只做连通性检查与测试后 TRUNCATE，不负责建表或执行 Alembic。
- Alembic 从 `DATABASE_URL` 读取目标，因此回归前需将同一个 `TEST_DATABASE_URL` 显式映射为 `DATABASE_URL` 执行升级；不会触碰生产库。
- 当前工作树只有本次 V1.7 相关文件，另有 `uv` 首次执行产生的未跟踪 `uv.lock`，最终提交前需核对其是否应纳入项目。

## V1.7 干净迁移后的首轮回归

- 测试库已从 `a42b9c7d3e10` 成功升级到 `7c3f8d11a902 (head)`。
- 全量测试在约 24 秒内完成，数据库结构错误消失，剩余 6 个失败：4 个是新增接口/展示字段导致的旧断言更新，2 个是测试构造器缺少新增 `personal_flow` 依赖。
- `test_admin_promotion_and_bot_removal_update_cached_access` 可能受上一轮结构错误遗留数据影响；本轮各夹具 teardown 已恢复 TRUNCATE，需在干净库单独复测后再判断代码缺陷。
- 单独复测该管理员缓存用例已通过，确认不是产品代码缺陷。
- 功能核查发现三个尚未闭环点：`/index` 的“添加到群组”仍是占位 `https://t.me`；`message_tips` 已保存但开奖仍总是发送私聊中奖通知；`luck_mode=off` 目前仍沿用标准活跃/邀请票规则。
- 正式活动卡已支持封面与基础限制说明，但还需要显示满人/定时开奖方式和发奖人员，才能与创建预览一致。
- V1.7 合同明确主面板第三行为“人品加成 / 开奖方式 / 发奖人员”，第四行为“消息提示 / 发布抽奖”；当前代码顺序和分行尚未完全一致。
- 合同对“消息提示”的定义不是关闭必要通知，而是切换扩展说明；资格失败仍必须明确提示，因此不能用它静默抑制中奖/失败消息。
- `/index` 合同要求同时提供“添加到群组”和“添加到频道”，需根据当前 Bot 用户名生成真实 Telegram 深链接，而不是固定占位地址。
- “人品加成关闭”合同要求只保留报名基础票，但现有账本没有报名票来源，结算又会剔除权重为 0 的参与者；若仅停止活跃/邀请加票会导致无人可抽。V1.7 必须新增幂等报名基础票并为 `TicketSource` 增加明确的 `registration` 来源。
- `TicketSource` 使用非原生枚举检查约束，新增来源需要通过 Alembic 显式更新检查约束，不能只改 Python 枚举。
- 用户地区当前只有资料快照，没有旧值/新值审计表，未满足 V1.7 数据合同中的地区变更事实，需要和票源约束一起追加迁移。
- 增加 `registration` 后回归显式暴露了旧 `ticket_ledger.source_type` 为 `VARCHAR(10)`；检查约束虽已允许新值，但 12 字符仍无法落库。迁移还必须同步扩大列宽，并在 downgrade 遇到已存在报名票时明确拒绝，避免静默丢失账本事实。
- 更新处理器测试不能用裸 `object()` 代替 `personal_flow`，因为回调路由会先调用其 `handle_callback`；应使用返回 `False` 的协议型测试替身。
- 定向回归已通过，但功能清单复查发现 `/index` 仍只有一个合并的“进行中”入口，缺少已开奖/已取消分类；`/join` 也只显示标题和状态，未达到合同要求的资格、票数、开奖时间。
- 新增报名基础票后，“我的票数”也需要单列基础票，否则总数与活跃票+邀请票无法对账。
- 当前草稿的 24 小时有效期已由 `AdminSessionService` 实现；“上次抽奖配置”另存于用户资料，尚未检查 `last_draft_saved_at`，因此目前会永久可载入，需要补同样的 24 小时窗口。
- 额外参与条件当前只接收数字 ID，发布时也未逐项验证 Bot 能否调用成员查询，更没有冻结标题/类型；这不满足参考 Bot 的公开用户名输入和 V1.7 发布前可验证性合同。
- “消息提示”页面文案仍写成关闭通知，而实际合同是关闭扩展说明，需同步文案以免误导。
- 新增解析/冻结逻辑后主要生产文件仍低于 500 行：`campaign_creator.py` 446、`creator_settings.py` 374、`user_profiles.py` 125。
- 现有测试覆盖开奖通知载荷但没有 Telegram Gateway 的文案级测试；V1.7 需要至少验证消息扩展开关被写入群结果与中奖通知载荷，外部 Telegram 发送效果仍归 E2E。
- 最终代码度量首次发现 3 个硬指标超限：bootstrap 构造函数 54 行、PersonalFlow 回调分派嵌套 6 层、开奖通知排队函数 52 行。它们是可机械拆分的职责混合，不需要放宽指标。
- Alembic 往返已成功，但模型漂移检查发现票源列被扩为宽松 `VARCHAR(20)`，而非原生 Enum 的最大合法值长度是 12；应精确使用 `VARCHAR(12)`，让列宽、检查约束与模型枚举共同构成一致边界。
- V1.7 最终文档字段已与代码统一：地区锁定字段使用 `region_editable_until`/`region_locked_until`；额外参与条件在发布时冻结 Telegram 解析事实；消息提示关闭不抑制关键结果或错误。
- V1.7 生产发布事实：release run `31265194548` 成功，current 指向 `20260808154605_bf27e9f`，镜像为完整 SHA `bf27e9f256430cd560be430049b7543fb5298759`；独立健康 run `31265354364` 确认 app/worker running+healthy、Alembic `2a4e91b6f703 (head)`。
- 本次发布/健康日志没有输出容器 RestartCount 精确计数；Telegram 客户端锁屏也阻断了发布后命令和创建链路 E2E，因此二者只标记为未证明，不推断成功。

## 2026-08-09 第二轮差距审计与继续开发边界

- 当前审计基线为 master `b796108`、release `bf27e9f`，工作树在继续开发前干净；两分支相关 `src/` 产品代码一致。
- P0-1：创建面板生成的 16 个 V1.7 callback action 未进入 `CREATOR_ACTIONS`，`draft_cover` 已动态复现 `ValueError: unknown callback action`；开奖方式两个按钮同样不可达，新抽奖无法完成。
- P0-2：开奖前资格复核只观察 legacy `required_channel_id`，未复核主群和 `required_chat_ids`；报名后退群的原合格用户可能仍进入开奖快照。
- P0-3：参考 Bot 与合同默认“人品加成关闭”，当前新草稿/领域默认 `standard`，会静默发活跃票和邀请票并改变权重。
- 参考 Bot 的“消息提示”实测语义为“延迟删除参与完成提示”，包含同步删除口令、10/20/30 秒、1/5/10/30/60 分钟及不删除；当前合同和代码错误实现为“是否附加扩展说明”。
- 参考 Bot 的“人品加成”是独立积分体系：有效消息累积人品、每日上限、按不同参与群成本兑换额外抽奖票、票数上限、查询/排行/规则、管理员增减与审计；当前三档票预设只实现了近似能力。
- `/index` 应是可进入单活动的管理首页；当前分类仅输出文本，管理动作藏在未公开 `/menu`。发布卡“查看活动规则”与“立即报名”共用报名深链，点击规则有报名副作用。
- 允许保留的唯一核心差异是抽取内核：继续使用票账本、不可变快照、顺位多奖品和 `sha256-weighted-v2`；调度、资格、交互与可见业务能力不属于该豁免。
- Chronicle 于 2026-08-09T05:21Z 正常运行，但当前录屏 OCR 未命中此前参考 Bot 页面；本轮以刚完成的 Telegram 实机观察和已落盘逐屏事实为依据，不用过期画面补证。

## 2026-08-10 V1.8 修复合同结论

- 当前不能以 V1.7 的 81 项自动化和历史 `[x]` 宣称完整对标；V1.8 的 21 项需求全部从 `unproven` 起步，Telegram 客户端证据与本地/数据库/部署证据分层记录。
- P0 发布阻断为：动作注册表与旧页面隔离、多群开奖复核、默认关闭人品、可执行加入路径、满人触发与本地中午兜底、发布 saga 幂等、资格聚合不短路。
- P1/P2 覆盖参与条件累加、发奖人冻结、延迟删除、人品账本/兑换、`/index` 管理、规则零副作用、客户等级与推送、卡片状态、冻结投影、多奖品和创建交互一致性。
- 参考 Bot 已确认“人品按群消息累积并受日上限约束”，但计分长度、每条分值、日上限、兑换成本及票上限没有直接证据；这些值必须通过配置确认，不能把猜测写进产品规则。
- 修复 PRD 同时给出数据事实、迁移规则、事务边界、幂等键、失败恢复、安全隐私、验收矩阵和完成定义；后续实现不得用静默降级或健康检查替代业务完成事实。
- 旧 `off/standard/active` 只属于 legacy 票制；旧非终态活动按冻结规则运行到终态，新活动使用独立的人品规则 revision，不转换历史票、不回填历史积分，也不允许 legacy 活动接收 `human_redeem`。
- 验收清单的当前权威已统一为 V1.8：A-G 仅是 V1.3 基线历史、H 仅是 V1.7 历史实现证据、I 才是当前发布门禁，历史 `[x]` 不得覆盖 I 节未通过项。

## 2026-08-10 公开消息技术字段修复

- 用户提供的生产历史消息明确显示活动卡直接输出 `2026-08-08T03:20:00+00:00` / `2026-08-08T03:25:00+00:00`，开奖结果直接输出 64 位 `snapshot_hash`；这是公开展示投影问题，不是快照数据本身错误。
- 当前 CampaignService 已把新活动时间预先格式化为活动时区的分钟字符串，但 TelegramGateway 仍直接拼接载荷，无法清理已排队的旧 ISO 载荷；最终发送边界必须再次规范化。
- `DrawingService` 将完整快照哈希写入内部 Outbox，`TelegramGateway` 再把它拼入群消息。修复应只停止公开渲染，保留 `DrawSnapshot.snapshot_hash` 和内部 Outbox 载荷，避免破坏开奖可复算性。
- 现有测试只检查活动卡/开奖结果载荷，没有 TelegramGateway 文案级回归；需要覆盖新载荷、旧 ISO 载荷、活动时区转换和公开结果不含完整哈希。
- 最终实现按“内部事实不变、公开投影脱敏”分层：CampaignService 只提交 ISO instant 与冻结时区，TelegramGateway 负责本地化；DrawingService 仍提交完整 `snapshot_hash`，TelegramGateway 只输出业务结果和冻结依据说明。
- 公开结果不再暴露完整快照哈希不影响开奖可复算性；数据库 DrawSnapshot、DrawRun/DrawRound 与内部 Outbox 仍是审计事实源。
- 历史 ISO Outbox 不一定带 `timezone`；最终兼容路径必须从关联 Campaign 读取冻结时区。按配置默认值或 ISO 自带 UTC 直接展示都会产生静默时区错误，因此已改为精确读取持久化事实，缺失则显式失败。
- 已发送的 Telegram 历史消息不在本次修复范围内；本地回归无法证明生产已切换到新渲染逻辑，必须保持 `unproven`，不能由 86 项自动化或 DOCX 验收替代。

## 2026-08-10 公开投影统一审计边界

- “类似问题”按数据边界定义为：内部存储正确，但最终 Telegram 文案直接暴露机器格式或工程实现细节，包括 ISO/UTC、哈希、UUID、内部数据库 ID、原始枚举/状态和异常原文。
- 审计必须覆盖 Bot API 的 `send_message`、`send_photo` caption、`edit_message_text`、`edit_message_caption`、callback answer，以及由 Outbox 间接发送的所有操作；日志和内部审计字段不属于公开泄露。
- 允许保留用户理解或业务操作必需的 Telegram user/chat ID，但必须有明确中文标签和用途；随机输入、请求哈希、快照哈希、账本主键和内部业务键不得公开。
- 首轮确认问题 A：`admin_flow.py` 的活动列表、详情和状态变更回执直接展示 `CampaignState.value`，普通用户会看到 `running`、`paused`、`ready_to_draw` 等内部枚举值。
- 首轮确认问题 B：`personal_flow.py` 的参抽记录和创建记录直接展示 `CampaignState.value`、`QualificationState.value`，并用无时区标签的 `%Y-%m-%d %H:%M` 输出数据库 datetime；既有原始枚举，也存在时区语义不明确。
- 首轮确认问题 C：`update_handler.py` 把捕获的 `DomainError` / `ValueError` 原文直接拼成 `操作未完成: {exc}`；内部英文校验、callback action、UUID 或模型字段都可能通过这一通道泄露给用户。
- `member_flow.py` 的群资格观测把 `chat_id:member_status` 保存为内部证据，但当前公开文案只输出中文业务状态，没有直接泄露该内部观测串；这一路暂时安全。
- 创建流程的大部分文案已是中文业务语义；但 `draft_config.format_local()` 只输出 `YYYY-MM-DD HH:MM`，没有明确活动冻结时区，仍应并入统一公开时间格式。
- `draft_config.panel_text()` 会直接展示 `required_channel_id`；需确认调用方，再迁移到可读群组名称或删除失效路径，不能把原始 chat ID 当作默认公开文案。
- `creator_settings.py` 与若干回调流程存在英文内部异常（例如 callback message 不可访问）；当前全局异常处理会把这些原文回显给用户，属于同类技术字段泄露。
- 统一修复需要集中提供：活动状态中文映射、资格状态中文映射、按冻结时区格式化的公开时间，以及保留完整日志但不回显内部实现细节的公开错误投影。
- `ParticipatedCampaign` 查询 DTO 当前没有 `timezone`，因此个人参抽列表无法按活动冻结时区正确格式化；查询必须显式携带 `Campaign.timezone`，不能使用机器人默认时区替代。
- 创建活动列表直接返回 `Campaign`，已具备冻结 `timezone`，可复用同一公开时间格式器；后台活动列表/详情/状态回执也可复用同一状态映射。
- `campaign_creator._required_chat_record()` 把 Telegram API 异常原文拼进 `ValueError`；完整异常应留在异常链和日志，公开文案只说明哪个参与条件验证失败。
- `panel_text()` 的生产调用搜索仅命中其定义和测试，当前主创建流程使用 `preview_text()`；应在确认无生产引用后删除这条旧公开路径及其过时测试，避免继续维护会泄露 chat ID 的死代码。
- 当前 R-P1-09 已约束时间与快照哈希，但尚未明确覆盖公开状态/资格枚举和异常原文；本轮应把四类投影统一写入同一合同：时间、状态、标识、错误。
- 上一轮 DOCX 模板蒸馏产物仍在 `/private/tmp/tg-choujiang-v18-prd.if7mly/artifact.md`，V1.7 参考渲染与样式证据也完整存在；重建前需再次核对参考 SHA，再复用该模板合同。
- `AuthorizationDenied` 既承载中文可公开文案，也承载 callback token/管理员缓存等英文内部原因，因此不能按异常基类直接信任 `str(exc)`；需要单独的显式“可公开错误”类型。
- `InvalidStateTransition`、`SettlementBlocked` 等 DomainError 明确包含状态枚举、blocker key 和内部模型语义；全局边界必须记录完整异常栈，但对用户只输出稳定的业务提示。
- 地区锁定错误当前在应用层直接格式化数据库 datetime，既无活动/业务时区也无时区标签；应让异常携带锁定 instant，由 Telegram 公开投影使用机器人业务时区格式化。
- Gateway 已有可用的中文时间逻辑，但它是私有函数并与活动卡耦合；应提取为共享公开投影模块，供 Gateway、个人列表、创建预览和错误提示一致复用。
- 创建设置页面只显示参与条件数量，不回显用户输入的原始数字 ID；发布时冻结后的 `required_chats` 才进入正式卡，整体边界安全。
- 但 `_required_chat_record()` 在 Telegram 没有可读标题时回退为 `str(chat.id)`，该值随后可能进入正式卡；回退文案应改为“未命名群组/频道”，ID 仅保留在内部冻结事实。
- `/myid` 是用户主动查询自身 Telegram ID 的明确业务功能，可保留；callback_data、深链 payload 和内部 Outbox 标识不是消息正文，不按公开文案泄露处理。
- 现有测试没有覆盖 `PersonalFlow` 的参抽/创建列表文案，也没有覆盖 `AdminFlow` 的状态按钮、状态回执与详情文本；这正是原始枚举长期未被发现的直接测试缺口。
- `test_update_handler.py` 只验证路由，不验证异常公开投影与内部日志分离；需新增安全错误、内部 ValueError、授权失败和地区锁定时间四类边界回归。
- `test_draft_config.py` 仍为无生产调用的 `panel_text()` 保留断言；删除死路径时应同步删除该过时展示测试，保留真正使用的 `preview_text()` 回归。
- 独立复核确认 5 个追加问题：公开报名深链/验证按钮携带 Campaign UUID；Telegram 邀请链接名称截取 Participant UUID；未映射时区回显 IANA/UTC 技术标识；创建器展示 Python `strftime` 格式串；管理员开奖确认使用“数据库/独立送达”等实现术语。
- Campaign UUID 深链属于已发布协议，修复必须新增不可反推主键的公开引用，同时继续只读兼容已发送的 `join_<UUID>` 链接；新消息和新验证按钮不得再生成 UUID 协议。
- 邀请链接名称需要独立随机公开标签并持久化/冻结，不能仅在每次重试时重新随机，也不能改用另一张表的内部 UUID。
- Campaign 只需新增一个不可变唯一公开引用，Participant 无需新增公开字段；入箱时把公开引用解析回内部 Campaign ID，可保持 `TelegramUpdate.routing_campaign_id` 与结算排空语义不变。
- 新卡片的 Outbox 继续保留内部 `campaign_id` 用于投影，同时由 Gateway 请求构造器从 Campaign 权威记录注入公开引用；因此已排队的旧卡片也不会继续生成暴露 UUID 的新按钮。
- 发布前复审确认 3 个阻断点：Campaign 卡片新增的本地数据库投影发生在 `gateway_started_at` 之后，失败会伪造远端不确定；`public_ref` downgrade 会丢失已发布引用映射；自定义时间示例固定为 2026-08-10，过期后成为必然失败的输入。
- 修复边界必须保持远端事实语义：请求构造及本地校验在 Gateway 开始标记之前完成，仅 Bot API 调用进入“不确定结果”区间；迁移遇到任意 Campaign 时拒绝 downgrade；时间示例由活动冻结时区和可注入时钟计算。
- 邀请链接随机名称在创建 durable Outbox 意图时生成并冻结于 payload，重试复用同一值，成功投影再原样保存到 `InviteLink.telegram_name`；无需提前创建带占位秘密的 InviteLink。
- 合法但未配置中文标签的时区不能回显 `Europe/London` 或 `UTC`；应显示不含技术标识的“活动当地时间”，而权威 IANA 时区仍只保存在内部事实。
- 最终交叉审计确认 legacy `create_invite_link` Outbox 是结构性遗漏：新报名已使用随机名称，但升级前的 `PENDING`/`RETRY_SCHEDULED`/未开始 Gateway 的过期领取仍可能保存 `lot_<Participant UUID 前缀>`。修复必须在领取锁内一次生成并持久化中性名称、同步重算 `request_hash`，让后续重试复用同一事实。
- 已开始 Gateway 或进入 `UNKNOWN_HOLD` 的邀请意图不能净化名称，因为远端可能已经按旧名称创建成功；本地单边改名会制造事实分叉。当前实现明确保持原值并进入未知态，既有远端链接名称也不由迁移伪装成已修改。
- 同一交叉审计确认主群注册仍使用 `event.chat.title or str(event.chat.id)`，该 Group.title 会进入创建者选择和预览；无标题时必须统一保存“未命名群组/频道”，chat ID 仅留作内部 Telegram 路由键。
- Polling 需要分别回归 `member:verify:<public_ref>` 与 legacy `member:verify:<Campaign UUID>`，两者必须在持久化 Update 时解析为同一 `routing_campaign_id`，否则结算排空会漏掉截止前验证更新。

## 2026-08-14 创建预览与抽奖加成优化

- 用户要求点击“创建抽奖”后直接进入抽奖预览面板，不再先引导填写奖品；标题、封面、用户限制、参与条件等都从预览面板逐项配置。
- 参与条件应统一支持一个或多个群聊/频道，可混合配置；每项都必须在发布前解析并冻结 Telegram 可验证事实，不设置人为 2–3 个上限。
- “人品加成”统一更名为“抽奖加成”；抽奖加成等价于额外抽奖次数，继续以不可变票账本作为权威来源。
- 邀请加成需要可配置的累计邀请人数阶梯和总上限；示例语义为邀请 1 人获得 1 次、累计邀请 2 人获得 3 次，阶梯命中后只补发差额并保持幂等。
- 发言加成按活动时区逐自然日独立结算；当日有效消息达到阈值即增加配置次数，不要求连续天数，次日重新计数，未达标当日不奖励。
- 当前实现已经具备多参与群冻结、逐群资格核验、专属邀请链接、邀请留存校验、每日发言进度和票账本，但创建入口仍先进入奖品引导，公开名称仍为“人品加成”，邀请奖励仅支持固定每人票数与简单总上限。

## 2026-08-14 V1.9 漏洞修复口径

- 有效邀请不要求被邀请人完成报名，但在留存到期时必须仍属于主群和全部冻结额外群/频道；任一不合格不发加成，远端未知重试。
- 邀请生效后，被邀请人在报名截止前退出任一必需群/频道即冲正对应邀请阶梯权重；重新加入后保留首次归因，但重新计算完整留存期和全部条件，避免邀请刷入后立即退出。
- 仅依赖实时退群事件仍可能受 Bot 短暂离线或 Update 延迟影响；截止资格复核必须再次检查所有有效邀请，发现不合格即按截止时刻冲正，远端未知则阻断开奖重试。
- 发言加成只统计活动主群。主活动载体为频道时发布前拒绝开启发言加成，避免配置出用户无法完成的规则。
- 邀请配置采用完全显式的累计阶梯；最后一档累计次数必须等于总上限，不再存在独立但永远无法达到的上限。
- “不限制一到三个”解释为不把条件固定为 2 或 3 个；操作层仍需分页/紧凑展示与分批核验，不能无限堆叠 Telegram 按钮。本期先补指定编号删除与类型文案，发布卡使用统一条件入口以避免按钮爆炸。
# 2026-08-14 V1.9 发布前第二轮修复

- 恢复上次草稿时 `required_chats` 未持久化，且删除/清空条件未同步清除旧 `required_chat_ids`，形成界面不可见但发布生效的旧条件。
- 报名完成无条件创建并承诺发送邀请链接，没有服从冻结规则中的 `invite_bonus_enabled`。
- 截止复核读取执行时的当前成员状态，却把失败事实写成 `cutoff_at`，会错误反推历史截止状态。
- 首次留存验证失败不写 `left_at`，而补条件后的重新验证依赖 `left_at`，正常补关注路径无法恢复。
- 邀请多群验证只保存聚合状态，未持久化逐条件观察证据；需要新增 JSONB 审计字段和迁移。
- 旧 `luck_mode=standard` 规则缺少邀请阶梯/上限，当前规则页会显示空阶梯和 `None`。
- 创建输入接受零值与重复加成子句，错误延迟到发布阶段；领域数值也没有安全最大范围。
- 发言奖励按每张机会逐条写 `TicketLedger`；没有配置上限时，极端输入会放大为超大事务。范围必须在创建输入和领域模型双重校验，不能静默截断。

## 2026-08-14 V1.9 生产复核与客户端阻塞

- 当前重新触发的 Production Health `31736421816` 精确检出 `7264e9b766afa1c6b482c84bbf3a7a99986b450f`，app/worker 均为 `running healthy`，Alembic 为 `b91e4d7a6c20 (head)`，检查脚本返回 `STATUS=HEALTHY`。
- `STATUS=HEALTHY` 的代码定义为四类生产异常计数同时为 0：`unknown_hold` Outbox、已确认未投影 Outbox、阻断/终止失败 Job、失败 Telegram Update；它不能替代按钮点击证据。
- 本机生产 SSH 仍在 banner 交换阶段超时，但同一时刻 GitHub Actions 生产网络路径成功执行服务与数据库检查；因此这是本地访问路径问题，不是生产故障事实。
- 原生 Telegram 客户端仍被 macOS 锁屏阻断；in-app Browser 和 Chrome 的 Telegram Web 均停留在二维码登录页，没有可复用的授权会话。不得输入验证码、伪造 callback 或以自动化测试冒充真实点击。
- Mac 解锁后已定位生产 Bot `@TGCCJBOT`；新的 `/new` 直接进入抽奖预览，预览与设置页均使用“抽奖加成”，非法 `发言 0:0` 返回明确范围错误，合法邀请阶梯加每日发言规则会回写预览。
- 真实点击发现参与条件页“继续添加”按钮没有可见状态变化：该 action 被映射回 `_edit_participation`，而页面本身已处于 `draft:participation` 输入状态，因此点击前后文字和按钮完全相同。应拆分为明确的添加输入页，不能依赖用户猜测当前可直接回复。
- 根因修复把按钮改为“添加参与条件”，并把 callback 映射到独立输入提示页；提示页明确公开用户名/数字 ID 与逗号、空格、换行分隔方式，同时提供“返回条件列表”。
- 验收清单原有两个 `J-14` 编号会导致问题追踪歧义，已将分页/正式卡入口条目更正为 `J-17`，并新增 `J-18` 固化明确输入反馈合同。
- 生产修复版本 `72a778938872f5425875a1e692753a5a71f9296a` 上，重新发送 `/new` 后的当前消息显示新预览；进入参与条件页可见“添加参与条件”，点击后正文明确公开用户名/数字 ID 和三种分隔方式，“返回条件列表”可恢复列表页。
- 本轮只验证创建、打开、返回和错误反馈，没有添加不受控群/频道，也没有发布测试抽奖；因此不会把混合条件、实际邀请结算或跨日发言结算误报为本轮客户端已完成。

## 2026-08-14 第三轮产品反馈

- 当前参与条件输入把公开用户名和数字 ID 作为主文案，要求创建者理解 Telegram 内部 chat ID，操作成本过高；公开群/频道应直接接受 `t.me/<username>` 链接，数字 ID 只能作为兼容/高级路径。
- 当前抽奖加成依赖自由文本 DSL，虽然可表达复杂规则，但不符合 Telegram Bot 的可发现性操作；创建者应通过内联键盘完成启用、关闭和参数选择，预览仍展示最终规则摘要。
- 个人邀请链接必须以 `campaign_id + participant_id` 唯一，不能跨活动复用；旧活动链接在活动时间结束后仍可作为普通 Telegram 入群链接存在，但任何到达旧链接的加入事件不得继续发放旧活动加成，也不得归因到新活动。

### V1.9 第三轮冻结合同

- 创建者添加参与条件时，主入口接受 `https://t.me/<username>`、`t.me/<username>`、`@username`；Telegram 私有消息链接 `https://t.me/c/<内部群号>/<消息号>`可换算为群聊 chat ID。`t.me/+...` 邀请哈希无法由 Bot API 反查群信息，必须明确提示转发来源可见的群/频道消息，数字 chat ID 只作为高级方式，不能再称“用户 ID”。
- 抽奖加成不再要求创建者记忆文本语法。入口、邀请阶梯、发言门槛和每日奖励全部通过内联键盘修改；邀请最后一档的累计奖励就是总上限。
- 报名成功面板增加带活动 `public_ref` 的“获取本期邀请链接”；链接查找必须同时校验 campaign、participant 和 user，避免串活动或越权查看。
- 个人链接按 `(campaign_id, participant_id)` 唯一。正常截止/开奖后不撤销 Telegram 链接，仅停止归因；取消活动仍撤销。旧链接在截止后带来的入群事件不计旧活动，也不能计入后来活动；后来活动必须使用新活动生成的新链接。

## 2026-08-14 参与条件真实失败复核

- 生产 Bot 已恢复即时消费：公开频道链接和私有邀请链接都立即返回业务错误，因此当前问题不在轮询或部署健康边界。
- 公开 `t.me/<username>` 已通过输入解析，随后 `getChat` 或 `getChatMember` 抛出 Telegram API 异常；当前实现把两种失败合并成“无法验证其中一个参与条件”，丢失目标和可操作原因。
- `t.me/+<hash>` 是 Bot API 无法直接作为 `getChat` 标识使用的私有邀请链接；需要增加来源可见的转发消息识别，同时继续对仅有邀请 hash 的输入明确拒绝。
- 主活动群始终是冻结参与条件，创建者再次输入同一群时不应报笼统重复错误；应明确提示“主活动群已自动包含，无需重复添加”。
- Telegram 消息的链接预览正文不是 `message.text` 的额外输入；本次公开频道失败不是预览中的其他 `@username` 被误解析。
- 当前 aiogram `Message` 同时保留 `forward_origin` 与兼容字段 `forward_from_chat`；来源为频道或代表群/频道发送时可取得权威 `Chat.id`，来源隐藏或仅转发普通用户身份时不能推断原群。
- `CreatorSettings._receive_participation` 当前只读取 `message.text`，因此转发媒体、频道帖子或无文本消息无法进入参与条件解析；应由独立 helper 同时提取文本引用和可信转发来源。
- `resolve_required_chat` 当前把 `get_chat` 与 `get_chat_member` 放在同一个异常块中；拆开两个远端边界后可以给出“链接/可访问性失败”与“管理员权限检查失败”两类稳定公开提示，同时保留异常链供日志取证。
- 额外私有条件才需要 Bot 创建临时加入链接；公开条件已有稳定 `t.me/<username>`，不应因 `can_invite_users=False` 被提前拒绝，但仍必须要求 Bot 为管理员以保证参与者成员资格核验。
- 当前权威产品合同是 `docs/development/v1.9-lottery-bonus-prd.md`，验收清单 J 节已到 J-22；本轮属于参与条件行为变更，需在 V1.9 合同补失败定位/转发来源/主群去重，并新增独立验收项，不能只改 Bot 文案。
- 本轮实现不涉及 schema；独立 PostgreSQL 16 从空库升级到 `b91e4d7a6c20 (head)`，`alembic check` 无新操作，证明转发来源与错误投影只改变 Telegram 输入边界和草稿交互。
- 全量 181 项测试在 60 秒硬上限内通过；Ruff format/check、代码度量和 compileall 同步通过。原 55432 容器密码状态与测试夹具不一致，已使用 55433 的一次性项目隔离数据库验证，未把环境认证失败算作业务失败。
- 自审发现隐藏来源的真实转发通常仍带正文；输入边界已调整为只要存在转发来源但没有权威 chat，就优先返回隐藏来源提示，不再让任意正文落入普通链接格式错误。现代 `origin.chat`、`origin.sender_chat`、legacy `forward_from_chat` 与媒体 caption 链接均有独立回归。
