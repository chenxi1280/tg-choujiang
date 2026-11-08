# 生产服务监控合同（2026-11-04）

## 已确认方案与范围

采用生产同机 systemd，每 5 分钟执行一次只读巡检，直接向飞书自定义机器人推送。另一方案为上海机跨机巡检，可发现生产整机离线，但增加 SSH 依赖；本次选择同机方案，整机断网或宕机时无法主动发送告警。

仓库维护的 GitHub Actions 仅保留 Deploy Production 及其内部质量检查、上海测试、镜像构建和部署。移除独立健康监控、Python Checks、手动诊断与 Outbox 对账工作流；诊断和对账的应用模块保留，人工操作仍需原有授权。

GitHub 另有托管的 `dynamic/dependabot/update-graph`，其 Workflow 停用 API 返回 422；它不属于上述仓库工作流，且不消耗 Actions 分钟额度，见[GitHub 官方说明](https://docs.github.com/en/code-security/concepts/supply-chain-security/dependency-graph-data)。本次保留该托管任务，不改依赖图安全设置。

## 检查与通知

- 复用当前版本 `deploy/check-services.sh`，核对 app/worker 容器、轮询与 Worker 心跳、Inbox 堵塞、迁移版本和投递健康。任何非零退出或超时均告警。
- 检查已配置的 integration 和已部署的 ops-bot 容器；扫描上述容器最近 30 分钟 ERROR、CRITICAL、FATAL、PANIC 和 Python traceback。通知只含容器、日志级别、logger 和计数，不转发原始消息正文或堆栈。
- 可用内存低于 128 MiB、项目所在磁盘使用率达到 90% 时告警。监控不重启服务、不改数据库、不重发 Telegram 消息。
- 出现新异常时发送一次「线上告警」，即使同轮也有旧异常消失。只减少异常且仍有未决项时发送「告警范围缩小」，并列出当前未决项。异常集合相同时不重复发送，全部消失才发送「告警恢复」。日志异常不再检出表示已移出最近 30 分钟观察窗口。健康时首次运行发送启用通知，每天北京时间 09:00–09:59 的首次成功巡检发送正常日报。
- 飞书 HTTP 或业务返回码失败时巡检失败，不能记录为通知成功；保留上次通知状态，下轮继续尝试。告警本身保留非零退出码，systemd timer 继续按计划运行。
- 历史已知悉 UNKNOWN 仍在每次巡检的 journal 中逐条显示，日报/恢复通知明确其仍未确认；新 UNKNOWN 和失效审计仍告警。不能把监控恢复解释为 Telegram 业务成功。

## 安装与运行

发布程序在切换 `current` 后安装 `deploy/systemd/tg-lottery-healthcheck.service` 和 timer。服务以 root 运行，使用宿主机 Python 3.6+ 标准库；不依赖业务容器内的 Python，不新增第三方依赖。首次启用前将 `FEISHU_WEBHOOK_URL` 写入 `/etc/tg-lottery/service-health.env`，目录权限 700、文件 600；Webhook 不进入 Git、Actions 或命令输出。

状态文件：`/data/tg-lottery/shared/service-health.json`，权限 600；日志使用 systemd journal。脚本总执行时间受 systemd 300 秒限制，现有服务检查 90 秒、Docker 命令 15 秒、飞书请求 10 秒。

```bash
sudo systemctl status tg-lottery-healthcheck.timer
sudo journalctl -u tg-lottery-healthcheck.service --since today
sudo systemctl start tg-lottery-healthcheck.service
```

## 成功标准

上海隔离测试验证：异常、去重、部分异常消失、全部恢复、日报、发送失败重试、读取失败/超时告警及历史 UNKNOWN 语义。线上核对相同提交的 current、镜像、迁移与容器状态，timer 已启用且有下次执行时间，首次真实巡检收到飞书成功回执。非发布工作流停用并从仓库移除。真实恢复与翌日 09:00 的自动执行，未自然发生前仅能计为隔离验证通过。
