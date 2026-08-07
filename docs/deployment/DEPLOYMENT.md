# 生产部署合同

## 发布路径

代码只通过 `master -> release -> GitHub Actions -> GHCR -> 马来西亚服务器` 发布。生产机不直接修改代码。每个发布目录包含 Git 归档和 `.image.env`，`current` 仅在迁移、启动和双健康检查通过后原子切换。

## 服务器目录

- 基础目录：`/data/tg-lottery`
- 共享配置：`/data/tg-lottery/shared/.env`
- 不可变版本：`/data/tg-lottery/releases/<release_id>`
- 当前版本：`/data/tg-lottery/current`
- 外部 Docker 网络：`infra_default`
- PostgreSQL 主机：`app-infra-postgres`

共享配置必须提供 `BOT_TOKEN`、`BOT_ID`、`DATABASE_URL`、`APP_ENCRYPTION_KEY`，并设置实际超级管理员 `ADMIN_IDS`。一个 Bot Token 只能有一个长轮询消费者，禁止与其他服务复用。

## GitHub 配置

仓库 Secrets：`PRODUCTION_HOST`、`PRODUCTION_USER`、`PRODUCTION_PORT`、`PRODUCTION_SSH_PRIVATE_KEY`、`GHCR_USERNAME`、`GHCR_TOKEN`。仓库 Variables：`PRODUCTION_BASE_DIR=/data/tg-lottery`、`RELEASE_BRANCHES=release`。

## 验收证据

发布完成必须分别核验：Actions checks/build/deploy 结论；GHCR 镜像 SHA；服务器 `current` 指向；Alembic revision；app/worker 健康；Bot `getMe`；真实群内创建、报名、消息票、邀请留存、截止复核、开奖、群公告和中奖私聊。容器健康不能替代 Telegram 业务闭环。

## 回滚

```bash
BASE_DIR=/data/tg-lottery bash /data/tg-lottery/current/deploy/rollback.sh <release_id>
```

回滚仍会先运行目标版本迁移和健康检查。若数据库迁移不向后兼容，必须按对应版本迁移合同单独处理，不允许只切镜像伪装成功。
