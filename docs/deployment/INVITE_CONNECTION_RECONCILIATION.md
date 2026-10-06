# 邀请链接建连失败修正合同

本合同修正“所有 TelegramNetworkError 均代表远端未知”的误分类，不改变真正未知投递的处理。
仅适用于 Gateway 中只有一次远端调用的 `create_invite_link`、`create_required_chat_invite`。
普通消息、复合活动卡、超时、响应阶段断线、序列化异常均不适用。

## 新请求

aiogram 保留原始 aiohttp 异常作为 `__cause__`。真实 `ClientConnectorError` 表示
TCP/TLS 连接尚未建立，请求尚未发送；以 `PRE_ACCEPT_FAILED` 记录，原错误仍明确输出。
沿用已有的 `explicit_rejection` Attempt 类别（也用于 Gateway 前的本地校验失败），
表达本次没有远端接受事实，不声称收到 Telegram 拒绝回执。Worker 不自动重试；
用户再次获取链接时沿用原业务键、请求哈希和邀请名称，活动准入/截止检查仍正常执行。

依据：[aiohttp 连接异常](https://docs.aiohttp.org/en/stable/client_reference.html#connection-errors)
及原生产制品内 `TCPConnector._wrap_create_connection`、`AiohttpSession.make_request` 源码。
首次事故制品 `045cf378117117574bea371a6befd74e8852fb1e` 使用 aiogram 3.31.0 / aiohttp 3.14.3；
前者通过 `raise TelegramNetworkError(... f"{type(e).__name__}: {e}") from e` 保存异常身份，
后者在 TCP/TLS 建连异常处创建 `ClientConnectorError`。因此历史中确切的异常类型前缀
有可核验的运行时来源；仅含“Connection reset by peer”的描述不能作为依据。

## 历史修正

只能指定单条 Outbox，先核对其原制品和依赖源码，将不可变版本写入 `runtime_ref`。
工具核对待修正状态、单次请求操作、完整请求哈希、原 Gateway 时间、无租约/远端回执、
最新 Attempt 的方法/UNKNOWN/TelegramNetworkError 与原序列化类型前缀一致。
其他 Attempt 若存在确认或未知结果，拒绝用后来的建连失败推翻它们。
预览指纹绑定 Outbox 状态/更新时间/请求、完整原错误、最新 Attempt 及源码证据引用。
应用在行锁下重读并校验指纹，写入 `outbox.connect_failure_reconciled` 审计，
将当前状态改为明确失败，保留原错误与原 UNKNOWN Attempt，不伪造新调用。
证据不匹配、指纹变化或重复应用均明确失败。

```bash
python -m tg_lottery.ops.reconcile_invite_connection \
  --outbox-id <uuid> --runtime-ref '<original-sha>:aiogram=<version>:aiohttp=<version>' \
  --repair-ref '<incident-reference>'

python -m tg_lottery.ops.reconcile_invite_connection \
  --outbox-id <uuid> --runtime-ref '<original-sha>:aiogram=<version>:aiohttp=<version>' \
  --repair-ref '<incident-reference>' --expected-fingerprint <preview-fingerprint>
```

只通过已授权的生产控制台执行，工具不调用 Telegram。应用后独立回读 Outbox、原 Attempt、
审计和生产巡检。历史活动已截止/开奖时，不重试创建、不改变快照或补记参与/加成。
巡检恢复表示没有该误分类未决项，不表示真实邀请成功或所有业务流程已验收。
