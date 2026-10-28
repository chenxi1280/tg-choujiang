# 邀请链接建连失败修正合同

本合同区分有严格请求前证据的连接失败和仍然未知的投递。普通消息、复合活动卡、超时、
响应阶段断线、序列化异常均不能仅凭错误文本判定远端未执行。

## 新请求

Worker 的 Telegram API 请求使用单次 HTTP POST，并明确禁止自动重定向。只有该传输路径
抛出的 `PreRequestConnectionError` 且原始 `__cause__` 为 `ClientConnectorError`，
才能证明该次单请求在建连阶段失败。邀请创建以 `PRE_ACCEPT_FAILED` 记录，原错误仍明确输出。
该证明当前仅对已核验的 aiogram 3.31.0 / aiohttp 3.14.5 且无代理的运行时启用；依赖
版本已固定；版本变化或启用代理时，连接异常恢复为 UNKNOWN，直到重新核验传输实现。
HTTP 3xx 或 5xx 也一律按结果未知处理，即使响应体带有 `retry_after` 字段。
沿用已有的 `explicit_rejection` Attempt 类别（也用于 Gateway 前的本地校验失败），
表达本次没有远端接受事实，不声称收到 Telegram 拒绝回执。Worker 不自动重试；
用户再次获取链接时沿用原业务键、请求哈希和邀请名称，活动准入/截止检查仍正常执行。

群进度提醒、临时提醒和已确认临时提醒的删除请求各自只有一次 Telegram 写调用；同样有上述
请求前证据时，在原业务键上最多延迟重试五次，分别等待 5、15、30、60、90 秒；重试次数
按有证明的建连失败计数，Telegram 限流等待不消耗该次数。每次 Attempt 均保留，达到上限
明确记录失败，健康 Action 对这三类操作的明确失败返回非零。
其它异常和无法证明传输阶段的连接错误继续进入 `UNKNOWN_HOLD`，不自动重发。

依据：[aiohttp 连接异常](https://docs.aiohttp.org/en/stable/client_reference.html#connection-errors)
及原生产制品内 `TCPConnector._wrap_create_connection`、`AiohttpSession.make_request` 源码。
aiogram 原默认传输保留异常身份，但 aiohttp 默认允许跟随 HTTP 重定向：一个早先请求
可能已经发出，后续连接才失败。因此即使历史错误前缀为 `ClientConnectorError`，
也不能仅靠版本源码和错误文本声称整个 Telegram 操作未发送。
参考：[aiohttp 客户端重定向参数](https://docs.aiohttp.org/en/stable/client_reference.html)。

## 历史修正

只能指定单条 Outbox，先核对其原制品和依赖源码，将不可变版本写入 `runtime_ref`；
还必须提供与该次尝试关联、能独立证明没有发生重定向的证据引用
`unredirected_evidence_ref`。没有该证据时保留 UNKNOWN，不得用推测补齐。
工具只把该引用绑定到预览指纹和审计，并不自动核验引用的内容；执行者必须先独立验证证据。
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
  --repair-ref '<incident-reference>' --unredirected-evidence-ref '<verified-evidence>'

python -m tg_lottery.ops.reconcile_invite_connection \
  --outbox-id <uuid> --runtime-ref '<original-sha>:aiogram=<version>:aiohttp=<version>' \
  --repair-ref '<incident-reference>' --unredirected-evidence-ref '<verified-evidence>' \
  --expected-fingerprint <preview-fingerprint>
```

只通过已授权的生产控制台执行，工具不调用 Telegram。应用后独立回读 Outbox、原 Attempt、
审计和生产巡检。历史活动已截止/开奖时，不重试创建、不改变快照或补记参与/加成。
巡检恢复表示没有该误分类未决项，不表示真实邀请成功或所有业务流程已验收。

## 已确认临时提醒的删除恢复

对于结果未知的 `delete_message`，可以不推断原调用是否发送，而是重新执行同一群、同一消息
的删除。该路径只接受唯一匹配的已确认临时提醒、Telegram 回执中的相同消息 ID、已投影的
15 秒删除任务、原删除请求和未过期的目标；预览指纹绑定上述证据。应用时保留原 UNKNOWN
Attempt，审计明确记录原结果仍未知，并将同一删除请求交回 Worker。再次成功后，才能确认删除；
再次明确拒绝或结果未知时仍保留 UNKNOWN，不把之前的未知事实改为明确失败。

```bash
python -m tg_lottery.ops.reconcile_notice_connection \
  --outbox-id <uuid> --runtime-ref '<original-sha>:aiogram=<version>:aiohttp=<version>' \
  --repair-ref '<incident-reference>' --retry-delete

python -m tg_lottery.ops.reconcile_notice_connection \
  --outbox-id <uuid> --runtime-ref '<original-sha>:aiogram=<version>:aiohttp=<version>' \
  --repair-ref '<incident-reference>' --retry-delete --expected-fingerprint <preview-fingerprint>
```
