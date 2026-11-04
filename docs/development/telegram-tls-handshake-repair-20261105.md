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

执行证据保存在 `artifacts/production-error-diagnosis-20261105/`。

发布前检查：全范围 Ruff 格式、lint、代码指标与编译检查通过；上海专用执行器的
会话、运维身份和连接失败恢复相关测试 66 项通过。新增测试验证实际 ClientHello
内容与大小，不只检查 setter 调用。

## 发布与线上结果

代码提交 `27af3c4ce58d06335fc6e99a36040a2bf9f4f216` 已经由
[发布工作流 41868458649](https://github.com/chenxi1280/tg-choujiang/actions/runs/41868458649)
完成 checks、tests、build-image 和 deploy。八分片各 234 个测试节点，共 1872 项通过。

北京时间 2026-11-05 00:52:40，新 app、Worker、integration 启动并通过健康检查；
运维 Bot 于 00:54:25 使用相同镜像重建，身份和授权配置核对一致。
`current` 指向 `20261104165104_27af3c4`，四个容器使用同一镜像摘要
`sha256:b219053fc8d4b344ac38fab10fc4a489fe8dd1b337bf74c59a224b48b37ec090`；
Alembic 为 `c34a1030ux14 (head)`。

两类实际会话各创建 20 个独立连接执行 `getMe`，40 次全部取得预期 Bot 身份回执，
ClientHello 均为 517 字节。运维 Bot 的 `getMe` 和无 Webhook 状态也单独核验。
00:56:15 只读快照确认新 Worker 已自然完成 6 次频道扫描；当时的新容器日志中没有
ERROR 或 WARNING。输入持续处理，未触发验收开奖或人为发送业务消息。

全部 38 条 UNKNOWN 及目标关联的 Attempt、原提醒、删除任务和审计指纹均与发布前一致。
整体巡检继续因那一条未审阅的历史 `delete_message` 返回 DEGRADED 和
`CHECK_STATUS=FAILED`。该历史未决项按用户选择保留；连接修复单独验收，
整体投递健康检查仍未通过，原删除结果也仍未确认。

正确性复核：普通会话和单请求会话共享 TLS 配置，后者的请求次数与未知投递分类不变。
性能复核：组配置仅在创建会话时执行，没有每次业务请求的新检查。
可维护性复核：一处配置和真实握手回归覆盖两个会话类型，依赖版本继续固定。
安全性复核：CA、证书和主机名校验保留，TLS 1.3 仍可协商；X25519 取代默认混合 PQ
keyshare，修复范围限于 Telegram 会话。

本次证明上述握手配置修复在验收窗口有效，不承诺外部网络永久无波动；
TCP 连接阶段的瞬时故障仍按原机制记录、重试和监控。未执行真实 Telegram 客户端的
报名到开奖、群公告和中奖私信验收，不能据此宣称这些业务场景已全部通过。
