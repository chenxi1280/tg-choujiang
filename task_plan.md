# TG 群抽奖机器人开发与马来西亚生产验证计划

## 目标

以 V1.3 PRD 为开发合同，完成 Python + PostgreSQL 抽奖机器人、自动化测试、GitHub Actions 发布链路，并部署到马来西亚生产服务器完成运行与 Telegram 业务验证。

## 阶段

- [complete] 1. 读取并渲染原始 DOCX，确认完整内容与版式
- [complete] 2. 对照同类 TG 项目，审查产品规则、数据一致性、安全性与工程落地
- [complete] 3. 形成修订版 PRD 并写入 `doc/`
- [complete] 4. 渲染、逐页检查并完成结构与内容验收
- [complete] 5. 复审 V1.1 的剩余设计缺口与 Telegram/PostgreSQL 数据边界
- [complete] 6. 设计端到端数据流、事务边界、状态机、幂等键与失败恢复
- [complete] 7. 生成 V1.2 数据流转设计版并保存至 `doc/`
- [complete] 8. 完成全量渲染、结构审计与逐页验收
- [complete] 9. 提取 V1.2 开发验收清单，核对同类项目与马来西亚服务器现状
- [complete] 10. 初始化 Git 仓库、Python 工程、PostgreSQL 模型与迁移
- [complete] 11. 实现 Durable Inbox、活动/报名/计票/邀请/结算/开奖/Outbox 全链路
- [complete] 12. 实现 Bot 交互、worker、健康检查、Docker 与运维文档
- [complete] 13. 建立单元/集成/并发/故障恢复测试并完成本地验收
- [complete] 14. 创建 GitHub 仓库与 Actions，按 master -> release 发布到马来西亚服务器
- [in_progress] 15. 核验线上 SHA、迁移、容器、Telegram 连通性和真实抽奖业务闭环
- [complete] 16. 对 V1.3 实现、数据一致性、Telegram 边界和生产运维做发布后复审并修复确认问题
- [complete] 17. 复审每期奖品、中奖人数、随机抽取的产品规则、数据模型、交互和算法现状
- [complete] 18. 补齐多奖品配置、名额校验、随机开奖与通知的设计和实现
- [complete] 19. 更新 V1.4 PRD、验收清单、测试与迁移并完成文档视觉验收
- [complete] 20. 通过质量门禁后发布生产并核验运行事实
- [complete] 21. 全量复审应用服务、事务边界、状态机、并发幂等与 Telegram 交互
- [complete] 22. 为确认缺陷建立可复现测试并实施根因修复
- [complete] 23. 完成静态检查、42+ 项测试、迁移往返、漂移与生产配置验证
- [complete] 24. 发布修复并分别核验线上版本、服务健康与真实业务证据
- [complete] 25. 第三轮独立审查生命周期任务、回调重放、资格快照、Outbox 与运维恢复边界
- [complete] 26. 为新增确认缺陷补回归并完成根因修复
- [complete] 27. 执行完整质量门禁、迁移漂移与发布前检查
- [complete] 28. 发布第三轮修复并回读生产版本、运行状态和未决事实
- [complete] 29. 对照极简抽奖机器人，提取面板式创建交互与本项目差异
- [complete] 30. 更新 V1.5 PRD，冻结极简创建与数据流转合同
- [complete] 31. 实现可回退的配置面板、逐项编辑、发布校验与本地时区展示
- [complete] 32. 完成自动化测试、静态检查、文档渲染与代码审查
- [complete] 33. 按 master -> release -> Actions 发布并验证线上版本、服务健康与私聊入口回复
- [complete] 34. 重新逐步实测极简机器人的完整创建路径并形成逐屏差异表
- [complete] 35. 重做创建入口、步骤编排、按钮层级、返回/取消与确认发布交互
- [complete] 36. 保留本项目计票、结算和加权随机开奖合同并补充回归测试
- [complete] 37. 更新 PRD、完成质量门禁并按 master -> release 发布生产
- [complete] 38. 在 Telegram 真实客户端验收新版创建路径
- [blocked] 39. 实测 `@LotteryBestBot` 全部命令、管理入口、创建字段与参与端流程，形成逐屏功能清单（已完成可访问页面；Mac 被用户锁屏后，消息提示等剩余参考页无法继续实测）
- [complete] 40. 对照现有实现形成完整差异矩阵，并更新 V1.7 产品合同与验收清单
- [complete] 41. 实现用户资料、地区、参抽记录、完整创建字段与抽奖管理入口
- [complete] 42. 实现封面、用户限制、参与条件、抽奖说明、人品加成和本项目开奖方式的交互闭环
- [complete] 43. 完成自动化测试、静态检查、迁移检查与 DOCX 渲染验收
- [complete] 44. 按 master -> release -> GitHub Actions 发布并核验线上版本与服务健康
- [blocked] 45. 在 Telegram 真实客户端验收命令、创建、管理与参与链路，发布动作使用“不发布”测试草稿（客户端锁屏，发布后 Telegram E2E 未证明）
- [complete] 46. 第二轮只读核对参考 Bot、V1.7 合同、当前代码和测试覆盖，确认 3 个 P0 与后续差距
- [complete] 47. 修订 V1.8 对齐合同与验收清单，纠正消息提示、人品加成、默认值和管理入口语义
- [complete] 48. 修复创建器安全回调、逐步创建、参与条件累加、身份解析和发布卡交互
- [complete] 49. 修复开奖前多群资格复核、满人触发持久化与 30 天中午兜底
- [blocked] 50. 已完成延迟删除提示；人品积分/兑换/排行/管理审计等待合同中 `TBD` 的计分、日上限、兑换成本和票上限配置
- [blocked] 51. 已完成 `/index` 权威活动管理；客户等级与导航/精选推送等待合同中 `TBD` 的等级和门槛配置
- [blocked] 52. 静态检查、141 项 60 秒全量测试、迁移往返/漂移已完成；真实 Telegram 不发布 E2E 未执行，保持 `unproven`
- [complete] 53. 从用户提供的 Telegram 旧消息取证公开卡片的 UTC ISO 时间与完整快照哈希泄露
- [complete] 54. 先修订公开投影合同，再统一活动卡/开奖结果的最终展示边界并保留内部快照审计事实
- [complete] 55. 增加新旧载荷兼容回归，执行 Ruff、代码度量与 60 秒全量测试
- [complete] 56. 盘点全部 Telegram 发送、编辑、回调提示和异常回复路径，建立公开技术字段泄露清单
- [complete] 57. 把统一公开投影规则写入 V1.8 合同与验收清单，明确允许显示的业务标识和禁止字段
- [complete] 58. 在最终展示边界统一时间、状态、标识和错误文案，并为每类确认问题增加回归
- [complete] 59. 执行 Ruff、代码度量、60 秒全量测试及 V1.8 DOCX 重建与视觉/结构验收
- [complete] 60. 修复 Outbox 本地投影与 Gateway 开始标记的事务边界
- [complete] 61. 保护 Campaign 公开引用迁移，拒绝不可逆 downgrade
- [complete] 62. 将自定义开奖时间提示改为按活动时区动态生成的未来示例
- [complete] 63. 补齐故障、迁移和时间过期回归并执行完整质量门禁
- [complete] 64. 提交 master、同步 release，并核验精确 SHA 的 GitHub Actions 发布结果
- [complete] 65. 提交本轮确定性 V1.8 修复、推送 master/release，并核验精确 SHA 的 GitHub Actions 与生产健康
- [complete] 66. 审计当前创建入口、参与条件、邀请/发言计票规则与公开文案，冻结本轮优化合同
- [complete] 67. 将创建入口改为直接打开抽奖预览面板，并允许从面板设置标题、封面、用户限制等字段
- [complete] 68. 将参与条件统一为可累加的群聊/频道列表，允许配置一个或多个条件并冻结可验证事实
- [complete] 69. 将“人品加成”更名为“抽奖加成”，实现可配置邀请阶梯/上限与按自然日独立结算的发言加成
- [complete] 70. 更新 PRD/验收清单，完成迁移、自动化测试与静态质量门禁
- [complete] 71. 修复 V1.9 审查确认的邀请资格、退出冲正、发言范围、阶梯上限和参与条件操作漏洞
- [complete] 72. 复核创建者与参与者操作路径，统一频道/群聊文案、按编号删除和发布前可执行性校验
- [complete] 73. 更新 V1.9 PRD 与验收清单，补并发/退出/频道/大量条件回归并执行全量门禁
- [complete] 74. 修复复核确认的草稿条件、关闭邀请、截止复核、补条件恢复和极端值问题
- [complete] 75. 补齐邀请逐条件审计、旧规则展示和创建输入即时校验
- [complete] 76. 更新 V1.9 合同、数据库迁移与全部回归，执行完整质量门禁
- [complete] 77. 提交 master、快进 release 并通过 GitHub Actions 部署生产
- [complete] 78. 核验线上 SHA、迁移、健康状态和真实 Telegram 按钮点击
- [complete] 79. 对线上点击发现的问题补回归、修复并通过全量质量门禁
- [complete] 80. 重新发布修复并复核精确 SHA、迁移、队列和 Telegram 客户端路径
- [completed] 81. 冻结链接输入、按钮式抽奖加成和活动级邀请链接生命周期合同
- [completed] 82. 为三条操作链补失败回归并实施 Telegram 交互与领域修复
- [completed] 83. 更新 V1.9 PRD/验收清单并通过完整本地质量门禁
- [complete] 84. 发布生产并验证精确 SHA、健康状态与真实 Telegram 点击
- [complete] 85. 为参与条件失败定位、转发消息识别和主群去重补失败回归
- [complete] 86. 实施参与条件解析、Telegram 权限诊断与创建交互修复
- [complete] 87. 执行定向测试、全量 60 秒回归及静态质量门禁
- [complete] 88. 发布生产并分别核验精确 SHA、健康状态和 Telegram 客户端结果
- [complete] 89. 审计主群创建权限、多条件管理员校验与发言条件现有数据合同
- [complete] 90. 冻结并补回归：所有群管理员可创建、多群双管理员、逐群发言要求展示与核验
- [complete] 91. 实施权限、草稿、活动卡和资格判定改动并更新 V1.9 合同
- [complete] 92. 执行完整本地门禁，发布生产并分层验证 SHA、健康和 Telegram 行为
- [complete] 93. 复核公开频道参与条件失败，区分链接解析、机器人权限与真实 Telegram 未验证边界
- [complete] 94. 根据 2026-08-15 客户端无回复截图，只读追踪 Inbox/Outbox、轮询心跳与创建状态机的首个破损边界
- [complete] 95. 证明活动已按冻结配置发往主群，条件群无卡片属预期语义；不擅自改成多群重复发卡
- [complete] 96. 完成脱敏生产诊断、精确时间/目标群回读及 master 质量门禁，保持 release 和应用未变
- [complete] 97. 修复当前生产 `unknown_deliveries=3`、重新部署，并完成线上功能验收

## 关键约束

- 技术栈按 Python + PostgreSQL 设计。
- Telegram 远端事实、数据库状态与本地测试结果分开表述。
- 保留原始文档，项目内另存修订版。
- 不引入静默降级、模拟成功或无依据的兜底流程。
- 生产应用代码只通过 GitHub Actions 发布；SSH 只用于只读核验和必要的非代码环境配置。
- 发布成功、服务健康与 Telegram 业务完成分别取证，不互相替代。
- 所有 PRD 明示规则均进入验收清单，不以 MVP 名义静默删减。
- 抽奖加成直接增加该参与者的抽奖次数；邀请奖励按累计有效邀请人数的配置阶梯结算，发言奖励按活动时区逐自然日独立结算。
- 公开 Telegram 文案与内部审计事实分层：不可变快照及完整哈希保留在数据库/Outbox，群消息不得直接展示内部校验值。

## 错误记录

| 错误 | 尝试 | 处理 |
|---|---:|---|
| LibreOffice 隔离渲染环境未发现中文字体 | 1 | 在任务临时配置目录安装系统自带 `Arial Unicode MS` 后重新渲染；未修改系统与项目依赖 |
| 文档表格审计工具不接受旧参数 `--audit` | 1 | 改用当前版本的直接调用方式，20 张表几何审计通过 |
| 当前目录不是 Git 仓库且没有实现代码 | 1 | 作为新项目初始化；先复用同类项目的 Actions/部署合同，再创建仓库和工程 |
| macOS 没有 `timeout` 命令，且 zsh 的 `status` 为只读变量 | 1 | 改用 Python `subprocess.run(..., timeout=60)` 包装测试，避免重复执行失败命令 |
| 系统 Python 3.14 未安装 pytest | 1 | 发现项目 `.venv` 已存在，改用 `.venv/bin/python` 运行项目门禁 |
| 全量 pytest 的数据库夹具连接 PostgreSQL 时反复 `ConnectionResetError: [Errno 54]` | 1 | 停止本地全量进程；将其标记为本机数据库环境阻断，改以定向纯单元测试和最近 CI/生产健康结果核对 |
| GitHub 不存在 `chenxi1280/tg-choujiang` | 1 | 代码和测试完成后创建私有仓库，并配置 Actions secrets/variables |
| 首轮 Ruff 报 2 个 import 顺序、1 个行宽、1 个 ClassVar | 1 | 自动排序 import，拆分长集合，并为 SQLAlchemy 类型映射标注 ClassVar |
| 应用服务首轮 Ruff 报 5 个排序和 2 个行宽问题 | 1 | 统一 Ruff 自动排序并拆分长签名/推导式；未改变业务语义 |
| Telegram 层 Ruff 将中文全角冒号识别为歧义字符 | 1 | 用户可见模板统一改用半角冒号，import 交由 Ruff 排序 |
| 中文 Bot 文案触发 Ruff RUF001 共 19 处 | 1 | 仅对 `telegram/*.py` 用户界面文案关闭 RUF001；其余静态规则保持启用 |
| Alembic 异步连接缺少 greenlet | 1 | 将依赖改为 `SQLAlchemy[asyncio]`，确保开发、CI 和生产迁移均显式安装异步 extra |
| Alembic check 误报 10 个 Enum CHECK 待删除，3 个外部 ID 主键生成序列 | 1 | 外部 ID 主键显式 `autoincrement=False`；diff 仅忽略已由模型定义并实际保留的 Enum CHECK 名称 |
| 本机 Docker build 停在 Docker Hub `python:3.12-slim` 元数据获取 | 1 | 已停止挂起构建；Compose/脚本/源码检查均已通过，GitHub Actions 继续执行真实 BuildKit 构建并作为发布硬闸门 |
| V1.3 在隔离 LibreOffice 中中文显示为空框 | 3 | 确认打包运行时未加载系统中文字体；仅在 QA 运行时临时注入系统 Arial Unicode，完成 31 页逐页渲染后移除临时副本，未改系统或仓库依赖 |
| 发布后修复首轮 Ruff format 检查发现 `groups.py` 一处可折叠换行 | 1 | 使用项目 Ruff formatter 做机械格式化后重新执行全部质量门禁 |
| 新回归测试首次插入命中暂停邀请测试中段，导致语法错误并缺少测试 import | 1 | 恢复原测试体顺序、补齐 Campaign/UpdateState import，再运行 formatter 与测试 |
| 新增 Outbox 回归使 `test_runtime_integrity.py` 超过 500 行且单测试超过 50 行 | 1 | 按职责拆分到 `test_outbox_projection.py`，长场景提取 seed/assert helper，不放宽度量阈值 |
| V1.3 字体试改导致分页从 21 页膨胀到 26 页 | 1 | 撤销字体替换，仅保留内容级 PRD 修订；用文档原字体配合临时 QA 字体完成最终 31 页视觉验收 |
| 首次多文件模型补丁因 Campaign 字段顺序不匹配而未应用 | 1 | 读取模型精确上下文后拆成小补丁，确认无部分写入再继续 |
| macOS 不提供 GNU `timeout`，首次 60 秒测试命令无法执行 | 1 | 改用受控测试执行并确认全套测试在 8 秒内结束；未放宽后端 60 秒上限 |
| `uv run` 重建环境时未安装 dev extra，pytest 入口误用系统环境 | 1 | 执行 `uv sync --extra dev` 后固定使用项目 `.venv/bin/python -m pytest` |
| 新增资格逐群观察列后直接运行数据库测试，测试库尚未升级导致 `observations does not exist` | 1 | 明确核验测试库 URL 后执行 Alembic `d4c8a1f2b903 -> e7a9c2d4f601`，再重跑同一测试分区 |
| 本轮定向数据库回归连接 `127.0.0.1:55432` 被拒绝 | 1 | 非数据库测试 21 项已通过；先核对 Compose 服务状态并启动项目测试 PostgreSQL，再重跑数据库回归 |
| 本轮失败优先回归连接旧 55432 测试库命中密码不一致 | 1 | 4 个新增业务断言按预期失败；数据库用例尚未进入断言，实施后继续使用既有 55433 隔离 PostgreSQL 合同验证 |
| 新迁移空库 downgrade 误用了不存在的 `jsonb_object_length` | 1 | 事务已完整回滚；改用 `jsonb_object_keys` 计数后重跑同一往返，不绕过不可逆数据保护 |
| 本轮首轮 Ruff/代码度量发现测试全角标点、4 个位置参数和 53 行测试函数 | 1 | 改用 ASCII 测试输入、关键字参数，并提取测试数据播种 helper；不放宽规则 |
| 首轮全量测试仅旧公开结果文案断言仍期待“抽奖票” | 1 | 产品文案已统一为“抽奖次数权重”，同步更新文案级回归后重跑全量测试 |
| 本轮迁移往返首次输入了不存在的父 revision `b91f2d6a7403` | 1 | Alembic 在任何 DDL 前拒绝；读取实际迁移链后用 `b91e4d7a6c20` 重跑，upgrade/downgrade/upgrade 和漂移检查全部通过 |
| V1.9 第二轮多文件补丁因 Invitation 模型导入上下文不匹配而未应用 | 1 | 确认补丁原子失败、无部分写入；改为按当前文件精确上下文拆分应用 |
| 第一轮定向检查发现参与条件同步 helper 未随原子失败补丁落盘 | 1 | 补入纯函数 `_with_required_chats` 后重跑同组 Ruff 与测试 |
| 逐群资格摘要直接拼接全部状态，超过 `observed_status VARCHAR(40)` | 1 | 将数据库摘要固定为短聚合状态，完整逐群事实只存 JSONB `observations`；不截断或丢弃审计事实 |
| 默认人气加成改为关闭后，3 个旧活动测试仍依赖隐式 `standard` | 1 | 在验证旧加成行为的测试中显式声明 `luck_mode="standard"`，保留新活动默认关闭合同 |
| V1.4 前两次隔离渲染把 QA 字体放到错误层级，中文未加载 | 2 | 定位 LibreOffice 实际 `fonts/truetype` 目录，临时注入后完成 33 页视觉验收并立即移除字体副本 |
| 复审读取命令的工作目录误写为 `/Users/xida/PychProjects/...` | 1 | 未执行任何代码或文件操作；改用已确认的仓库绝对路径后继续 |
| zsh 对不存在的 `scripts/*.sh` glob 直接报 `no matches found` | 1 | Alembic 往返、漂移与 Compose 已先完成；Shell 校验改用 `rg --files -g '*.sh'` 枚举实际文件，不依赖空 glob |
| 第三轮定向 Ruff 首次发现 `outbox.py` 新 import 顺序不符合 I001 | 1 | 使用 Ruff 仅机械排序该 import block；随后定向 Ruff、代码度量和 12 项相关回归通过 |
| Chronicle 当前未运行，无法回看用户所指“极简”历史界面 | 1 | 改用当前 Telegram 客户端搜索并实测 `@LotteryBestBot` 的创建面板；仅创建临时草稿，不发布活动 |
| 首次 V1.5 渲染组合命令包含被环境禁止的 `rm -rf` | 1 | 命令在执行前被拒绝、未改动文件；改用 `mktemp -d` 创建唯一 QA 输出目录 |
| V1.5 首轮隔离渲染未加载中文字体，中文显示为空框且分页压缩为 23 页 | 1 | 文档 XML 中文完整；按 V1.4 已验证方式向任务级 LibreOffice QA 运行时临时注入系统中文字体后重新渲染，完成检查后移除副本 |
| Compose 验证误用了不存在的 `.env.example` | 1 | 改用仓库实际存在的 `.env.docker.example`，配置解析通过 |
| macOS 缺少 GNU `timeout`，系统 pytest 又未安装项目包 | 1 | 固定使用项目 `.venv/bin/pytest`，完整 68 项测试在 14 秒内结束，满足 60 秒硬上限 |
| Telegram Desktop 最新会话的可访问性树读取持续超时 | 2 | 未复用旧坐标或伪造 callback；以生产 Inbox 的真实 Update 处理事实确认 `/start` 入口，创建面板点击验收保持独立口径 |
| Chronicle 当前未运行，本轮无法读取最新屏幕历史 | 1 | 不使用前一会话的过期图片；改用当前 Telegram 客户端重新实测参考机器人 |
| 本轮文档渲染命令误含环境禁止的递归删除 | 1 | 命令在执行前被拒绝且未改动文件；后续全部使用 `mktemp -d` 隔离输出目录 |
| macOS 未安装 GNU `timeout` | 1 | 使用 Python subprocess 的 60 秒超时执行完整测试，71 项均通过 |
| 文档技能的隔离 HOME 未加载中文字体 | 1 | 使用实际用户字体环境直接调用 LibreOffice，完成最终 29 页逐页视觉验收 |
| 交互后再次派发健康工作流时 GitHub API 连续返回 EOF | 2 | 不把网络错误记为健康失败；改用生产 SSH 只读执行同一 `check-services.sh`，服务、迁移与未决事实检查通过 |
| planning-with-files 恢复脚本误写为下划线文件名 | 1 | 未改动项目文件；确认技能实际脚本为 `session-catchup.py` 后重新执行 |
| Chronicle 本轮未运行 | 1 | 不使用过期屏幕历史；改用当前 Telegram 实时界面逐项实测参考 Bot |
| Telegram bundle identifier 同时匹配正式应用与更新临时副本 | 1 | 改用 `/Applications/Telegram.app` 绝对路径定位当前客户端 |
| 首次应用定位失败发生在 `tgState` 初始化前 | 1 | 重新以 `var` 声明持久变量，再读取绝对路径应用状态 |
| findings 补丁假定了不存在的 V1.6 标题 | 1 | 读取文件尾部实际结构后，改在现有末尾追加 V1.7 章节 |
| V1.7 文档脚本误用项目虚拟环境，缺少 `python-docx` | 1 | 不修改生产依赖；改用 Codex 工作区文档运行时执行同一脚本 |
| 最终门禁假定 shell 已导出 `TEST_DATABASE_URL` | 1 | 前置门禁全部通过；读取测试夹具默认连接后，以同一测试库显式执行 Alembic 漂移检查 |
| 系统未安装 ImageMagick `montage` | 1 | PDF 与逐页 PNG 已成功生成；使用工作区 Pillow 只做 QA 联系表拼接，不修改交付文档 |
| 首次提交命令未设置失败即停，`diff --cached --check` 报两行 Markdown 行尾空格后仍提交 | 1 | 提交尚未推送；清除行尾空格、严格复查并 amend 本地提交 |
| V1.8 增量 DOCX 的 canonical 隔离渲染器再次缺少中文字形 | 1 | 明确拒绝该渲染结果；使用真实 macOS 字体环境和唯一 LibreOffice profile 重渲染 58 页并逐页检查，未修改系统或源模板 |
| DOCX 内容探针首次使用了与合同不完全一致的预期短语 | 1 | 保留失败为显式校验信号；改按文档实际合同原文核对 R-P1-09、公开 snapshot_hash 禁止项与内部 Outbox 保留项 |
| 新增 GatewayRequest 文件首次 Ruff format check 报一处可折叠换行 | 1 | 使用项目 Ruff formatter 做机械格式化，再次执行全仓格式、规则、代码度量与测试门禁 |
| 会话恢复前误调用不存在的占位 Python 路径 `.venv-placeholder` | 1 | 调用被捕获且未改动文件；立即改用系统 `python3` 执行 planning-with-files 的真实 `session-catchup.py` |
| Alembic 最终复核首次未提供测试库连接，命中占位密码 | 1 | 未修改数据库；按测试夹具的 PostgreSQL 连接显式重跑，确认 `d4c8a1f2b903 (head)` 且 `alembic check` 无新增操作 |
| 最终指纹复核首次误用不存在的 V1.7 文件后缀“对齐版” | 1 | 通过 `rg --files doc` 解析真实文件名“完整对标版”，重新核对 V1.7/V1.8 SHA 与交付文件大小 |
| 本机到马来西亚生产 SSH 在 banner 交换阶段超时 | 1 | 不把访问路径失败记为生产故障；新数值范围改在 CampaignDraft 冻结边界执行，避免依赖未确认的历史规则范围，线上核验改走 GitHub Actions |
| 新活动范围收敛到 CampaignDraft 后单 helper 复杂度达到 11 | 1 | 按发言与邀请职责拆成两个校验函数，不放宽复杂度门禁 |
| 本轮全量测试首次连接 `127.0.0.1:55432` 被拒绝 | 1 | 启动仓库受控 PostgreSQL 16 测试容器，迁移至 head 并以同一连接重跑完整门禁 |
| zsh 中误用只读变量名 `status` 检查测试容器 | 1 | 首条等待命令立即失败、容器本身正常；改用任务专用变量名后确认就绪 |
| 审计命令假定报名服务位于 `application/registration.py` | 1 | 文件不存在且未发生修改；改用 `rg --files` 定位实际模块后继续 |
| 第三轮合同补丁假定 findings 标题没有日期前缀 | 1 | 补丁原子失败且未产生部分写入；读取文件尾部真实标题后按精确上下文重新应用 |
| 第三轮定向回归首次运行时本地测试 PostgreSQL 未启动 | 1 | 非数据库用例已执行；按仓库固定端口恢复受控 PostgreSQL 16 测试容器并升级迁移后重跑 |
| 第三轮定向 Ruff 发现一处 import 顺序和测试全角逗号 | 1 | 只调整 import 顺序并把测试数据改为 ASCII 逗号，不改变产品文案或业务逻辑 |
| 修复上述两项的首个组合补丁因格式化后的测试上下文不匹配而原子失败 | 1 | 读取精确上下文后重新应用；失败补丁未产生部分写入 |
| 新测试容器迁移首次误传 `TEST_DATABASE_URL`，Alembic 仍读取占位 `DATABASE_URL` | 1 | 未触达错误数据库；按 `alembic/env.py` 明确改传 `DATABASE_URL`，测试进程继续单独使用 `TEST_DATABASE_URL` |
| 首次代码度量发现 CreatorSettings 501 行、邀请回归文件 521 行和按钮状态机嵌套超限 | 1 | 收敛加成模块 import、把生命周期回归拆至独立文件，并用早返回拆平按钮状态分支；不放宽任何指标 |
| 首个度量修复组合补丁因 Ruff 已折叠状态机行而原子失败 | 1 | 读取精确格式后拆为三个小补丁重新应用；失败补丁没有部分写入 |
| CreatorSettings 首次收敛 import 后因视图分支被 Ruff 展开仍为 502 行 | 1 | 将纯视图选择移动到 `creator_bonus`，设置服务只保存状态并渲染，进一步降低文件职责和行数 |
| 首个视图选择补丁再次假定未折叠的赋值行而原子失败 | 1 | 读取当前格式化结果后按精确单行上下文应用；无部分写入 |
| master 发布后 `gh run watch/view` 连续两次 GitHub API TLS 握手超时 | 2 | 不把控制面读取失败记为 CI 失败；保留已确认的 run ID，稍后重试权威运行结论后再快进 release |
| 线上按钮点击后 `/new` 无响应且 app 轮询心跳过期 | 1 | 用独立 Production Health 与失败日志定位 aiogram `Default` 序列化异常；改用官方序列化器并补链接预览回归后重新发布 |
| 首次轮询修复 CI 因测试文件 506 行违反 500 行门禁 | 1 | 将新增回归拆到独立 `test_polling_serialization.py`，保持原门禁并重新执行全部 CI |
| 发布后最终 Telegram 点击复核遇到新的 macOS 锁屏 | 1 | 不绕过系统锁屏、不复用过期截图；生产健康已独立通过，等待用户手动解锁后继续真实客户端点击 |
| 新参与条件失败回归首次收集缺少 `message_chat_refs` | 1 | 这是先测后改的预期失败，确认缺失能力后进入实现，不以跳过或放宽测试处理 |
| 定向回归仍断言旧版笼统管理员错误文案 | 1 | 实现已按目标输出具体群名和操作指引；更新旧文案断言并新增公私群邀请权限边界回归 |
| 首轮全仓门禁发现 1 个格式化文件和 4 个正则断言规则 | 1 | 使用项目 Ruff 做机械格式化，测试正则改为 raw string；不放宽格式或 lint 门禁 |
| 首轮全量测试连接 55432 命中 PostgreSQL 密码不一致 | 1 | 非业务断言失败；先识别端口所有者，再使用仓库隔离测试数据库和显式 `TEST_DATABASE_URL` 重跑 |
| Shell 门禁误用 `sh -n` 检查 Bash 专用进程替换 | 1 | 脚本本身声明 Bash；改用 `bash -n` 逐文件检查，不修改脚本或规避语法 |
| 初次尝试同时向三个计划文件追加记录时，`findings.md` 上下文已变化 | 1 | 补丁原子失败且无部分写入；改为读取当前文件尾后按精确上下文追加 |
| 本机到马来西亚生产 SSH 在 banner 交换阶段超时 | 1 | 已在主机指纹校验后尝试，连接未进入远程 shell、未执行 SQL；改走 GitHub Actions 已授权生产通道做固定脱敏查询 |
| 新建 Actions 诊断工作流的首个 `apply_patch` 调用中，JavaScript 模板字符串把 shell `${...}` 误解为插值 | 1 | 调用在文件写入前失败；改用中性占位符构造补丁后再一次性替换，工作流文件已完整落盘 |
| 本轮首次追加计划文件时 `findings.md` 上下文已变化 | 1 | 补丁原子失败且无部分写入；读取文件尾部后改用精确末尾追加 |
