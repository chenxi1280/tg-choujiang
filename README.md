# TG 群抽奖机器人

基于 Python 3.12、aiogram 3、SQLAlchemy 2、Alembic 和 PostgreSQL 16 的可审计 Telegram 群抽奖机器人。

产品与数据合同由 [V1.6 PRD](doc/TG群抽奖机器人PRD_V1.6_极简交互重构版.docx)、[V2.3 多活动与邀请合同](docs/development/v2.3-user-invitation-multi-campaign-prd.md) 和 [V2.5 体验修复](docs/development/v2.5-user-experience-repair-prd.md) 共同定义，后续版本覆盖其明确修改的条款。核心设计包括 `/new` 直达的引导式极简创建、最终卡片预览、数据库先行的 Update Inbox、Telegram Outbox、不可变开奖快照、幂等业务键、Worker 租约和邀请链接密文存储。

## 抽奖时间、分享与开奖提醒

设置开奖时间时可直接选择“自定义时间（北京时间）”，复制时间示例，修改后按 `YYYY-MM-DD HH:MM` 格式发送。快捷时间、自定义输入和抽奖相关时间展示统一使用北京时间；已保存的开奖时刻不会因展示时区改变而被改期。

活动卡正文提供可复制转发的抽奖链接，“立即参与”也使用同一链接，打开机器人后进入报名流程。邀请加成页面和邀请通知正文同时显示对应邀请链接，仍按原活动和群归因。

开奖后向已确认的参与群独立发送结果消息，通过 Telegram 用户 ID @ 中奖者，并向中奖者发送领奖私信；关闭扩展提示不会关闭这些结果通知。消息显式开启通知，实际客户端提醒仍受 Telegram 通知设置及机器人私信可达性影响。

## 抽奖封面

创建抽奖时，封面保存后会收到“图片上传成功”提示（已有封面时为“图片替换成功”），该提示即最新操作面板：可直接再发一张图片替换，或点“✅ 完成”保留当前封面返回预览，“清除封面”可移除已设置的图片。上传后面板会移到新消息，旧消息保留，点击旧按钮会提示改用最新面板，不会改变当前图片输入状态。预览页在未设置封面时显示“上传封面”，已设置后显示“替换封面”。

## 基础抽奖门槛与加成提醒

“准入条件 → 基础连续发言门槛”可为各参与群分别设置连续天数、每天条数及最低字数，例如连续 7 天、每天 5 条。各配置群均达标后方可参与开奖；在此之前邀请和发言加成暂存。每日任务完成、加成增加、全部门槛完成和截止仍未达标均通过 Outbox 提醒对应用户。邀请加成确认后保留，后续退群或邀请链接过期、撤销不扣回；退群重进也不重复奖励。

具体规则、通知时机、兼容和验收范围见 [V2.6 基础门槛与进度提醒](docs/development/v2.6-base-activity-and-progress-notices.md)。

## 本地校验

```bash
python -m pip install -e ".[dev]"
ruff format --check src tests alembic scripts
ruff check src tests alembic scripts
python scripts/check_code_metrics.py
export TEST_DATABASE_URL="postgresql+asyncpg://postgres:<测试密码>@127.0.0.1:<测试端口>/lottery_test"
DATABASE_URL="$TEST_DATABASE_URL" alembic upgrade head
python scripts/run_pytest_shard.py --index 0 --count 8
```

测试使用独立测试数据库，按 `--index 0` 到 `--index 7` 顺序运行八个分片；每个分片执行硬超时为 60 秒，同一数据库不要并发执行。

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

接受 `https://t.me/` 频道或消息链接，未配置的入口不显示。本轮实际创建的测试频道见
[测试频道配置样例](.env.channels.test.example)，可将三项配置合入测试部署环境；样例不含令牌或数据库凭据。
这些配置提供跳转入口，活动自动推送导航、人工精选审核不属于当前实现。

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

### 组合分享和奖品编辑

在“准入条件 → 组合分享链接”可配置 Telegram 聊天文件夹的 `https://t.me/addlist/…` 链接。活动卡出现“一键加入群和频道”，每个实际准入条件仍独立核验。奖品页可按名称与自然序号删除指定项，剩余顺位保持不变。
