# TG 群抽奖机器人

基于 Python 3.12、aiogram 3、SQLAlchemy 2、Alembic 和 PostgreSQL 16 的可审计 Telegram 群抽奖机器人。

运营平台联动开发见[只读状态接口](docs/development/operations-integration-read-api-20261027.md)：独立监听、活动创建者白名单及一致只读快照，默认不启用，不改变报名、计数或开奖规则。

产品与数据合同由 [V1.6 PRD](doc/TG群抽奖机器人PRD_V1.6_极简交互重构版.docx)、[V2.3 多活动与邀请合同](docs/development/v2.3-user-invitation-multi-campaign-prd.md) 和 [V2.5 体验修复](docs/development/v2.5-user-experience-repair-prd.md) 共同定义；创建器调整见 [V2.8 准入与转发参与](docs/development/v2.8-creator-admission-sharing.md)，当前入口、时间校验和历史结果查询见 [V2.9 体验修复](docs/development/v2.9-experience-repair.md)。参与入口与完成提醒见 [2026-10-09 修订](docs/development/v3.2-navigation-and-interaction-prd-20261006.md#2026-10-09-参与入口与提醒修订)。封面接收与前置条件进度见 [2026-10-14 修订](docs/development/v3.3-unified-participation-and-reputation-prd-20261007.md#2026-10-14-封面接收与前置条件进度修订)。后续版本覆盖其明确修改的条款。核心设计包括 `/创建抽奖` 直达草稿预览、最终卡片预览、数据库先行的 Update Inbox、Telegram Outbox、不可变开奖快照、幂等业务键、Worker 租约和邀请链接密文存储。

当前线上代码 `85feb77` 按[抽奖体验优化 PRD R14](docs/development/lottery-experience-optimization-prd-20261029.md)及2026-10-30中文入口修订执行，保留原门槛、发言与邀请计算规则。群聊只响应中文业务指令；Bot私聊使用中文按钮与中文指令，原生菜单仅保留 `/start`。群内进度回复20秒删除，Bot私聊进度回复保留。master与release两轮各1744项通过，发布及受控客户端证据见[R14实施与验证记录](docs/development/lottery-r14-implementation-20261030.md)。

新活动规则见 [V3.0 抽奖门槛、独立加成与参与条件反馈 PRD](docs/development/v3.0-participation-rules-and-feedback-prd.md)：行为门槛在邀请好友与发言数中二选一，各配置群分别达标；每日发言及邀请加成独立设置，未达门槛时暂存。新活动的邀请加成不限累计上限，历史活动继续使用冻结规则。实施与各层验收状态见 [V3.0 实施记录](docs/development/v3.0-implementation-progress-20261004.md)。

发言计数以 [V3.1 发言计数调整](docs/development/v3.1-message-counting-20261006.md) 覆盖旧字数条款：门槛和加成不再限制每条字数，短文字、图片、纯表情、贴纸和 GIF 表情均可计数；历史字数字段不再参与新增消息判定。

显式启用固定任务日期的活动按[固定日期任务合同](docs/development/fixed-message-task-calendar-20261029.md)执行：仅本期开始至截止之间的发言和加成生效，每个任务日分别达标，提示区分今日完成与整场完成；未启用的历史活动维持原规则。

创建条数预设与响应修复见 [2026-10-06 修复记录](docs/development/v3.2-creation-response-repair-20261006.md)。

首页、创建面板和成员操作见 [V3.2 升级 PRD（R2实现与验证进行中）](docs/development/v3.2-navigation-and-interaction-prd-20261006.md)，统一入口、控件、排版、完整用户路径和此前操作优化，附[控件与体验规范](docs/development/v3.2-controls-and-experience-contract-20261006.md)及创建/成员按钮清单。代码、隔离验证、发布与真实客户端验收分别记录在[实现与验收记录](docs/development/v3.2-implementation-and-acceptance-20261006.md)。

[V3.3 统一 PRD：平台必备条件、双加成及完整使用体验（R13）](docs/development/v3.3-unified-participation-and-reputation-prd-20261007.md)是本版唯一规范入口。按用户最新要求，新创建/复用的活跃（人品）与邀请加成都不设每日或累计奖励上限，邀请固定入群满24小时核验、同群同人只计一次；历史已发布规则不追改。公告按条件→奖品→说明→加成→开奖排列，长封面卡保留奖品区；本轮同时修复门槛默认值误判、暂存/保存返回、无目标复制及旧按钮恢复，证据见[修复记录](docs/development/v3.3-creator-response-and-card-repair-20261007.md)。R5进一步补齐群内人品查询/本场活跃入账排行、平台冻结与资格链路、长页/旧模板/历史启动和64位权重，见[再次修复记录](docs/development/v3.3-prd-r5-gap-repair-20261008.md)。R6统一创建/成员术语、双规则入口、群查询发现说明、邀请分页/记录入口及结束态，代码已发布到`081d345`，最终master/release两条CI各1144项通过，见[体验统一及最终验收](docs/development/v3.3-experience-unification-20261008.md)。此前创建、公告、群内查询、12轮活跃入账与去重事实见[上一轮线上分项验收](docs/development/v3.3-review-release-acceptance-20261008.md)。R7进一步将同级按钮改为两列、主操作与底部导航分区，代码`4e8e4b0`已发布、双CI各1149项通过，帮助菜单、本人加成摘要及门槛预设已现场核对，跳转、选择反馈及返回正确；手机端及其他页面逐项现场验收仍未完成，见[按钮排版修复](docs/development/v3.3-button-layout-repair-20261008.md)。R8进一步分离普通按钮无内容回执与实际操作，避免回执网络失败挡住Inbox；代码`e15309c`已发布、双CI各1171项通过，最后客户端点击因聊天切换未完成，见[回执阻塞修复记录](docs/development/v3.3-callback-feedback-repair-20261008.md)。R9继续复查门槛/加成数字输入及旧按钮、复制恢复，代码`260533b`已发布，双CI各1209项通过，客户端数字点击尚未验收，见[数字编辑恢复修复](docs/development/v3.3-numeric-editor-repair-20261008.md)。R10继续线上补验创建、门槛数字错误后继续/恢复/返回及加成输入，修复复制页分页、奖品危险操作分区和发布规则页标题，系统表情统一为星光/成长风格；已并入R11发布首版，历史验收证据见[本轮线上复查与表情修复](docs/development/v3.3-brand-and-online-audit-20261008.md)。R11按用户新要求将门槛改为5/8/10/自定义与直接回复正整数、直接保存本群到草稿，并在最新回复明确反馈，扩展完整界面的语义图标；最终92a37ba已发布，master/release各1242项通过，app/worker同镜像健康。真实私聊已验证快捷8、非法0后直接回复10、自定义取消、快捷5及返回，独立草稿回读确认保存5条/天；最终帮助菜单及创建主题样式也已现场确认，见[快捷门槛与风格修复](docs/development/v3.3-gate-quick-save-and-style-20261008.md)。R12进一步按具体功能区分创建、继续草稿、本人参与、管理、预览/发布、群组/频道、搜索/复制及各字段，已有系统图标同样规范，优先Web复查，见[功能图标与Web复查](docs/development/v3.3-function-icons-and-web-audit-20261008.md)；R13进一步补验并修复门槛返回目标文案、列表/记录/个人页标题及动态按钮遗漏，最终2fec5a2已发布，双CI各1279项通过，app/worker同镜像健康。电脑插件已在Telegram Web补验门槛8→非法0→直接回复10→取消→5及返回，并确认创建、条件、加成、封面、更多、奖品空状态、参与/邀请/发起/管理与个人页；草稿独立回读为5条/天、1天。平台策略仍关闭，正式频道身份/权限及整版生产业务验收尚未完成。

## 首页与创建草稿

私聊 `/start`、`/首页` 或返回首页显示简短导航，提供创建、本人参与记录、邀请记录和使用指南入口；详细说明通过 `/使用指南` 或“使用指南”查看。活动报名、邀请等 `/start` 参数仍进入对应活动流程。

未发布草稿在最后一次更新后保留 24 小时，发送 `/创建抽奖` 可继续有效草稿。草稿过期时明确说明保留期限；旧面板不能覆盖另一条消息中的当前草稿，提示继续使用当前面板。

## 抽奖时间、分享与开奖提醒

2026-10-21 新版参与条件与分享修订见[两个官方频道、加成提醒与群分享](docs/development/two-channel-requirements-and-group-sharing-20261021.md)：新平台策略仅要求精选与官方抽奖两个频道和启动 Bot，历史冻结规则保留；完成通知补充人品加成剩余条数，分享卡增加群内联分享。实施、测试和线上启用状态分别以该记录为准。

2026-10-29 提醒群名和转发参与入口修订见[提醒与转发入口](docs/development/progress-links-and-forwarding-20261029.md)：进度提醒可跳转对应群，活动卡正文显示“点击参与”，新发送卡片使用可转发的链接按钮。

同日进度提醒精简与结果入口文案见[提醒文案精简](docs/development/compact-progress-notices-20261029.md)：自动提醒保留可点击的群名、门槛及加成状态；结果通知使用“查看开奖结果”链接。

创建器只提供定时开奖，点击“开奖方式”直接进入时间输入页。可点击正文日期复制，修改后按 `YYYY-MM-DD HH:MM` 格式直接发送；没有单独的复制按钮。重新打开时标明“已保存的开奖时间”，未设置时显示未来时间示例。快捷项为“7天后”“10天后”“明晚20:00”，输入页可直接返回草稿。格式错误、不存在的日期或时间、距当前时间不足要求分别提示；开奖时间必须晚于当前时间 5 分钟。快捷时间、自定义输入和抽奖相关时间展示统一使用北京时间，已保存的开奖时刻不会因展示时区改变而被改期。历史已发布的满人活动沿用原规则，旧满人草稿发布前需改为定时。

活动卡正文和参与按钮使用同一活动链接。V3.0 打开链接先展示完整参与条件，点击明确登记后才核验成员资格并启动计数；单纯查看或入群不自动报名。较长条件按冻结清单分多条规则消息投递，所有必要页确认后才发布主卡；规则页未知或失败不能显示发布完成。邀请页面和邀请通知正文显示对应邀请链接，仍按原活动和群归因。

公告使用图片在上、条件/奖品/说明在下的原生消息样式，群名可点击，逐群门槛缩进显示，加成摘要链接到完整规则。“获取分享卡片”会通过 Outbox 向请求者本人私聊发送同款公告，长按即可转发；正文参与链接保证转发后仍有入口，获取卡片本身不报名。具体排版、长文与投递边界见 [公告与转发卡片样式](docs/development/message-card-style-20261005.md)。

开奖后向已确认的参与群独立发送结果消息，通过 Telegram 用户 ID @ 中奖者和实际创建者，并分别向中奖者发送领奖私信、向创建者发送开奖完成私信；关闭扩展提示不会关闭这些结果通知。创建者通知以 `Campaign.created_by` 为准，与发奖联系人和手动开奖操作人分别处理。消息显式开启通知，实际客户端提醒仍受 Telegram 通知设置及机器人私信可达性影响。详细通知合同见 [2026-10-13 修订](docs/development/v3.3-unified-participation-and-reputation-prd-20261007.md#2026-10-13-开奖完成通知修订)。

正式开奖通知和中奖私信附带可点击的 `CJ-<活动编号>` 结果编号，进入同一活动的已完成开奖记录，展示实际开奖时间、中奖名单和未分配名额；无需报名，归档后仍可查询。此编号用于官方结果回查。新通知添加编号，历史通知不批量重发。

“我参加的抽奖 → 已结束”按本人参与记录回查结果，展示中奖、未中奖、未进入最终名单或活动取消，并区分计划与实际开奖时间。最终开奖次数取已保存快照，奖品、名额和领奖方式取完成的开奖记录；缺少开奖记录时明确提示核查。群内记录按钮将对应查询发送到点击者与机器人的本人私聊，不在群中展示个人记录。

## 抽奖封面

创建抽奖时，封面保存后会收到“图片上传成功”提示（已有封面时为“图片替换成功”），该提示即最新操作面板：可直接再发一张图片替换，或点“✅ 完成”保留当前封面返回预览，“清除封面”可移除已设置的图片。上传后面板会移到新消息，旧消息保留，点击旧按钮会提示改用最新面板，不会改变当前图片输入状态。预览页在未设置封面时显示“上传封面”，已设置后显示“替换封面”。

## 准入条件、历史门槛与加成提醒

“准入条件 → 添加准入条件”进入管理员群/频道列表，可勾选、取消、分页、搜索与刷新，也可发送群/频道链接或转发来源可见的消息。创建者和机器人都须是目标管理员，参与活动的群不能同时作为额外准入项。V3.0 的发言数或邀请好友门槛从预览页“抽奖门槛”进入，只能设置一种；逐群选择是否计入门槛。活跃加成（人品）和邀请加成从各自入口独立设置，额外准入条件管理关注或加入要求。

准入页已移除“基础发言门槛”“邀请与每日发言加成”“组合分享链接”入口，抽奖加成从预览页进入。历史已保存的基础门槛继续按原规则核验，变更额外准入条件保留参与活动群的已有发言门槛，仅移除被删除准入群的门槛。各配置群均达标后方可参与开奖；在此之前邀请和发言加成暂存。每日任务完成、加成增加、全部门槛完成和截止仍未达标均通过 Outbox 提醒对应用户。邀请加成确认后保留，后续退群或邀请链接过期、撤销不扣回；退群重进也不重复奖励。

活跃加成（人品）支持自定义每轮有效发言条数和奖励次数，新场每天可反复达标，不设每日或累计上限。历史已发布活动保留“每天达标一次”等冻结设置。北京时间每天零点更新今日统计，累计加成保留，计奖期间页面显示今日与累计次数。新开启的逐群邀请奖励默认为每 1 名有效新成员 +1 票。截止、开奖、取消后的加成页明确说明本期停止新增计奖，保留累计记录，不再引导完成下一次奖励；邀请留存期限导致最晚计奖入群时间先到时也停止新增邀请引导，既有邀请仍按原合同核验。开奖后隐藏本场邀请分享入口，旧链接仍可入群。

具体规则、通知时机和历史兼容范围见 [V2.6 基础门槛与进度提醒](docs/development/v2.6-base-activity-and-progress-notices.md) 与 [V2.7 准入与每日加成](docs/development/v2.7-creator-admission-and-daily-bonus.md)；当前准入入口与结束展示以 V2.8、V2.9 的覆盖条款为准。

## 校验与上海 Docker 测试环境

```bash
python -m pip install -e ".[dev]"
ruff format --check src tests alembic scripts deploy
ruff check src tests alembic scripts deploy
python scripts/check_code_metrics.py
bash deploy/open-shanghai-test-db-tunnel.sh
python scripts/check_shanghai_test_db.py
bash deploy/run-shanghai-tests.sh all
```

测试库与测试进程运行在上海服务器的独立 Docker 环境。最后一条命令传输当前工作区源码，
在服务器内执行 Alembic 往返迁移检查及八个顺序测试分片，每个测试进程硬超时 60 秒。
定向测试可运行 `bash deploy/run-shanghai-tests.sh pytest tests/test_domain.py -q`。
本机只读连接使用被 Git 忽略的 `.env.test`：`127.0.0.1:25433` 经 SSH 隧道连接上海机
`15433`，并使用独立 CA 验证 TLS 证书。配置结构见 [.env.test.example](.env.test.example)。
测试库身份、CI 配置与故障核查见 [上海测试环境](docs/deployment/SHANGHAI_TEST_ENV.md)。

运行时配置见 `.env.docker.example`，生产发布和证据要求见 [部署文档](docs/deployment/DEPLOYMENT.md)。

## 隔离运维 Bot

人工指定中奖不接入正式 Bot。需要操作时，为独立 Bot 配置 `OPS_BOT_TOKEN` 与非空
`OPS_ADMIN_IDS`，再按需启动隔离进程：

```bash
docker compose --profile ops run --rm ops-bot
```

该进程只接受白名单用户的私聊命令，不加入活动群，也不会触发截止、资格复核、结算或开奖：

```text
/campaigns
/winner_list <活动编号>
/winner_set <活动编号> <奖品顺位> <名额> <@用户名或用户ID>
/winner_del <活动编号> <奖品顺位> <名额>
```

正常部署只启动 `app` 与 `worker`，不会启动此运维 Bot。运维 Bot token 若复用正式 Bot 身份，
启动时会因数据库中已存在正式 polling 身份而拒绝运行。

## 使用指南与官方频道

私聊 `/使用指南` 或首页“使用指南”打开内置说明。以下配置在首页与指南中增加自有频道入口：

- `GUIDE_CHANNEL_URL`：使用说明频道或说明消息。
- `NAVIGATION_CHANNEL_URL`：抽奖导航频道。
- `FEATURED_CHANNEL_URL`：精选推荐频道。

接受 `https://t.me/` 频道或消息链接，未配置的入口不显示。现有正式入口为[使用说明](https://t.me/potato_lottery_guide)、[抽奖导航](https://t.me/potato_lottery_nav)和[精选推荐](https://t.me/potato_lottery_featured)。链接配置只提供跳转入口；精选频道自动推送及每日北京时间 10:00 汇总需另行启用 `FEATURED_PUSH_ENABLED` 并指定稳定频道 ID。所有群内发布成功、仍可参与的活动均收录，首次启用也补推现有活动；规则与验收边界见[精选频道推送修订](docs/development/featured-channel-push-20261029.md)。历史[测试频道配置样例](.env.channels.test.example)仅供测试部署使用，不含凭据。

后台指定中奖人通过隔离运维 Bot 操作，前台继续正常展示中奖结果并通知中奖者。

### 服务器后台指定中奖

正式机器人不注册指定命令。可选的隔离运营 Bot 和服务器控制台共用指定校验及审计服务，中奖者仍正常出现在前台结果中。

生产环境的 `OPS_ADMIN_IDS` 配置允许操作的 Telegram 用户。服务器管理员通过 SSH 使用下列命令（`操作人ID` 必须在白名单中）：

```bash
docker exec tg-lottery-app python -m tg_lottery.ops.winner_console --actor 操作人ID '/campaigns'
docker exec tg-lottery-app python -m tg_lottery.ops.winner_console --actor 操作人ID '/winner_list 活动编号'
docker exec tg-lottery-app python -m tg_lottery.ops.winner_console --actor 操作人ID '/winner_set 活动编号 奖品顺位 名额 @用户名'
docker exec tg-lottery-app python -m tg_lottery.ops.winner_console --actor 操作人ID '/winner_del 活动编号 奖品顺位 名额'
```

先查看活动、奖品和名额再设置。被指定者必须已经报名，开奖时仍需通过冻结资格复核；不合格时明确报错，不静默替换。控制台不启动公开 Bot、不触发截止或开奖，审计来源为 `offline_ops_cli`。服务器身份验证和数据库访问权限只授予运营管理员。

### 历史组合分享配置和奖品编辑

当前准入页不提供组合分享链接编辑入口。旧配置已有 Telegram 聊天文件夹 `https://t.me/addlist/…` 链接时仍保留其状态和活动卡“一键加入群和频道”行为，每个实际准入条件继续独立核验。奖品页可按名称与自然序号删除指定项，剩余顺位保持不变。
