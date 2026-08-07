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
