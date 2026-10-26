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

共享配置必须提供 `BOT_TOKEN`、`DATABASE_URL`、`APP_ENCRYPTION_KEY`。`BOT_ID` 默认从 Token 的数字前缀解析，并在启动时通过 `getMe` 再校验；也可显式配置一致值。`ADMIN_IDS` 仅作为可选预留项，实际群管理权限以机器人入群时同步并在敏感动作前实时复核的 Telegram 管理员身份为准。一个 Bot Token 只能有一个长轮询消费者，禁止与其他服务复用。

## GitHub 配置

仓库 Secrets：`PRODUCTION_HOST`、`PRODUCTION_USER`、`PRODUCTION_PORT`、`PRODUCTION_SSH_PRIVATE_KEY`。GHCR 构建和远端拉取统一使用当前 Actions run 的 `GITHUB_TOKEN`，不依赖长期 PAT。仓库 Variables：`PRODUCTION_BASE_DIR=/data/tg-lottery`、`RELEASE_BRANCHES=release`、`PRODUCTION_ENABLED=true`；生产健康工作流每 15 分钟检查服务与未决事实。

每次迁移前会通过共享 PostgreSQL 容器生成 custom-format 备份到 `/data/tg-lottery/backups/postgres`，默认保留最近 14 份。`admin` 发布用户通过 `sudo -n docker` 操作 Docker，不依赖 Docker socket 组权限。

## 验收证据

发布完成必须分别核验：Actions checks/build/deploy 结论；GHCR 镜像 SHA；服务器 `current` 指向；Alembic revision；app/worker 健康；Bot `getMe`；confirmed/unprojected、UNKNOWN 和失败队列；真实群内创建、报名、消息票、邀请留存、截止复核、开奖、群公告和中奖私聊。容器健康不能替代 Telegram 业务闭环。

app 健康检查同时核对轮询连续性与当前 Bot 的处理队列：最早未完成 Update 等待超过 120 秒即明确报告阻塞，不能由新鲜轮询心跳掩盖。`check-services.sh` 单独报告已处理输入中界面回信被 Telegram 明确拒绝的数量；这些记录保留 `last_error`，不表示回信送达或业务成功。明确丢失 Bot 成员身份的历史管理员事件，在撤销本地管理员缓存后单独计入 `unavailable_memberships`，不代表权限同步或提示送达成功。未知网络结果、资格核验错误和内部处理错误仍按原 Inbox 顺序重试；Outbox UNKNOWN 不自动重发。

Inbox 限流通过 `next_attempt_at` 持久保存 Telegram 的最早重试时间；普通失败仍遵循原重试间隔。成功处理会清理当前 `last_error`，但独立保留最近一次 `last_failure` 供追溯。长轮询也遵守 `retry_after`，等待可被正常停机打断。

迁移 `c33a1026ir01` 新增上述两个可空字段并回填现有错误，兼容原有输入；存在尚未到期的 Inbox 限流等待时拒绝数据库降级。回滚运行制品也须支持 Inbox 限流截止时间，不能运行旧逻辑绕过等待。

## 回滚

```bash
BASE_DIR=/data/tg-lottery bash /data/tg-lottery/current/deploy/rollback.sh <release_id>
```

回滚仍会先运行目标版本迁移和健康检查。若数据库迁移不向后兼容，必须按对应版本迁移合同单独处理，不允许只切镜像伪装成功。

V3.0 发布及回滚在迁移或替换进程前执行制品规则能力检查。候选镜像运行
`python -m tg_lottery.rule_capabilities --describe`，当前守卫将其声明与数据库中全部活动
（包含历史查询和未决投递对应活动）的冻结规则版本对照。不识别版本或缺少能力入口时明确失败，
保留当前运行制品；首次启用 V3.0 后只选择包含该检查且支持 V3.0 的回滚版本。
数据库 downgrade 在存在 V3.0 活动时拒绝删除资格事实。
