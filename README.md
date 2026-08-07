# TG 群抽奖机器人

基于 Python 3.12、aiogram 3、SQLAlchemy 2、Alembic 和 PostgreSQL 16 的可审计 Telegram 群抽奖机器人。

产品与数据合同以 [V1.2 PRD](doc/TG群抽奖机器人PRD_V1.2_数据流转设计版.docx) 为准。核心设计包括数据库先行的 Update Inbox、Telegram Outbox、不可变开奖快照、幂等业务键、Worker 租约和邀请链接密文存储。

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
