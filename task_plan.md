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
- [in_progress] 28. 发布第三轮修复并回读生产版本、运行状态和未决事实

## 关键约束

- 技术栈按 Python + PostgreSQL 设计。
- Telegram 远端事实、数据库状态与本地测试结果分开表述。
- 保留原始文档，项目内另存修订版。
- 不引入静默降级、模拟成功或无依据的兜底流程。
- 生产应用代码只通过 GitHub Actions 发布；SSH 只用于只读核验和必要的非代码环境配置。
- 发布成功、服务健康与 Telegram 业务完成分别取证，不互相替代。
- 所有 PRD 明示规则均进入验收清单，不以 MVP 名义静默删减。

## 错误记录

| 错误 | 尝试 | 处理 |
|---|---:|---|
| LibreOffice 隔离渲染环境未发现中文字体 | 1 | 在任务临时配置目录安装系统自带 `Arial Unicode MS` 后重新渲染；未修改系统与项目依赖 |
| 文档表格审计工具不接受旧参数 `--audit` | 1 | 改用当前版本的直接调用方式，20 张表几何审计通过 |
| 当前目录不是 Git 仓库且没有实现代码 | 1 | 作为新项目初始化；先复用同类项目的 Actions/部署合同，再创建仓库和工程 |
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
| V1.4 前两次隔离渲染把 QA 字体放到错误层级，中文未加载 | 2 | 定位 LibreOffice 实际 `fonts/truetype` 目录，临时注入后完成 33 页视觉验收并立即移除字体副本 |
| 复审读取命令的工作目录误写为 `/Users/xida/PychProjects/...` | 1 | 未执行任何代码或文件操作；改用已确认的仓库绝对路径后继续 |
| zsh 对不存在的 `scripts/*.sh` glob 直接报 `no matches found` | 1 | Alembic 往返、漂移与 Compose 已先完成；Shell 校验改用 `rg --files -g '*.sh'` 枚举实际文件，不依赖空 glob |
| 第三轮定向 Ruff 首次发现 `outbox.py` 新 import 顺序不符合 I001 | 1 | 使用 Ruff 仅机械排序该 import block；随后定向 Ruff、代码度量和 12 项相关回归通过 |
