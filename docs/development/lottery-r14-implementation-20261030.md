# R14 实施与验证记录

日期：2026-10-30（北京时间）。依据：[R14 PRD](lottery-experience-optimization-prd-20261029.md)。

状态：独立审查发现的三项问题已完成本地代码修复；本轮按用户要求暂不运行测试。下方自动化结果和源码清单属于此次审查修复前的快照，不能作为当前修复已通过测试的证明。未发布、未部署、未触发真实 Telegram 操作。

## 独立审查后修复（本轮未测试）

1. 暂停中的开始通知保留原Outbox和业务键，发送前转为可恢复的延后状态，不写成永久取消。已确认规则页在暂停时仍形成下一页意图；恢复活动后，在同一业务事务中唤醒已有暂停项并补齐已有开始意图的授权目标。发送前继续复核截止、当前状态和原公告确认；不重发已确认页，不重置首次开始时间，不为历史活动新建开始意图。
2. `send_channel_shortcut`、`send_start_card`和`send_start_rules`接入原有单次通知的连接前失败分类。只在`PreRequestConnectionError`带明确连接失败原因时按原5次退避策略重试，耗尽后保留明确失败；超时等未知结果仍为UNKNOWN，不扩大重发范围。
3. 详情及进度命令共用的任务摘要按状态和窗口展示：已截止、结算、开奖、归档或取消时保留累计发言加成、累计达标天数和邀请记录，不显示新的今日任务或下一轮差额；旧活动发言加成读取原账本累计。暂停/未开始时不承诺当前发言计奖，邀请窗口已过时只展示历史及待核验记录，未关闭的邀请仍按原规则处理。

本轮追加修复Worker租约并发边界：

- 暂停延后、开始卡取消、频道辅助入口取消、旧进度删除取消均在行锁内重新读取Outbox，核实CLAIMED状态、领取身份、领取时租约截止时间、租约有效期及网关尚未启动。旧领取失效时记录原因并退出，不覆盖其他Worker的领取、发送或确认事实。
- 网关启动前再次核实同一领取快照；发送前参数错误落库也使用同一保护。已开始请求后的确认与UNKNOWN处理继续沿用原事实保存路径，不把发送前保护套到已发送结果上。
- 恢复活动仅唤醒未持有租约、网关未启动且因暂停延后的通知，保留原幂等键。没有数据库迁移，没有运行测试、连接数据库或发布。已有直接调用发送前检查的测试夹具补齐了合法租约，但本轮未执行。
- 本轮源码复查未发现这几条发送前状态变更还有绕过共同租约校验的分支；修改文件格式/lint和差异检查通过，实际并发行为仍未运行验证。之前的测试结果不适用于这些新增修复。

本轮采用原流程内增量修复，未重写通知状态机或计奖逻辑。同步了已有测试夹具中的活动状态、开始/截止时间与固定时钟，但未执行测试。仅做源码路径核对、修改文件的格式/lint检查及`git diff --check`；没有连接测试库或生产环境。

## 方案与再审修复

采用在原创建器、成员导航、查询与 Outbox 上增量实现的方案，复用原资格、门槛、计数与票数服务。另一方案是重建独立参与流程，虽然界面隔离更直接，但会重复资格/账本逻辑、增加旧活动迁移成本，不适合本次保留业务的要求。

本轮再审实际修复：

1. 创建页补齐“立即开始/指定开始时间”。原页面只输入开奖时间；新开始时间必须未来且早于截止，并继续校验门槛完成窗口。复制不沿用旧日期，保存的开奖时刻不变。
2. 收敛旧深链、群回调、成员变化和邀请入口中的隐式登记分支：浏览/关注/查询不报名；未完成请求必须回到私聊明确点击。已报名后的资格复核保留。
3. 成功登记与待完成请求分开，人数只取成功事实；不依据缓存票数或一条参与者行推断报名成功。
4. 删除以持久生效时点T和实际发送证据分类。旧活动T后新进度20秒；T前已发取消/跳过待删任务；跨T证据不足保留待核对，不猜消息编号。
5. 完整任务面板与短消息分开，避免报名后带操作按钮的面板被短提示策略误删。主动进度命令的成功、空、失败、限频固定20秒。
6. 开始状态与发送意图同事务；原主卡未确认时稍后恢复；发送前复核当前状态/截止/原公告确认及目标，历史running不补发。
7. 精选频道沿原独立推送链，在新单场卡确认后追加一次辅助入口；每日汇总、旧卡与状态编辑不追加，不向仅关注条件的频道扩散。
8. 详情首页补今日发言加成、轮次/差额及邀请摘要，原各群门槛、累计账本、邀请记录及结果保留。统一两类加成名称，旧人品命令仍查原发言范围。
9. 查询投影只读；纯文字别名沿原普通消息统计链裁决，斜杠命令沿原排除规则；翻页不计发言。明确这一点以避免新增入口悄悄改动原计数规则。
10. 消息设置页打开时逐目标检查删除权限，兼容仅有chat_id的旧草稿并实时核实聊天类型，区分机器人自己的短提示和用户报名命令；普通成员身份不被误判成不能删除机器人自己的群消息。权限异常显示未知及检查时间，不伪装全部可删。
11. 报名失败反馈明确区分“报名待完成／核验并报名”和“已报名、当前资格未通过／核验并刷新”，保留后者原登记与进度；个人进度增加总体资格状态。✅/❌/⏳状态按钮不再叠加功能图标。
12. 管理页独立显示辅助入口未完成，提供带管理员、状态及版本校验的“重试失败通知”。只恢复明确未送达的频道辅助消息和开始通知原Outbox，保持原幂等键；不重发主卡、已确认续页或UNKNOWN。发送辅助入口前再次核对原公告授权；坏回调给出可理解反馈，条件分页保持从第一页开始。

第10–12项来自再次逐条反查PRD第5、7.3、7.4节的实现缺口；没有增加新的奖励计算或派奖权限。删除权限采用“打开设置时实时检查”，另一方案“只显示旧权限快照”成本较低，但不能反映当前权限变化，因此未采用。

## 改变旧行为的项目

对应[实施前核对表 C01–C14](lottery-prd-feature-crosscheck-20261030.md)：说明默认值、消息保留策略、成功/待完成列表、命名/布局/图标、频道辅助入口、群活动查询、公共人数、减少重复规则发送、综合进度命令与完整开始卡均在本轮实现。报名明确点击属于原合同修复；发言和邀请计算、开奖资格、中奖快照不重写。

原分享按钮没有移除。未报名详情与已报名详情保留分享卡入口；私聊卡、选择群/频道、长按转发与个人邀请复制/打开/转发保持各自职责。普通宣传不自动携带个人邀请归因。

## 56项逐项核对

“自动”表示具备所列自动测试证据来源，最终运行结果统一见下文；不表示每个客户端场景已实测。“源码”表示本轮对路径/字段进行了人工核对。客户端待验项不能据此结项为线上通过。

| AC | 实现/保留结果 | 主要位置（src/tg_lottery 下） | 验证来源（tests 下） | 验证层次 |
| --- | --- | --- | --- | --- |
| AC01 | 默认说明、编辑/清空、旧稿恢复与复制 | `draft_config.py / creator_settings.py` | [test_r14_views.py](../../tests/test_r14_views.py), [test_draft_config.py](../../tests/test_draft_config.py), [test_template_schedule.py](../../tests/test_template_schedule.py) | 自动 |
| AC02 | 链接摘要、完整规则按需分页、原面板刷新 | `start_payloads.py / member_navigation.py` | [test_v32_member_navigation.py](../../tests/test_v32_member_navigation.py), [test_start_and_draft_recovery.py](../../tests/test_start_and_draft_recovery.py) | 自动 |
| AC03 | 未满足条件不报成功，逐项状态及正确入口 | `member_condition_check.py / member_missing_targets.py` | [test_r14_views.py](../../tests/test_r14_views.py), [test_member_registration_reply.py](../../tests/test_member_registration_reply.py), [test_r14_completion_gaps.py](../../tests/test_r14_completion_gaps.py) | 自动 |
| AC04 | 明确私聊点击、资格通过登记、幂等 | `member_flow.py / application/participants.py` | [test_group_join_flow.py](../../tests/test_group_join_flow.py), [test_registration.py](../../tests/test_registration.py) | 自动 |
| AC05 | 分群门槛、未全达标有效次数为0 | `member_record_text.py / member_detail_actions.py` | [test_v30_admission_integration.py](../../tests/test_v30_admission_integration.py), [test_r14_boundaries.py](../../tests/test_r14_boundaries.py) | 自动 |
| AC06 | 跨日完成事实、每日加成、邀请待核验 | `原 admission/activity/invitations 服务` | [test_v30_admission_integration.py](../../tests/test_v30_admission_integration.py), [test_v30_event_edges.py](../../tests/test_v30_event_edges.py), [test_v30_invitation_edges.py](../../tests/test_v30_invitation_edges.py) | 自动 |
| AC07 | ✅/❌/⏳、待批准、已关注隐藏跳转 | `member_condition_check.py / participation_progress.py` | [test_r14_views.py](../../tests/test_r14_views.py), [test_r14_completion_gaps.py](../../tests/test_r14_completion_gaps.py) | 自动；客户端待验 |
| AC08 | 平台与活动目标去重、Bot启动独立 | `platform_condition_view.py / member_condition_check.py` | [test_official_two_channels.py](../../tests/test_official_two_channels.py), [test_r14_views.py](../../tests/test_r14_views.py) | 自动；真实频道待验 |
| AC09 | 公共入口同场链接，不写个人状态 | `campaign_card.py / channel_shortcuts.py` | [test_campaign_sharing.py](../../tests/test_campaign_sharing.py), [test_r14_start_and_signup.py](../../tests/test_r14_start_and_signup.py) | 自动；转发客户端待验 |
| AC10 | 新20秒及15秒/更多/关闭，旧场新进度20秒 | `retention_policy.py / creator_messages.py` | [test_r14_retention.py](../../tests/test_r14_retention.py), [test_r14_views.py](../../tests/test_r14_views.py) | 自动；实际删除待验 |
| AC11 | 删除错误、重启恢复、未知不猜编号 | `message_deletion.py / message_delete_permissions.py / outbox_preflight.py` | [test_notice_deletion.py](../../tests/test_notice_deletion.py), [test_notice_connection_recovery.py](../../tests/test_notice_connection_recovery.py), [test_r14_retention.py](../../tests/test_r14_retention.py), [test_r14_completion_gaps.py](../../tests/test_r14_completion_gaps.py) | 自动 |
| AC12 | 删除不删事实，完整详情与历史仍可回查 | `member_navigation.py / member_record_text.py` | [test_v32_member_navigation.py](../../tests/test_v32_member_navigation.py), [test_archived_participant_outcome.py](../../tests/test_archived_participant_outcome.py) | 自动；客户端待验 |
| AC13 | 普通按钮最多两列，长条件分页 | `ui_style.py / member_condition_check.py` | [test_ui_style.py](../../tests/test_ui_style.py), [test_r14_views.py](../../tests/test_r14_views.py) | 自动；移动端视觉待验 |
| AC14 | 总入口和两类加成命名，旧人品指令口径 | `group_reputation.py / member_bonus_*.py` | [test_v33_experience_unification.py](../../tests/test_v33_experience_unification.py), [test_v33_group_reputation.py](../../tests/test_v33_group_reputation.py) | 自动与源码搜索 |
| AC15 | 终态不放行新报名，保留退群复核与结果 | `member_detail_actions.py / 原资格与开奖服务` | [test_registration.py](../../tests/test_registration.py), [test_referral_deadline_experience.py](../../tests/test_referral_deadline_experience.py), [test_cancelled_base_progress_history.py](../../tests/test_cancelled_base_progress_history.py) | 自动 |
| AC16 | 七天退群仅自由说明 | `lottery_defaults.py / draft_config.py` | test_r14_views.py；源代码未新增领奖后监测/追回任务 | 自动与源码 |
| AC17 | 原频道个人响应仅本人，首次报名私聊 | `telegram/channel_shortcuts.py` | [test_r14_start_and_signup.py](../../tests/test_r14_start_and_signup.py) | 适配自动；真实原地交互未验 |
| AC18 | 全URL原卡/分享卡及个人响应失败私聊入口 | `campaign_card.py / channel_shortcuts.py` | [test_campaign_sharing.py](../../tests/test_campaign_sharing.py), [test_inline_campaign_sharing.py](../../tests/test_inline_campaign_sharing.py) | 自动；客户端转发未验 |
| AC19 | 原门槛/条数/连续天/轮次/差额/邀请/暂存保留 | `member_navigation.py / lottery_query_views.py / 原详情` | [test_r14_boundaries.py](../../tests/test_r14_boundaries.py), [test_v23_bonus_navigation.py](../../tests/test_v23_bonus_navigation.py), [test_v30_admission_integration.py](../../tests/test_v30_admission_integration.py) | 自动与字段逐项核对 |
| AC20 | 正文分区、状态与按钮图标，自定义文本不改 | `ui_style.py / 各展示模板` | [test_ui_style.py](../../tests/test_ui_style.py), [test_campaign_card_layout.py](../../tests/test_campaign_card_layout.py), [test_r14_completion_gaps.py](../../tests/test_r14_completion_gaps.py) | 自动与源码；读屏/字号未验 |
| AC21 | 竞品分区、逐群条件、奖品、编号说明、时间 | `presentation/campaign_card.py / participation_rules.py` | [test_campaign_card_layout.py](../../tests/test_campaign_card_layout.py), [test_v30_publication.py](../../tests/test_v30_publication.py) | 自动；真实客户端视觉未验 |
| AC22 | 缺失关注短提示与总进度、报名/刷新区分 | `member_missing_targets.py / member_condition_check.py` | [test_r14_views.py](../../tests/test_r14_views.py), [test_group_join_flow.py](../../tests/test_group_join_flow.py), [test_r14_completion_gaps.py](../../tests/test_r14_completion_gaps.py) | 自动；客户端未验 |
| AC23 | 当前群发现、公开列表、无报名副作用 | `application/lottery_queries.py / telegram/lottery_queries.py` | [test_r14_queries.py](../../tests/test_r14_queries.py), [test_r14_boundaries.py](../../tests/test_r14_boundaries.py) | 自动 |
| AC24 | 成功登记人数及达标人数同聚合，时间标记 | `public_participation.py` | [test_r14_start_and_signup.py](../../tests/test_r14_start_and_signup.py) | 自动；人工核对统计公式 |
| AC25 | 主动意向、成功登记、后续失效分离 | `participant_details.py / participant_records.py` | [test_r14_start_and_signup.py](../../tests/test_r14_start_and_signup.py), [test_registration.py](../../tests/test_registration.py) | 自动 |
| AC26 | 截止/并发裁决沿用，查询分页串行锁 | `participants.py / lottery_queries.py / member_navigation.py` | [test_registration.py](../../tests/test_registration.py), [test_v30_event_edges.py](../../tests/test_v30_event_edges.py), [test_v32_callback_mutation_replay.py](../../tests/test_v32_callback_mutation_replay.py) | 原裁决自动；客户端并发未验 |
| AC27 | 正文链接、联系人、文件夹、原卡转发均保留 | `presentation/campaign_card.py / campaign_share_keyboard.py` | [test_campaign_card_layout.py](../../tests/test_campaign_card_layout.py), [test_campaign_sharing.py](../../tests/test_campaign_sharing.py) | 自动；长按转发未验 |
| AC28 | 有图/无图/长文规则续页确认后主卡 | `participation_rules.py / rule_publication.py` | [test_v30_publication.py](../../tests/test_v30_publication.py), [test_campaign_sharing.py](../../tests/test_campaign_sharing.py) | 自动 |
| AC29 | 旧设置兼容、更多时长、待办面板不删 | `creator_messages.py / member_flow.py / retention_policy.py` | [test_r14_views.py](../../tests/test_r14_views.py), [test_member_flow_public_refs.py](../../tests/test_member_flow_public_refs.py), [test_r14_retention.py](../../tests/test_r14_retention.py) | 自动 |
| AC30 | 固定群分母、空条件、仅邀请/仅发言、分页 | `member_condition_check.py / lottery_query_views.py` | [test_r14_views.py](../../tests/test_r14_views.py), [test_v33_experience_unification.py](../../tests/test_v33_experience_unification.py) | 自动 |
| AC31 | 旧深链回调只读、私有入口与身份校验 | `start_payloads.py / member_flow.py / member_navigation.py` | [test_start_and_draft_recovery.py](../../tests/test_start_and_draft_recovery.py), [test_v32_member_navigation.py](../../tests/test_v32_member_navigation.py), [test_r14_completion_gaps.py](../../tests/test_r14_completion_gaps.py) | 自动；真实屏蔽/失效链接待验 |
| AC32 | 功能去向与验证分层记录 | `本记录下方保留清单及本表` | 56项逐行映射；全量回归记录 | 文档与源码 |
| AC33 | 未点报名无登记副作用，邀请入口不代报名 | `member_flow.py / platform_inviter.py / lottery_queries.py` | [test_r14_queries.py](../../tests/test_r14_queries.py), [test_r14_boundaries.py](../../tests/test_r14_boundaries.py), [test_campaign_sharing.py](../../tests/test_campaign_sharing.py) | 自动 |
| AC34 | 确认主卡后单一辅助入口，独立Outbox | `application/channel_shortcuts.py / notice_recovery.py / admin_notice_recovery.py` | [test_r14_start_and_signup.py](../../tests/test_r14_start_and_signup.py), [test_r14_boundaries.py](../../tests/test_r14_boundaries.py), [test_r14_completion_gaps.py](../../tests/test_r14_completion_gaps.py) | 自动；实际投递待验 |
| AC35 | 统一原报名合同，旧冻结规则仍由原服务计算 | `start_payloads.py / member_flow.py / 原业务服务` | [test_group_join_flow.py](../../tests/test_group_join_flow.py), [test_registration.py](../../tests/test_registration.py), [test_v30_admission_integration.py](../../tests/test_v30_admission_integration.py) | 自动 |
| AC36 | 计划/暂停/运行/截止文案不夸大计数 | `member_base_activity.py / member_detail_actions.py` | [test_r14_boundaries.py](../../tests/test_r14_boundaries.py), [test_v23_bonus_navigation.py](../../tests/test_v23_bonus_navigation.py), [test_referral_deadline_experience.py](../../tests/test_referral_deadline_experience.py) | 自动 |
| AC37 | 仅邀请门槛仍有入口，旧人品只查原发言口径 | `member_invites.py / group_reputation.py` | [test_v30_member_invite_access.py](../../tests/test_v30_member_invite_access.py), [test_v33_experience_unification.py](../../tests/test_v33_experience_unification.py) | 自动 |
| AC38 | 独立待完成区、与成功名单分开、终态保留 | `participant_records.py / participant_list_views.py` | [test_r14_start_and_signup.py](../../tests/test_r14_start_and_signup.py), [test_personal_flow_messages.py](../../tests/test_personal_flow_messages.py) | 自动 |
| AC39 | 辅助入口只跟授权单场，新精选沿原链，排除汇总/编辑 | `application/channel_shortcuts.py` | [test_r14_boundaries.py](../../tests/test_r14_boundaries.py), [test_r14_start_and_signup.py](../../tests/test_r14_start_and_signup.py) | 自动 |
| AC40 | 主卡/分享/详情同口径，空条件不伪造0/0 | `public_participation.py / campaign_sharing.py / member_navigation.py` | [test_r14_start_and_signup.py](../../tests/test_r14_start_and_signup.py), [test_campaign_sharing.py](../../tests/test_campaign_sharing.py), [test_r14_views.py](../../tests/test_r14_views.py) | 自动 |
| AC41 | 普通用户查询蓝色正文活动名、同场私聊入口 | `bot_commands.py / lottery_query_views.py` | [test_bot_commands.py](../../tests/test_bot_commands.py), [test_r14_queries.py](../../tests/test_r14_queries.py) | 自动；菜单客户端待验 |
| AC42 | 群命令菜单、@指定Bot、中文兼容、隐私边界 | `bot_commands.py / lottery_queries.py` | [test_bot_commands.py](../../tests/test_bot_commands.py), [test_r14_queries.py](../../tests/test_r14_queries.py) | 自动；消息可见权限未验 |
| AC43 | 未报名/已报名分享、选择器、个人邀请复制/打开/转发 | `member_navigation.py / member_share.py / member_group_invites.py` | [test_campaign_sharing.py](../../tests/test_campaign_sharing.py), [test_inline_campaign_sharing.py](../../tests/test_inline_campaign_sharing.py), [test_v23_bonus_navigation.py](../../tests/test_v23_bonus_navigation.py) | 自动；选择/转发实际结果未验 |
| AC44 | 普通宣传无个人归因；邀请原归因；投递事实独立 | `campaign_sharing.py / 原邀请服务` | [test_campaign_sharing.py](../../tests/test_campaign_sharing.py), [test_v23_referral_experience.py](../../tests/test_v23_referral_experience.py) | 自动 |
| AC45 | 草稿/封面/搜索/复制/首次提醒/数字三联/结果保留 | `原创建器与结果模块；ui_style.py` | [test_creator_cover.py](../../tests/test_creator_cover.py), [test_creator_gate_search_regressions.py](../../tests/test_creator_gate_search_regressions.py), [test_creator_numeric_recovery.py](../../tests/test_creator_numeric_recovery.py), [test_unregistered_reminders.py](../../tests/test_unregistered_reminders.py) | 自动 |
| AC46 | T前放弃、T后20秒、跨T按实际发送证据 | `retention_policy.py / message_deletion.py` | [test_r14_retention.py](../../tests/test_r14_retention.py), [test_r14_boundaries.py](../../tests/test_r14_boundaries.py) | 自动；生产切换未执行 |
| AC47 | 三类别名同三分区，完整词匹配与只读投影 | `lottery_queries.py / lottery_query_views.py` | [test_r14_queries.py](../../tests/test_r14_queries.py), [test_r14_boundaries.py](../../tests/test_r14_boundaries.py) | 自动 |
| AC48 | 引用本人命令、多场实际进度、失败不当0 | `gateway_lottery_query.py / lottery_query_views.py` | [test_r14_queries.py](../../tests/test_r14_queries.py), [test_r14_views.py](../../tests/test_r14_views.py), [test_r14_boundaries.py](../../tests/test_r14_boundaries.py) | 自动；客户端回复待验 |
| AC49 | 所有主动进度回复固定20秒，不删除原命令/详情 | `lottery_queries.py / message_deletion.py` | [test_r14_queries.py](../../tests/test_r14_queries.py), [test_notice_deletion.py](../../tests/test_notice_deletion.py) | 自动；实际20秒未验 |
| AC50 | 精确两行开始标题，原完整内容/封面/按钮同源 | `start_notices.py / gateway_start.py` | [test_r14_start_and_signup.py](../../tests/test_r14_start_and_signup.py), [test_campaign_card_layout.py](../../tests/test_campaign_card_layout.py) | 自动；真实完整投递待验 |
| AC51 | 普通@文本、无新增置顶、不自动删开始卡 | `gateway_start.py / start_notices.py` | [test_r14_start_and_signup.py](../../tests/test_r14_start_and_signup.py) | 自动；客户端提醒不作承诺 |
| AC52 | 意图/投递分离、幂等恢复、不补历史running | `start_notices.py / jobs/handlers.py` | [test_r14_start_and_signup.py](../../tests/test_r14_start_and_signup.py), [test_outbox_projection.py](../../tests/test_outbox_projection.py) | 自动 |
| AC53 | 请求人/消息/聊天绑定，匿名不冒认、同话题 | `lottery_queries.py / gateway_lottery_query.py` | [test_r14_queries.py](../../tests/test_r14_queries.py) | 自动；多端真实身份场景未验 |
| AC54 | 持久T、分类优先级、命令不转持久面板 | `retention_policy.py / lottery_queries.py` | [test_r14_retention.py](../../tests/test_r14_retention.py), [test_member_flow_public_refs.py](../../tests/test_member_flow_public_refs.py) | 自动；在途生产切换未执行 |
| AC55 | 状态与开始意图同事务、待确认恢复、发送前复核 | `jobs/handlers.py / start_notices.py` | [test_r14_start_and_signup.py](../../tests/test_r14_start_and_signup.py), [test_r14_boundaries.py](../../tests/test_r14_boundaries.py), [test_r14_completion_gaps.py](../../tests/test_r14_completion_gaps.py) | 自动 |
| AC56 | 完整信息/两行标题容量、秒级时间、Bot不入分母 | `campaign_card.py / participation_rules.py / condition_check` | [test_campaign_card_times.py](../../tests/test_campaign_card_times.py), [test_r14_views.py](../../tests/test_r14_views.py), [test_r14_start_and_signup.py](../../tests/test_r14_start_and_signup.py) | 自动与文案核对 |

## 原功能保留去向

| 原能力/数据 | 本轮保留位置 | 核对结果 |
| --- | --- | --- |
| 分群条件总数与逐项结果 | 详情+参与条件页 | 群/频道分母不含Bot启动，隐藏已关注按钮不减少分母；异常和待批用⏳ |
| 每群今日条数、连续天数、达标状态 | 原门槛页+详情+综合进度 | 原资格与计数服务保留，跨日已取得资格不倒退 |
| 今日奖励、累计、轮次、下一轮差额 | 详情摘要+原加成页+综合进度 | 原DailyProgress与TicketLedger仍为来源 |
| 邀请人数、待核验、留存、群归因 | 综合进度+原分群邀请页及记录 | 无奖励但有邀请门槛仍保留入口；公开摘要不包含对象名单/私有邀请链接 |
| 基础、有效、暂存次数 | 原详情账本区+综合进度 | 不在展示层补票，未知资格不写成已达标 |
| 提醒触发/首次/冷却/事件时间 | 原提醒服务与Outbox | 只改消息生命周期及短文案；删除不重置触发资格 |
| 公告正文、封面、说明、奖品、联系人、文件夹 | 原卡渲染+分享+开始卡 | 长文仍走原续页确认与恢复；自定义原文不批量替换 |
| 参与记录、中奖结果、领奖联系 | 原列表+本人历史详情 | 新增待完成分类；不删除历史事实，不改结果通知开关 |
| 草稿、复制、封面、搜索、数字控件 | 原创建器 | 默认值只影响新建，旧草稿不补写说明；三联控件保留 |

## 四维代码复核

- 正确性：报名意向与成功事实分离；冻结规则继续由原服务裁决；删除T边界和开始投递使用持久事实；命令查询不能自动报名。
- 性能：公共人数单次聚合、不逐人调用Telegram；活动列表5场/页、条件6项/页、个人进度1场/页；只在请求/发布时查询，不新增人数定时广播。条件刷新仍需逐目标核验，极多目标时有Telegram延时风险；没有开展负载测试。设置页权限核验逐目标读取聊天类型与成员权限，只在打开设置时进行。
- 可维护性：新生命周期和查询分为独立小模块，复用现有渲染、账本、Outbox及错误分类；代码指标由现有脚本检查。
- 安全性：个人分页绑定请求人及原消息；匿名不冒认身份；公共卡不写个人状态；辅助消息仅在原授权发送确认后产生；领奖后7天只作文本，不新增追回权限。通知重试继续检查管理员、活动版本和原主卡事实，UNKNOWN不重发。

## 验证结果

| 验证 | 结果 | 证据 |
| --- | --- | --- |
| 上海隔离测试执行器完整回归 | 8个分片、24批，共1650项通过，进程退出0 | [完整日志](../../artifacts/lottery-r14-20261030/full-regression.log) |
| Alembic往返与模型一致性 | upgrade head → check → downgrade base → upgrade head → check通过 | 同上完整日志；仅上海专用测试库 |
| 缺口专项 | 109项通过，3条SQLAlchemy弃用告警 | [专项日志](../../artifacts/lottery-r14-20261030/completion-gaps-109.log) |
| 最终源码R14专项 | 74项通过，3条SQLAlchemy弃用告警；覆盖本次新增与最后两处边界修正 | [最终专项日志](../../artifacts/lottery-r14-20261030/final-source-focused.log) |
| 设置页/管理入口及状态等离线单元检查 | 13项通过，无数据库连接；已纳入最终上海专项 | [离线日志](../../artifacts/lottery-r14-20261030/final-wiring-local.log) |
| 格式、Ruff、代码指标 | 577个Python文件格式检查、lint、指标全部通过 | [静态检查](../../artifacts/lottery-r14-20261030/static-checks.txt) |
| 编译、差异与文档 | compileall、git diff --check通过；56条AC编号无重复，文档相对链接无缺失 | [验证清单](../../artifacts/lottery-r14-20261030/verification.json) |

不同测试组存在重叠，不将其相加当作独立测试总数。全量测试采用启动时的源码快照；运行期间修正了频道条件的零起始页码、旧草稿群ID与实际聊天类型检查，这两处最终源码由74项R14专项重新验证，没有再次声称最终源码全量重跑。最终Python源码哈希见[源码清单](../../artifacts/lottery-r14-20261030/source-manifest.json)。

中间失败已用于修复新默认值/标签断言、旧报名测试的群/私聊语义、成功登记夹具及遗漏展示分支；没有删除原业务断言来让测试通过。设置页接线测试还暴露并修复了旧草稿主群漏检，保留[修复前失败日志](../../artifacts/lottery-r14-20261030/intermediate-legacy-draft-failure.log)。失败轮次不计为全量通过。SQLAlchemy告警为既有Session.flush(objects=...)弃用提示，本次没有顺带改写相关业务事务。

## 未发布及外部验收边界

尚未在真实Telegram验证：普通成员斜杠菜单可见性、中文文本在隐私模式下可达性、频道个人临时响应在手机/桌面的支持、双用户隔离、分享选择器和长按转发、真实20秒删除、定时完整开始卡与实际推送。SDK个人临时消息采用显式适配，失败保留私聊入口；仅跳转成功不能声称频道原地能力通过。适配参数与删除权限已按[Telegram Bot API官方接口](https://core.telegram.org/bots/api#ephemeralmessageparameters)和[deleteMessage限制](https://core.telegram.org/bots/api#deletemessage)核对；文档支持不等于真实客户端已验收。

本轮迁移只新增持久策略时点表，不改历史中奖/票数事实。以后若另行授权发布，需按现有发布合同停止旧消费者、迁移并由新Worker持久化T，再核对发送及删除证据；不能让旧Worker在T后继续按旧策略投递。已经在途且Telegram接受的删除无法撤回，必须保留实际结果。这里仅记录发布前置条件，本轮未执行发布。
