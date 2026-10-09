# 新建抽奖设置的恢复路径修复

## 发现与修复

本轮在 `865ed01` 基础上检查新建设置的接收状态、失效按钮、输入保存后的显示失败及重放、暂停后继续、分页到期和相邻确认步骤。

1. 当前面板旧按钮只对门槛、加成保留编辑；标题、奖品、开奖时间、封面、说明等会退回 idle。补齐各字段的恢复及原取消出口；奖品删除编号、组合分享链接、兼容准入数字输入也纳入继续输入识别。
2. 奖品删除/清空确认没有独立状态，输入已保存但 Telegram 编辑失败后会回到奖品摘要。现在保留明确确认状态，恢复同一范围的确认页面，恢复本身不删除奖品。同样保留参与群移除确认和发布前确认。
3. 参与群选择/搜索保存为 idle，重放和退出后继续会退回草稿。现在持久化列表状态，恢复页码、搜索及已选群，重新核验有权范围和实时群权限。准入条件列表同样恢复搜索及已选项，不重复添加/移除。
4. 准入条件输入重放原来只有泛化输入说明。现在恢复完整条件列表及操作按钮，已保存组合链接、条件摘要可见。
5. 分页面板渲染或翻页期间草稿到期、缓存页内容失效，会触发 AttributeError/KeyError。现在返回明确过期/失效提示，避免内部错误占住有序 Inbox。

合同先更新至 [V3.2 新建设置恢复补充](v3.2-navigation-and-interaction-prd-20261006.md#新建设置的恢复补充合同2026-10-09)。保持草稿身份、版本、消息绑定、业务幂等、全局 Inbox 顺序及开奖/投递语义；不自动执行旧按钮，也不把未知网络结果视为送达。没有新增迁移。

## 验证

- 新用例在原代码的基线运行 24 项失败，复现字段状态丢失、确认丢失及分页未处理错误。长文过期用例随后补齐 mock 的 `set` 方法，使失败断点准确对应真实 `None.state` 访问，而非 mock 缺少方法。
- 首次定向回归 127 通过、1 失败：旧封面恢复用例仍要求退回 idle，与本轮保留封面接收状态的合同相冲突。只更新该旧按钮恢复断言；主动完成封面仍返回 idle 的用例继续保留并通过。
- 最终上海 Docker 专用库定向回归 161 项通过（12.84 秒），每个 pytest 进程硬超时 60 秒；迁移及 Alembic 漂移检查成功，无新增迁移。
- 覆盖文件：`test_creator_setting_recovery.py`、`test_creator_numeric_recovery.py`、`test_v32_callback_mutation_replay.py`、`test_creator_leaf_experience.py`、`test_creator_settings.py`、`test_draft_panel_recovery.py`、`test_creator_cover.py`、`test_panel_response_recovery.py`、`test_creator_rule_review.py`、`test_creator_gate_search_regressions.py`、`test_creator_admission_picker.py`、`test_v32_creator_controls.py`。
- Ruff 全范围格式、Lint、代码规模和差异检查通过；发布版本与真实 Telegram 客户端证据单独记录，不以隔离测试替代线上结果。
