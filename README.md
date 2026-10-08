# TG 群抽奖机器人

基于 Python 3.12、aiogram 3、SQLAlchemy 2、Alembic 和 PostgreSQL 16 的可审计 Telegram 群抽奖机器人。

产品与数据合同由 [V1.6 PRD](doc/TG群抽奖机器人PRD_V1.6_极简交互重构版.docx)、[V2.3 多活动与邀请合同](docs/development/v2.3-user-invitation-multi-campaign-prd.md) 和 [V2.5 体验修复](docs/development/v2.5-user-experience-repair-prd.md) 共同定义；创建器调整见 [V2.8 准入与转发参与](docs/development/v2.8-creator-admission-sharing.md)，当前入口、时间校验和历史结果查询见 [V2.9 体验修复](docs/development/v2.9-experience-repair.md)。后续版本覆盖其明确修改的条款。核心设计包括 `/new` 直达草稿预览、最终卡片预览、数据库先行的 Update Inbox、Telegram Outbox、不可变开奖快照、幂等业务键、Worker 租约和邀请链接密文存储。

新活动规则见 [V3.0 抽奖门槛、独立加成与参与条件反馈 PRD](docs/development/v3.0-participation-rules-and-feedback-prd.md)：行为门槛在邀请好友与发言数中二选一，各配置群分别达标；每日发言及邀请加成独立设置，未达门槛时暂存。新活动的邀请加成不限累计上限，历史活动继续使用冻结规则。实施与各层验收状态见 [V3.0 实施记录](docs/development/v3.0-implementation-progress-20261004.md)。

发言计数以 [V3.1 发言计数调整](docs/development/v3.1-message-counting-20261006.md) 覆盖旧字数条款：门槛和加成不再限制每条字数，短文字、图片、纯表情、贴纸和 GIF 表情均可计数；历史字数字段不再参与新增消息判定。

创建条数预设与响应修复见 [2026-10-06 修复记录](docs/development/v3.2-creation-response-repair-20261006.md)。

首页、创建面板和成员操作见 [V3.2 升级 PRD（R2实现与验证进行中）](docs/development/v3.2-navigation-and-interaction-prd-20261006.md)，统一入口、控件、排版、完整用户路径和此前操作优化，附[控件与体验规范](docs/development/v3.2-controls-and-experience-contract-20261006.md)及创建/成员按钮清单。代码、隔离验证、发布与真实客户端验收分别记录在[实现与验收记录](docs/development/v3.2-implementation-and-acceptance-20261006.md)。

[V3.3 统一 PRD：平台必备条件、双加成及完整使用体验（R5）](docs/development/v3.3-unified-participation-and-reputation-prd-20261007.md)是本版唯一规范入口。按用户最新要求，新创建/复用的活跃（人品）与邀请加成都不设每日或累计奖励上限，邀请固定入群满24小时核验、同群同人只计一次；历史已发布规则不追改。公告按条件→奖品→说明→加成→开奖排列，长封面卡保留奖品区；本轮同时修复门槛默认值误判、暂存/保存返回、无目标复制及旧按钮恢复，证据见[修复记录](docs/development/v3.3-creator-response-and-card-repair-20261007.md)。R5进一步补齐群内人品查询/本场活跃入账排行、平台冻结与资格链路、长页/旧模板/历史启动和64位权重，见[再次修复记录](docs/development/v3.3-prd-r5-gap-repair-20261008.md)。代码已发布到`3a10804`，最终master/release两条CI各1123项通过；创建、公告、群内查询、12轮活跃入账和去重已实测，见[代码审查与线上分项验收](docs/development/v3.3-review-release-acceptance-20261008.md)。平台策略仍关闭，正式频道身份/权限及整版生产业务验收尚未完成。

## 首页与创建草稿

私聊 `/start`、`/menu` 或返回首页显示简短导航，提供创建、本人参与记录、邀请记录和使用指南入口；详细说明通过 `/help` 或“使用指南”查看。活动报名、邀请等 `/start` 参数仍进入对应活动流程。

未发布草稿在最后一次更新后保留 24 小时，发送 `/new` 可继续有效草稿。草稿过期时明确说明保留期限；旧面板不能覆盖另一条消息中的当前草稿，提示继续使用当前面板。

## 抽奖时间、分享与开奖提醒

创建器只提供定时开奖，点击“开奖方式”直接进入时间输入页。可点击正文日期复制，修改后按 `YYYY-MM-DD HH:MM` 格式直接发送；没有单独的复制按钮。重新打开时标明“已保存的开奖时间”，未设置时显示未来时间示例。快捷项为“7天后”“10天后”“明晚20:00”，输入页可直接返回草稿。格式错误、不存在的日期或时间、距当前时间不足要求分别提示；开奖时间必须晚于当前时间 5 分钟。快捷时间、自定义输入和抽奖相关时间展示统一使用北京时间，已保存的开奖时刻不会因展示时区改变而被改期。历史已发布的满人活动沿用原规则，旧满人草稿发布前需改为定时。

活动卡正文和参与按钮使用同一活动链接。V3.0 打开链接先展示完整参与条件，点击明确登记后才核验成员资格并启动计数；单纯查看或入群不自动报名。较长条件按冻结清单分多条规则消息投递，所有必要页确认后才发布主卡；规则页未知或失败不能显示发布完成。邀请页面和邀请通知正文显示对应邀请链接，仍按原活动和群归因。

公告使用图片在上、条件/奖品/说明在下的原生消息样式，群名可点击，逐群门槛缩进显示，加成摘要链接到完整规则。“获取分享卡片”会通过 Outbox 向请求者本人私聊发送同款公告，长按即可转发；正文参与链接保证转发后仍有入口，获取卡片本身不报名。具体排版、长文与投递边界见 [公告与转发卡片样式](docs/development/message-card-style-20261005.md)。

开奖后向已确认的参与群独立发送结果消息，通过 Telegram 用户 ID @ 中奖者，并向中奖者发送领奖私信；关闭扩展提示不会关闭这些结果通知。消息显式开启通知，实际客户端提醒仍受 Telegram 通知设置及机器人私信可达性影响。

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

## 使用指南与测试频道

私聊 `/help` 或首页“使用指南”打开内置说明。以下配置在首页与指南中增加自有频道入口：

- `GUIDE_CHANNEL_URL`：使用说明频道或说明消息。
- `NAVIGATION_CHANNEL_URL`：抽奖导航频道。
- `FEATURED_CHANNEL_URL`：精选推荐频道。

接受 `https://t.me/` 频道或消息链接，未配置的入口不显示。按用户要求，三个频道目前继续作为测试入口，线上体验测试沿用现有链接，不替换正式频道。对应配置见[测试频道配置样例](.env.channels.test.example)，样例不含令牌或数据库凭据。这些配置提供跳转入口，活动自动推送导航、人工精选审核不属于当前实现；入口配置与测试成功、正式运营分别记录。

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
