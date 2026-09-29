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
| 服务端映射 | `124.221.246.89:15433 -> 5432`；公网协议握手当前失败 |
| 本机入口 | SSH 隧道 `127.0.0.1:25433 -> 上海机 127.0.0.1:15433` |
| 本机 CA | `/Users/xida/.config/tg-lottery/test-db-ca.crt` |
| 本机配置 | `.env.test`，Git 忽略，权限 `0600` |
| 测试执行器 | `tg-lottery-test-runner:current` |
| 远端入口 | `/opt/tg-lottery-test/run.sh` |

数据库容器映射独立的 `15433` 端口。公网 TCP 可建立，但 PostgreSQL 协议握手
当前无响应；云侧入站规则尚未核实，因此本机经 SSH 隧道访问。测试容器共享数据库
网络命名空间，只通过内部 `127.0.0.1:5432` 连接。
公网路径只允许 TLS 1.3 与 SCRAM；证书 SAN 同时包含公网 IP 和隧道端的
`127.0.0.1`，本机使用 `ssl=verify-full` 及独立 CA 验证。
`tests/conftest.py` 在清表前核对库名、角色和实例标记；
缺少 URL 或实例标记时直接失败。远端入口使用 `flock` 避免同一测试库并发清表。

## 本地和 CI 运行

本地 SSH 别名 `shanghai-tg-test-db` 已连接上海机。本机 `.env.test` 的
`TEST_DATABASE_URL` 指向 `127.0.0.1:25433/tg_lottery_test`，同时包含
`PGSSLROOTCERT` 和实例标记；密码从远端 root-only 文件安全取得并只保存在本机。
本机验证和远端测试分别运行：

```bash
bash deploy/open-shanghai-test-db-tunnel.sh
python scripts/check_shanghai_test_db.py
bash deploy/run-shanghai-tests.sh migrate
bash deploy/run-shanghai-tests.sh pytest tests/test_domain.py -q
bash deploy/run-shanghai-tests.sh all
```

脚本只打包当前工作区的 `src`、`tests`、`alembic`、`scripts` 和测试执行器，
传到远端临时目录构建镜像。`all` 先运行 Alembic 升级、差异检查、
降级到 base、重新升级及差异检查，再顺序运行八个分片。每个后端测试进程硬超时 60 秒，
超时或数据库身份不匹配均为失败。
SSH 隧道只用于只读身份检查和开发工具；完整数据库测试与迁移仍由远端串行入口运行。

GitHub Actions 的 `master` 与 `release` 流水线使用同一远端入口。仓库需配置
`SHANGHAI_TEST_HOST` Variable，以及 `SHANGHAI_TEST_SSH_PRIVATE_KEY` 和
`SHANGHAI_TEST_KNOWN_HOSTS` Secrets。外部 fork 的 PR 无权读取这些凭据，
其工作流只执行静态检查与镜像构建；同仓库 PR、`master` 和 `release` 执行远端测试。

## 只读核查

```bash
ssh shanghai-tg-test-db \
  'sudo -n docker inspect tg-lottery-test-postgres \
    --format "{{.State.Status}} {{.State.Health.Status}} {{json .HostConfig.PortBindings}}"'
python scripts/check_shanghai_test_db.py
```

容器、卷和密码文件必须保留独立身份。测试失败先检查容器健康、迁移结果、
镜像构建和测试日志，不清理或重建生产资源。
