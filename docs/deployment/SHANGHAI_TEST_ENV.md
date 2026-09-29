# 上海 Docker 测试环境

抽奖项目的数据库集成测试只使用上海机 `shanghai-tg-test-db` 上的独立容器，
不连接生产数据库或 yunying 测试数据库，也不启动 Telegram Bot、Worker 或真实开奖。

| 资源 | 固定身份 |
| --- | --- |
| 数据库容器 | `tg-lottery-test-postgres`，PostgreSQL 16 |
| 数据库 / 角色 | `tg_lottery_test` / `tg_lottery_test` |
| 数据卷 | `tg_lottery_test_postgres_data` |
| 实例标记 | `shanghai-lottery-dedicated-20260929` |
| 密码文件 | 远端 root-only `/opt/tg-lottery-test/postgres_password` |
| 测试执行器 | `tg-lottery-test-runner:current` |
| 远端入口 | `/opt/tg-lottery-test/run.sh` |

数据库容器不映射公网端口。测试容器共享其网络命名空间，只通过内部
`127.0.0.1:5432` 连接。`tests/conftest.py` 在清表前核对库名、角色和实例标记；
缺少 URL 或实例标记时直接失败。远端入口使用 `flock` 避免同一测试库并发清表。

## 本地和 CI 运行

本地 SSH 别名 `shanghai-tg-test-db` 已连接上海机。运行命令：

```bash
bash deploy/run-shanghai-tests.sh migrate
bash deploy/run-shanghai-tests.sh pytest tests/test_domain.py -q
bash deploy/run-shanghai-tests.sh all
```

脚本只打包当前工作区的 `src`、`tests`、`alembic`、`scripts` 和测试执行器，
传到远端临时目录构建镜像。`all` 先运行 `alembic upgrade head` 与
`alembic check`，再顺序运行八个分片。每个后端测试进程硬超时 60 秒，
超时或数据库身份不匹配均为失败。

GitHub Actions 的 `master` 与 `release` 流水线使用同一远端入口。仓库需配置
`SHANGHAI_TEST_HOST` Variable，以及 `SHANGHAI_TEST_SSH_PRIVATE_KEY` 和
`SHANGHAI_TEST_KNOWN_HOSTS` Secrets。外部 fork 的 PR 无权读取这些凭据，
其工作流只执行静态检查与镜像构建；同仓库 PR、`master` 和 `release` 执行远端测试。

## 只读核查

```bash
ssh shanghai-tg-test-db \
  'sudo -n docker inspect tg-lottery-test-postgres \
    --format "{{.State.Status}} {{.State.Health.Status}} {{json .HostConfig.PortBindings}}"'
ssh shanghai-tg-test-db \
  'sudo -n docker exec tg-lottery-test-postgres \
    psql -U tg_lottery_test -d tg_lottery_test -Atc \
    "SELECT current_database(), current_user, \
    current_setting('\''tg_lottery.test_database_instance'\'', true)"'
```

容器、卷和密码文件必须保留独立身份。测试失败先检查容器健康、迁移结果、
镜像构建和测试日志，不清理或重建生产资源。
