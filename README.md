# TG 群抽奖机器人

基于 Python 3.12、aiogram 3、SQLAlchemy 2、Alembic 和 PostgreSQL 16 的可审计 Telegram 群抽奖机器人。

产品与数据合同以 [V1.6 PRD](doc/TG群抽奖机器人PRD_V1.6_极简交互重构版.docx) 为准。核心设计包括 `/new` 直达的引导式极简创建、最终卡片预览、数据库先行的 Update Inbox、Telegram Outbox、不可变开奖快照、幂等业务键、Worker 租约和邀请链接密文存储。

## 本地校验

```bash
python -m pip install -e ".[dev]"
ruff format --check src tests alembic scripts
ruff check src tests alembic scripts
python scripts/check_code_metrics.py
alembic upgrade head
pytest
```

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
