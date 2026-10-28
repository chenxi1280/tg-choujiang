# 历史 UNKNOWN 投递风险知悉

本机制只记录明确授权的人工风险知悉，不判定 Telegram 是否收到请求，不改变 Outbox、原始 Attempt、业务事实或消息，也不重发。临时提醒是否曾发送、是否已删除和参与者是否已读均可能仍未知。健康工作流的成功结论表示当前没有未知悉的新问题；已知悉 UNKNOWN 仍在每次运行的警告和摘要中逐条显示。只读取绿色结论的外部系统可能忽略警告，应同时读取摘要或警告。

## 安全边界

- 只允许 `send_progress_notice` 和 `send_temporary_reminder`，且每个目标必须仍为 `UNKNOWN_HOLD`，只有一条对应的 UNKNOWN `TelegramNetworkError` Attempt，请求哈希、原错误、时间和租约均通过核验。
- 操作前冻结逐条清单，明确批准具体记录、数量和业务风险；清单必须包含 Outbox、请求、来源及原 Attempt 的身份和时间。预览指纹绑定清单 SHA-256 和当前事实，应用时在行锁内重新核验；任何变化使整批事务失败。
- 每条审计保存事件引用、清单 SHA-256、原 Attempt 身份与事实指纹、请求身份指纹和完整状态指纹。原 Attempt 仍为 UNKNOWN，不伪造确认回执；重复应用同一清单和指纹是幂等的。
- 指纹用于检测证据的意外变化，不是抵御具有数据库写权限者篡改的签名；数据库权限和审计访问控制仍须独立保护。
- 健康检查在只读重复读事务中核对全部未知记录和风险审计。没有匹配审计的新 UNKNOWN、缺失的审计、被删除或变化的已知悉记录，以及额外投递尝试都会使工作流失败。即使后续尝试获得成功回执，也不能单凭它证明原始 UNKNOWN 请求的结果；状态变化须重新核查。
- 已知悉记录未变化且没有其他错误时，输出 `STATUS=DEGRADED_ACKNOWLEDGED`、GitHub 警告及每条 Outbox ID、用途、原 Attempt ID、开始时间和事件引用；任何未知悉记录、失效审计、确认未投影、失败任务、失败 Update、进度通知/临时提醒/删除请求的明确失败或检查自身错误均返回非零。其他用途的 `PRE_ACCEPT_FAILED` 不在此检查项内。

## 预览和应用

部署包含该机制的版本后，使用经过授权和核验的 JSONL 清单，将其作为标准输入送入生产 Worker。预览返回 `fingerprint`；核对清单哈希、数量、每个 ID 和用途后，以完全相同的清单、引用、数量和预览指纹应用。清单和输出可能含内部身份或事件信息，保存在受限目录，不应包含凭据、邀请链接或加密密钥。

```bash
docker exec -i tg-lottery-worker python -m tg_lottery.ops.unknown_review \
  --manifest - --incident-ref <review-reference> --expected-count <count> < approved.jsonl

docker exec -i tg-lottery-worker python -m tg_lottery.ops.unknown_review \
  --manifest - --incident-ref <review-reference> --expected-count <count> \
  --expected-fingerprint <preview-fingerprint> < approved.jsonl
```

应用后应通过独立只读查询核对每条审计、原始 UNKNOWN Attempt 和 Outbox 状态，再运行 Production Health，确认摘要和警告完整，且没有新增未知记录。若目标变化、预览不符、任一检查报错或风险未经授权，停止应用；不要为使 Action 通过而删除记录或降低新增问题的严重性。
