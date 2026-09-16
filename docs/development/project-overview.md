# 项目结构与核心机制说明（全量通读记忆）

> 本文是对整个代码库**全量逐文件通读**后形成的长期记忆，供后续会话直接复用，避免重复阅读。
> 形成日期：2026-09-14。**所有结论均已直接核对源码**；未验证项在文末第 11 节集中标注。
> 规模基线（`find`/`wc` 实测）：`src/` 154 个 `.py`；`telegram/` **60 个文件 / 10,784 行**；`application/` 48 个文件 / 8,502 行；`tests/` 78 个 `test_*.py` + 3 个辅助文件 / 12,334 行 / **395 个测试函数**；alembic **20 个 revision**；数据库 **40 张表**。

## 1. 项目是什么

`tg-choujiang`（包名 `tg-lottery`）是一个**可审计的 Telegram 群抽奖机器人**。群主发起抽奖，群成员报名，靠**群内发言活跃度**和**邀请新成员**赚取额外抽奖机会，到点自动开奖。

- 技术栈：Python 3.12 + aiogram 3 + SQLAlchemy 2 + Alembic + PostgreSQL 16，依赖用 `uv`（`uv.lock`）。
- 线上身份：`@TGCCJBOT`；产品对标商业机器人 `@LotteryBestBot`。
- 文档：`doc/`（V1–V1.8 PRD，docx）、`docs/development/`（V1.7–V2.6 设计合同）、`.planning/`（15 个迭代目录）、`task_plan.md` / `findings.md` / `progress.md`。

## 2. 架构分层与依赖方向

```
telegram/  jobs/  ops/        ← 适配器（inbound / worker / 运维）
        ↓
application/  (48 个用例服务)  ← 事务边界 + 用例编排 + 授权 + 幂等
        ↓
domain/                        ← 纯领域：enums / rules / draw / transitions / errors / public_refs
infrastructure/db/             ← SQLAlchemy ORM + UnitOfWork
presentation/                  ← 纯渲染，无 IO
```

依赖单向向内；`domain/` 与 `infrastructure/` **不反向依赖** `application/`。

**唯一显式 Port**（`application/ports.py`，15 行）：只有 `UnitOfWorkPort` 协议与 `UnitOfWorkFactory` 别名。

- 写服务：注入 `uow_factory`，用例内 `async with self._uow_factory() as uow:`。
- 读服务（`queries` / `participant_records` / `query_invites` / `group_bonus_progress` / `publication_queries`）：注入 `session_factory`，只读不持锁，`expunge` 后返回游离对象。
- **没有为 Telegram / 时钟 / 随机数定义 Port**。应用层**从不直接调用 Telegram API**，只写「待发送意图」到 `telegram_outbox`。这是全项目最重要的解耦决策。

**三种横切副作用工厂**（`application/records.py`，82 行），与业务变更**同一事务**提交：

| 函数 | 目标表 | 要点 |
|---|---|---|
| `add_job()` | `scheduled_jobs` | 延时任务 |
| `add_outbox()` | `telegram_outbox` | 自动写 `request_hash=canonical_hash(payload)` |
| `add_audit()` | `audit_logs` | 自动带 `source_update_id=current_update_id()` |

**请求上下文传播**（`request_context.py`，34 行）：`ContextVar` 把 `(bot_id, update_id)` 穿透调用栈，使幂等性无需显式传参。`inbox._process` 用 `bind_update_context()` 包裹 dispatch。

## 3. 抽奖生命周期

状态机在 `domain/transitions.py`（34 行），`require_campaign_transition()` 强制校验：

```
draft → publishing → scheduled → running → settling → ready_to_draw → drawing → drawn → archived
                 ↘ cancelled ↗（多数状态可取消）      paused ⇄ running
```

`DRAWING` 只能到 `DRAWN`；`ARCHIVED` 无后继；`CANCELLED` 只能到 `ARCHIVED`。

1. **创建**：`/new` 或 `nav:create` → 草稿面板。
2. **发布**：冻结 `prize_plan_hash` 与规则（`rule_revisions.frozen=True`）；影响票数的规则不可原地改。
3. **参与**：群内点按钮直接报名（v2.0 起无需跳私聊），当场校验准入资格，结果分 `eligible / ineligible / unknown`，**无法校验绝不默认通过**。
4. **攒票**：append-only 的 `ticket_ledger`，四种 `TicketSource`：`REGISTRATION`(+1)、`POPULARITY`(发言)、`INVITATION`(邀请)、`REVERSAL`(回退负数)。
5. **结算**：`settling` 检查 7 项 drain 前置条件，全满足才生成不可变 `draw_snapshots` + `draw_entries`。
6. **开奖**：`sha256-weighted-v2` 加权抽取，每奖品名额一轮，默认 `unique_winner`。

**票数规则的默认值**（`domain/rules.py`）：发言默认 10 条有效消息 +1 票、每日 1 票、文本去空白后 ≥5 字符、同群 5 分钟内相同文本只计一次；上限常量 `MAX_ACTIVITY_MESSAGE_THRESHOLD=10000`、`MAX_INVITE_COUNT=100000`、`MAX_INVITE_BONUS_TICKETS=10000`。

## 4. 六个核心机制

### 4.1 开奖可验证性（commit–reveal）

`application/draw_commitments.py`（90 行）+ `domain/draw.py`（49 行）：

- 截止前 `secrets.token_hex(32)` 生成种子，**只存** `sha256` 承诺哈希（`sha256-commit-v1`）到 `draw_commitments`。
- 开奖时揭示并 `compare_digest` 校验 → `revealed_at`。
- 抽取：`sha256(f"{seed}:{round}:{nonce}")` **拒绝采样**取模（`limit = 2^256 - (2^256 % total)`），保证无偏。
- `draw_rounds` 落 `range_start / range_end / selected_value / selection_participant_id`，可离线复算。
- **仅 v2.4 合约强制**（`requires_draw_commitment()`：`contract_version in {"v2.4"}`）；v1/v2.1–v2.3 走 `secrets.token_hex(32)` 无承诺。

### 4.2 数据库先行的 Update Inbox

`telegram/polling.py`（281 行）+ `telegram/inbox.py`（154 行）：

- `poll_once` 在**同一事务**里：insert `telegram_updates`（`on_conflict_do_nothing` by `(bot_id, update_id)`）+ upsert `polling_offsets.last_durable_update_id` + 续租 `bot_runtime_leases`(fence)。**先落库再确认 offset**。
- `_claim_next` 用 `with_for_update` + **`NOT EXISTS`（更早未 PROCESSED 的 update）** 保证**严格按 `update_id` 串行**。
- `_PROCESSING_STALE_SECONDS = 300`（僵死可抢占）、`_FAILED_RETRY_SECONDS = 5`。`_locked_claim` 用 `attempts` 做乐观所有权校验，失配抛 `RuntimeError`。
- **间隙检测**：batch 首条 `update_id >` 期望 next_offset 时写 `integrity_status=integrity_review_required`（`_update_gap()`），`healthcheck --role app` 因此报错。宁可暴露丢更新也不静默继续。
- payload 为 None 时标 FAILED（`"update payload was pruned before processing"`）。

### 4.3 事务性 Outbox 与投递确定性

`telegram/outbox.py`（432 行）：

- `business_key` 唯一约束 = 幂等键。
- **`gateway_started_at` 闸门**是正确性基石：`_mark_gateway_started` 之后才真正调 Telegram。因此 `_recover_ambiguous` 能区分「没发出去」（可安全重投）与「可能已发出」（一律 `UNKNOWN_HOLD`，**绝不自动重发**）。
- 三类确定性：`CONFIRMED` / `EXPLICIT_REJECTION`(`PRE_ACCEPT_FAILED`) / `UNKNOWN`(`UNKNOWN_HOLD`)，每次尝试写一行 `delivery_attempts`。`_EXPLICIT_ERRORS`=(BadRequest, Forbidden, Unauthorized)；`_UNKNOWN_ERRORS`=(Network, ServerError, Timeout, ConnectionError)。
- `CONFIRMED` 后第二阶段 projector（`outbox_projection.py`，257 行），失败独立 30s 重试（`_PROJECTION_RETRY_SECONDS`），成功才置 `projected_at` 并清空 `encrypted_remote_secret`。`_PROJECTED_OPERATIONS` = 5 种（send_campaign_card / create_invite_link / revoke_invite_link / create_required_chat_invite / revoke_required_chat_invite）。
- 租约：`_CLAIM_LEASE_SECONDS = 120`，`_LEASE_RENEW_INTERVAL_SECONDS = 30`，由 `infrastructure/heartbeat.py` 的 `run_with_heartbeat`（65 行，取消安全）驱动。
- `gateway.py`（466 行）是**唯一出网点**，按 operation 分发。

### 4.4 幂等与租约

- `business_key` 唯一约束贯穿 `ticket_ledger` / `scheduled_jobs` / `telegram_outbox`。
- worker 抢占全部 `FOR UPDATE SKIP LOCKED` + lease + fence；`_locked_*` 校验 owner 与 state，失配抛 `"ownership lost"`。
- `worker_heartbeats`（UPSERT）+ `healthcheck.py` 存活检查（polling/worker 最大 180s）。
- 邀请加成用**目标值减当前值**的差额模型（`reconcile_invitation_bonus`），天然幂等。
- `maintenance.reconcile_ticket_caches` 按账本重建 `ticket_total_cache` 并写审计；**账本才是唯一真相**。

### 4.5 邀请链接密文存储

`telegram/crypto.py`（25 行）：`SecretCipher` = AES-256-GCM，12 字节 nonce 前置，密钥须 base64url 解出恰好 32 字节。

加密对象**只有 Telegram 邀请链接明文**（泄露 = 任意人可进群）：

- `invite_links.encrypted_url`
- `campaign_participation_groups.encrypted_join_url` / `campaign_requirements.encrypted_join_url`
- `telegram_outbox.encrypted_remote_secret`（仅 `CONFIRMED` → `projected` 之间的中转态）

**AAD context 绑定**（`campaign_targets._qualification_secret_context` / `required_chat_links`）：
- 多群合约：`campaign-target:{campaign_id}:{kind}:{row_id}`
- legacy：`required-chat:{campaign_id}:{chat_id_snapshot}`
- `invite-link:{id}` / `outbox-result:{id}`

防止密文被搬到另一行复用解密。`ops/reconcile_legacy_outbox.py` 只把 `UNKNOWN_HOLD` 改 `CANCELLED`（同时写 `last_error`、清租约），**不重发**。

### 4.6 隔离运维 Bot

`ops_bot.py` / `ops/winner_console.py` / `application/winner_authorization.py`（34 行）：

- 正式 Bot **不注册**任何指定中奖命令。
- `ops_bot._require_isolated_bot_id`：bot_id 已存在于 `polling_offsets` 则拒绝启动，防止抢走长轮询消费者。只注册 message handler、`allowed_updates=["message"]`、只接受私聊。
- 授权双通道：`OPS_ADMIN_IDS` 环境变量 **或** `winner_designation_operators` 表（`enabled=true`，`with_for_update(read=True)`）。
- 命令 `/campaigns`、`/winner_list`、`/winner_set`、`/winner_del`，**只写 `winner_designations`，不触发截止/复核/结算/开奖**。
- `winner_console.py` 是同一 handler 的 CLI 版（`--actor` + command），`source_bot_id=None` → 审计 source 记 `offline_ops_cli`。
- 被指定者仍须通过开奖前冻结资格复核；`drawing._designated_participant` 查不到**明确报错**而非静默替换。写 `draw_rounds.selection_mode="designated"`。`unique_winner` 开启时拒绝同一人被指定到多个名额。

## 5. 数据模型（40 张表，已与迁移精确核对）

**已裁定：40 张表。** ORM `__tablename__` 计数 40，迁移 `create_table` 计数 40，**双向差集为空，零漂移**。构成：初始迁移的 28 张（由 `src/tg_lottery/migrations/schema_v1_{runtime,core,draw}.py` 的 `UPGRADE_STEPS` 组合，共 1,151 行、84 处 `create_table`）+ 后续 revision 新增 12 张。

初始迁移 `20260808_0001` 的 `upgrade()` 是：
```python
steps = RUNTIME_STEPS + CORE_STEPS + DRAW_STEPS
for step in steps: step()
```
**注意**：迁移的建表逻辑放在 `src/tg_lottery/migrations/`（随包发布）而非 `alembic/versions/`，所以 `alembic/versions/*.py` 里 grep 不到 `create_table`。

按文件分组：

- `models_core.py`（274 行，15 张）：`bot_runtime_leases`、`polling_offsets`、`telegram_updates`、`groups`、`group_admins`、`user_profiles`、`user_region_changes`、`campaigns`、`rule_revisions`、`prizes`、`participants`、`qualification_checks`、`admin_sessions`、`callback_tokens`、`audit_logs`
- `models_draw.py`（206 行，11 张）：`scheduled_jobs`、`draw_snapshots`、`draw_commitments`、`draw_entries`、`draw_runs`、`draw_rounds`、`winner_designations`、`winners`、`telegram_outbox`、`delivery_attempts`、`worker_heartbeats`
- `models_engagement.py`（279 行，9 张）：`activity_events`、`daily_progress`、`campaign_referral_routes`、`invite_links`、`invitations`、`group_membership_facts`、`invitee_activity_events`、`user_group_message_totals`、`ticket_ledger`
- `models_campaign_targets.py`（4 张）：`campaign_participation_groups`、`campaign_requirements`、`qualification_target_observations`、`campaign_operational_blocks`
- `models_ops.py`（1 张）：`winner_designation_operators`

关键约束：
- `campaigns` 部分唯一索引 `uq_campaigns_group_nonterminal`（`state NOT IN ('drawn','archived','cancelled')`）——同群同时只允许一个非终态活动（legacy 路径）。
- `telegram_updates` 唯一 `(bot_id, update_id)`；`activity_events` 唯一 `(chat_id, message_id)`；`participants` 唯一 `(campaign_id, user_id)`；`draw_entries` 唯一 `(snapshot_id, participant_id)`；`draw_runs` 唯一 `(snapshot_id, algorithm_version)`。
- `ticket_ledger.business_key` 唯一；`scheduled_jobs.business_key` 唯一。

## 6. Worker 与 Job 系统

`jobs/worker.py`（178 行）+ `handlers.py`（333 行）+ `maintenance.py`（202 行）+ `qualification_review.py`（356 行）：

**12 种 `job_type`**：`campaign_start`、`campaign_cutoff`、`qualification_review`、`validate_invitation`、`revoke_invite_links`、`campaign_settle`、`campaign_draw`、`full_draw_verify`、`delete_telegram_message`、`telegram_update_retention`、`ticket_cache_reconcile`、`full_draw_reconcile`。

异常 → 状态映射（`JobWorker._execute`）：`JobRetry`→`RETRY_SCHEDULED`（可按 `delay_seconds`）、`JobBlocked`→`BLOCKED`、`ValueError`→`FAILED_TERMINAL`、`CancelledError`→re-raise、其它→30s 重试。lease 120s，心跳 30s。`_claim_next` 用 `run_at <= now` + `(PENDING|RETRY_SCHEDULED)` 或过期 `CLAIMED`，`with_for_update(skip_locked=True)`。

三个维护任务：`prune_processed_update_payloads`（7 天）、`reconcile_ticket_caches`、`reconcile_full_draws`（60s 一轮）。`ensure_maintenance_jobs` 用 `pg_advisory_xact_lock(hashtext(job_type))` 防重复排布。

**`qualification_review` 的降级策略**：先 `preflight_membership_targets` 预检；失败时 `_degrade_review` 按 `"last_persisted_final_else_ineligible"` 策略处理（审计 `qualification.review_degraded`），重试上限 `QUALIFICATION_RETRY_LIMIT=3`。`QualificationRateLimited` → `JobRetry(delay=retry_after)`。

## 7. Telegram 层（60 文件）要点

### 7.1 分发链路

`update_handler.handle()` 是互斥 if 链：`callback_query` → `chat_join_request` → `chat_member` → `my_chat_member` → `message`。

`_message()`：先查 `migrate_to_chat_id`（群升级）→ 私聊进 `_private_message` → 群聊进 `_group_command`（仅 `/getid`，含 mention 校验）或 `stats.record() + activity.record_message()`。

**深链 6 种**（全部走 `/start`，`_start_payload()` 按前缀顺序匹配）：`join_`、`rules_`、`invite_`、`bonus_`、`ref_`、`condition_`。

### 7.2 回调双契约（**已核实，两者均生效**）

- **`m:` 旧前缀**（4 个：`m:bo:` / `m:id:` / `m:ad:` / `m:ir:`）：纯字符串拼接，**不经过 `callback_tokens`**，无一次性/无过期语义。服务**参与者**（只操作本人数据，查询一律带 `query.from_user.id`）。
- **`secure:` 新前缀**：走 `callback_tokens` 表，DB 只存 SHA-256 哈希，`consume()` 做四重校验（owner / chat_id / 过期 / **草稿三元组 `(draft_id, draft_revision, panel_message_id)`**）。服务**发起人**（每个 action 开头都 `_verify_admin()`）。
- **幂等重放**：`consumed_at` 非空时若 `consumed_by_update_id == current_update_id()` 则放行（为 inbox 重试设计）。
- **分界线**是「操作是否需要重新验证 Telegram 管理员身份」。

`AdminSession` 三元组机制：任何面板重渲染令 `draft_revision` 自增，使该次渲染前铸出的 token 全部失效——解决陈旧回调竞态。

### 7.3 权限模型（三层）

1. `admin_authorization.verify_chat_admin()`（24 行）——**唯一授权实现**，每次实时查 Telegram，同时校验操作者与机器人；`require_invites` 可选。
2. `creator_bonus_permissions.verify_bonus_action()` / `verify_publication_bonus_permissions()`——按 action 推断目标群后逐个复核。
3. `member_bonus_permissions.inspect_bonus_permissions()`——**只读探测，不阻断**，供 UI 标注 ⚠。

**关键分离**：`groups.sync_admin` 存的缓存**仅用于筛选可管理群列表**；授权判定永远实时走 `verify_chat_admin()`。

## 8. 测试套件实况（82 文件 / 395 函数）

**真实性**（`grep` 实测）：

- **43 个文件**使用 `session_factory`（真 PostgreSQL，asyncpg）；conftest 预检 `SELECT 1`，teardown 全表 `TRUNCATE ... RESTART IDENTITY CASCADE`。
- **35 个 `test_*.py`** 完全不含 `session_factory`（纯逻辑/UI/文案测试）。
- 真并发/租约/恢复测试共 **6 条**，质量最高的两条：
  - `test_invitation_bonus_concurrency.py::test_parallel_invite_rewards_preserve_participant_ticket_cache`——**真跨事务行锁竞争**，核心断言 `assert second.done() is False` 证明 participant 行锁生效。
  - `test_runtime_integrity.py::test_late_outbox_success_cannot_overwrite_unknown_recovery`——迟到成功不得把 UNKNOWN_HOLD 改回 CONFIRMED。
  - 另有 `test_outbox_projection.py::test_running_outbox_delivery_renews_lease`（BlockingGateway + Event + lease=1s/renew=0.2s）、`test_runtime_integrity.py::test_two_workers_claim_distinct_jobs`、`test_running_job_renews_lease_and_cannot_be_reclaimed`、`test_expired_job_owner_cannot_finish_reclaimed_job`。

**覆盖充分**：Outbox/投递、Draw、Invitation、Qualification、Runtime 完整性。
**薄弱**：Migration（20 revision 仅 2 个测试）、启动编排（`bootstrap.py`/`app.py`/`worker.py` 零直接测试）、通知子系统（`campaign_notifications.py` 零测试）、UI 文案层（35 个无真库文件多为 `contains` 断言）。

**drift 检测的真相（重要纠正）**：`tests/` 与 `scripts/` 中**确实没有** drift 测试；但 **CI 跑 `alembic check`**（`.github/workflows/python-checks.yml:51` 与 `deploy-production.yml:58`，均在 `alembic upgrade head` 之后）。所以漂移防护**由 CI 提供，不是本地脚本**——`scripts/check_code_metrics.py` 只做文件/函数行数、复杂度、嵌套、位置参数检查。

## 9. 入口、配置与部署

四个 console script（`pyproject.toml`）：

| 入口 | 模块 | 作用 |
|---|---|---|
| `tg-lottery-app` | `app.py` | 长轮询 + Inbox（TaskGroup 两协程） |
| `tg-lottery-worker` | `worker.py` | JobWorker + OutboxWorker |
| `tg-lottery-ops-bot` | `ops_bot.py` | 隔离运维 Bot |
| `tg-lottery-healthcheck` | `healthcheck.py` | `--role app\|worker` |

`docker-compose.yml`：`app` + `worker` 常驻（各带 healthcheck），`ops-bot`（profile `ops`）与 `migrate`（profile `tools`）按需；外部网络 `infra_default`。

部署：`master → release → GitHub Actions → GHCR → 马来西亚服务器`。生产机不改代码；`/data/tg-lottery/releases/<release_id>` 不可变，`current` 仅在迁移+启动+双健康检查通过后原子切换。迁移前自动备份 PostgreSQL（保留 14 份）。

`config.py`（84 行）关键校验：`BOT_TOKEN` 必须含数字前缀 bot id 且与 `BOT_ID` 一致（`derive_bot_id_and_validate_polling`）；`POLL_LEASE_SECONDS ≥ POLL_TIMEOUT_SECONDS + 10`；`APP_ENCRYPTION_KEY` 长度 43–44；三个频道 URL 必须 `https://t.me/`。

**Bot 命令**：`/start` `/menu` `/new` `/index` `/join` `/region` `/myid`（私聊）+ `/getid`（群）。

**本地校验**（README）：`ruff format --check` → `ruff check` → `python scripts/check_code_metrics.py` → `alembic upgrade head` → `alembic check` → `scripts/run_pytest_shard.py --index 0..7 --count 8`。`run_pytest_shard.py` 的 60s 超时是**整个分片**（`subprocess.run(timeout=60)`），不是每个测试。

## 10. 全量通读发现的代码问题（已逐条验证）

### 🔴 高：可能影响线上行为

**1. 群聊回调错误提示可能被静默吞掉**（`update_handler.py:184-193` + `member_callbacks.py:261-265`）

`_callback()` 对**除** `member:join:` / `member:bonus:` / `member:rules:` 外的所有回调**先 `query.answer()`**，之后才 dispatch。若 dispatch 抛错且是群聊，`_callback_error` 走 `safe_answer(query, ..., alert=True)` —— **第二次 answer**，Telegram 返回 `TelegramBadRequest`，被 `safe_answer` 的 `except TelegramBadRequest: pass` **完全静默**。结果：群聊中回调失败时用户可能收不到任何反馈。

**已核实测试未覆盖**：`test_update_handler.py:329 test_callback_error_answers_alert_without_group_message` 用的正是 `member:join:`（在白名单内，跳过预 answer），因此**恰好绕开了这条路径**。

### 🟠 中：契约/一致性问题

**2. `guided` 引导流程是死代码**（`campaign_creator.py`）——**已彻底核实**：`_new_draft` 在 `:120` 设 `"guided": False`；`_set_state` 的**全部 6 个调用点**（`:195/:205/:265/:291/:310/:325`）都传 `guided=False`；其余写入点（`:330/:371`）也强制 `False`。唯一可能写 `True` 的是 `:399`，而它只接收调用方传入值，调用方全是 `False`。因此 `:236` 与 `:259` 的 `if updated.get("guided")` 分支及 `_advance_guided()`（`:241`）**永不可达**。

**3. `/help` 不在群聊 `_PRIVATE_COMMANDS` 中**（`update_handler.py:31-33`）——集合是 `{/start, /menu, /new, /index, /join, /region, /myid}`，含未注册为 BotCommand 的 `/menu`，却**缺 `/help`**。群里发 `/help` 无任何响应。

**4. `_is_member()` 逻辑三处重复**：`update_handler.py:480-484`、`qualification.py:233-237`、`member_referral_landing.py:19/54-55`（第三处结构还不同）。语义漂移会直接体现为票数不一致。

**5. 草稿过期提示与实际 TTL 不符**：`update_handler.py:331` 提示「草稿已超过 7 天」，但 `AdminSessionService._SESSION_TTL_HOURS = 24`、`CreatorRenderer.TOKEN_LIFETIME = 24h`。**没有任何一处是 7 天**。

**6. 报名成功票数为 0 时硬编码兜底为 1**（`member_flow.py:151-153`）——与 `registration` 基础票数解耦，当前默认 1 故无影响，属隐性耦合。

**7. `_resolved_data()` 的 `participation_groups[0]` 无防护**（`creator_publication.py:203`）——空列表时抛 `IndexError`，**不是 `DomainError`/`ValueError`**，因此 `_private_message` 的 except 不会捕获，会记 FAILED 重试。缓解：`CreatorGroupPicker.done()` 有非空校验，但 `draft_load_last` 路径绕过它。

**8. `condition_` 深链用 `rpartition("_")` 切分**（`update_handler.py:178`）——`public_ref` 由 `token_urlsafe` 生成，字母表**含 `_`**。`isdecimal()` 校验提供一层保护，属边界脆弱性而非安全问题。

**9. `format_public_time()` 签名与行为不符**（`public_projection.py:53-64`）——参数名 `timezone`，但输出**恒为北京时间**（两次 `astimezone` + 固定标签）。所有调用方传 `campaign.timezone` 会误解。

**10. `_group_buttons()` 串行 N+1**（`creator_renderer.py:125-131`）——每群 3 次 TG 调用，单页（≤6 群）最多 18 次串行 await，未用 `asyncio.gather`，未复用 `SmoothRateLimiter`。

**11. 跨模块导入私有名**：`creator_settings.py:30-40` 从 `creator_settings_ui` 导入 5 个下划线函数（且与 `campaign_creator.py:431-452` 有 4 对重复实现）；`member_bonus_views.py:12-19` 从 `member_group_invites` 导入 6 个私有名。

### 🟡 低：死代码

**12. `creator_participation.validate_main_threshold()`**——`src/` 零调用（**已核实**），仅 `tests/test_creator_settings.py:157/161` 引用。

**13. `MemberFlow._send_invite_link_to()`**（`member_flow.py:486-488`）——`src/` 与 `tests/` **全无引用**（**已核实**）。

**14. `member:rules:` 已废弃**（`member_callbacks.py:255-258`）——固定回复「请点击新版卡片」，参数未使用，但仍在预 answer 白名单中。

**15. `member:tickets` / `member:invites` 裸形式语义分叉**（`member_callbacks.py:57-59`）——带 ref 是明细/记录，裸形式却落到 `show_joined()`（我参加的抽奖列表）。

### ⚠️ 子代理报告中的错误结论（**经我核实为误**）

- **`render_groups` 不是死代码**。子代理称「整条调用链疑似残留」，但 `creator_group_picker._render_groups`（`:148`）确实调用 `creator_renderer.render_groups`（`:149`），且 `_render_groups` 被 `show()`（`:40`）与 `_save_and_render()`（`:146`）调用——**均为活路径**。

## 11. 两个待查风险点的最终裁定

**（1）payload 7 天裁剪 vs 对账工具时间窗 —— 风险成立，但失败安全**

- `maintenance.prune_processed_update_payloads` 只把 **PROCESSED 且 `processed_at <= now-7d`** 的 `telegram_updates.payload` 置 NULL，**无任何豁免**（已核实无保留逻辑）。
- `ops/reconcile_pinned_cards.py:67-69` **需要 `update.payload`** 来读 pinned_message；`update.payload is None` 时 `raise ValueError`。
- 由于 pinned-message update 正常会被 PROCESSED，该对账工具**实际只能在 7 天窗口内使用**。
- **失败安全**（抛错而非静默错误），但属可恢复性限制。
- **测试未覆盖二者交互**：`test_runtime_integrity.py:155` 测裁剪、`test_remote_receipt_recovery.py` 测对账，**从不在同一测试中组合**。

**（2）新旧回调契约并存 —— 成立，且边界清晰可接受**

`secure:`（发起人，有 token 与管理员复核）与 `m:`（参与者，无 token，只操作本人数据）**都真实生效**。`m:` 明文暴露 `public_ref` 与 `chat_id`，但篡改后只访问本人记录（查询含 `query.from_user.id`），越权需 22 字符 base64url 碰撞，不可行。属**有意保留的历史契约**，非缺陷。

## 12. 开发流程特点

- 文档驱动：每版本独立 PRD，明确标注 `implementation` / `automated_verification` / `deployment` / `telegram_e2e` 四个状态位。
- **严格区分「本地测试通过」与「线上验收通过」**。V2.5/V2.6 功能已本地实现并通过约 390 项回归，真实 E2E 与生产发布仍在进行中。
- 代码度量门禁：`ruff` 含 `C90`（mccabe max-complexity=10）+ `scripts/check_code_metrics.py`（文件 ≤500 行、函数 ≤50 行、复杂度 ≤10、嵌套 ≤3、位置参数 ≤3）。
- 无 TODO/FIXME 注释残留（全目录 grep 未发现）。

## 13. 未验证项（诚实标注）

1. **未运行任何测试**。本机 `127.0.0.1:55432` **CLOSED**，`docker ps` 无 PostgreSQL 容器，`TEST_DATABASE_URL` 未设置。395 个测试**一个都没执行过**，通过率未知。第 10 节所有问题均为**静态阅读结论**，未经运行时复现。
2. **`check_code_metrics.py` 实际运行通过**（`Code metrics passed`，exit 0）——这是唯一实际执行的验证命令。
3. **`alembic check` 未实际运行**（无数据库），仅确认 CI 配置中包含。
4. 第 10 节问题 7（IndexError 可达性）确认了代码路径存在，但**未构造完整复现**。
5. 第 10 节问题 10（N+1 影响）为静态调用计数，**未实测延迟**。
6. 各版本 PRD 之间的**精确覆盖关系**未逐条比对（仅抽样阅读开头）。
7. `docs/development/` 与 `doc/`（docx）中早期版本的细节未全量阅读。
