# Telegram TLS 握手故障修复

## 原因与方案

2026-11-05 生产 `eae5f84` 的频道扫描、个人主页回复和通知投递遇到
`ClientConnectorError: Connection reset by peer`。任务重试可以恢复，但连接重置仍持续发生。
不带 Token、只建立 TLS 连接的探测同样复现；业务数据和 Telegram 权限不是触发条件。

生产镜像使用 OpenSSL 3.5.7。默认 ClientHello 为 1532 字节，包含 OpenSSL 3.5
新增的混合密钥共享。固定 X25519 后为 517 字节，仍协商 TLS 1.3。
同容器、同目标 IP、交替顺序的初轮对照：默认配置 20 次中 2 次在 TLS 阶段连接重置；
X25519 20 次成功；限制 TLS 1.2 的 20 次也成功。
这定位了默认大 ClientHello 在当前 Telegram 路径上的握手故障触发条件；
不能据此确定是哪一个运营商或中间网络设备处理异常。

扩展交替对照每组 50 次：默认配置有 2 次 TLS 重置、2 次 TCP 建连超过 4 秒；
X25519 没有 TLS 重置，1 次 TCP 建连超过 4 秒。TCP 建连发生在发送 ClientHello 之前，
其超时不属于此 TLS 配置修复范围；探测的 4 秒截止也不能等同于生产请求的实际超时。
普通瞬时网络错误仍由原有持久化重试处理。

[OpenSSL 3.5 文档](https://docs.openssl.org/3.5/man3/SSL_CTX_set1_curves/)
明确记录默认混合 keyshare 可能使 ClientHello 超出单个 TCP 段，并被防火墙错误干扰。
实际默认组实现见
[OpenSSL 3.5.7 源码](https://github.com/openssl/openssl/blob/openssl-3.5.7/ssl/t1_lib.c)。

比较两种方案：限制 TLS 1.2 可以缩短握手，但限制了协议选择；仅固定 Telegram 客户端的
X25519 组可以保留 TLS 1.3，影响范围更小，因此采用后者。

## 实施合同

- 公共 Bot 与运维 Bot 的普通会话、Worker 的单请求会话使用同一 Telegram TLS 配置。
- 只在 aiogram 已建立的证书上下文中固定 X25519，保留 CA、主机名校验及协议版本策略。
- 不修改服务器出口、数据库 TLS 或其他项目，不增加代理、请求重试或重定向路径。
- 保持 Update Inbox、Outbox 单请求与连接前失败证明、UNKNOWN 隔离语义。
- 用户明确选择保留全部历史 UNKNOWN；不重新删除消息、不追加投递尝试、不修改风险知悉。

## 验证与验收

- 回归测试检查真实生成的 ClientHello 小于 1200 字节，X25519 keyshare 存在，
  TLS 1.3 仍被提供，证书与主机名校验保持启用。
- 上海专用执行器运行会话及相关测试；发布工作流完成全部检查、八分片回归和镜像构建。
- 发布走 `master -> release -> Actions -> GHCR -> 生产`；核对 SHA、镜像、迁移、容器和轮询。
- 线上以新会话重复建立 TLS，验证实际应用会话的只读 Telegram API 调用及自然频道扫描。
- 比较历史 UNKNOWN 的行、Attempt 和审计指纹；它们必须保持不变。
- 原有未审阅 `delete_message` UNKNOWN 继续使 delivery health 报告 DEGRADED；
  不能把连接修复验收描述为该历史消息已删除或全部业务验收通过。

执行证据保存在 `artifacts/production-error-diagnosis-20261105/`。发布结果待验收后记录。

发布前检查：全范围 Ruff 格式、lint、代码指标与编译检查通过；上海专用执行器的
会话、运维身份和连接失败恢复相关测试 66 项通过。新增测试验证实际 ClientHello
内容与大小，不只检查 setter 调用。
