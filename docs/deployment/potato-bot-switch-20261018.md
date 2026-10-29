# 土豆抽奖机器人与频道配置记录

2026-10-18（北京时间），按用户明确授权更换生产机器人 Token、名称、头像并创建对应频道。本文不保存 Token 或其他生产凭据。

## 身份与配置

- 旧机器人：`8713586647` / `@TGCCJBOT`。
- 新机器人：`8763179035` / `@potato_lottery_bot`，显示名称“土豆抽奖”。
- 生产共享配置：`/data/tg-lottery/shared/.env`，已更新 `BOT_TOKEN`、`BOT_ID` 及三个频道 URL；原配置保存在同目录权限为 0600 的备份中。
- 未更换应用制品，仍为 `20261014082822_a2df313`，代码 SHA `a2df3131607678ec7bf0ec39db969e76899ce542`。通过现有 Compose 配置重建 app 和 worker，未启动额外轮询消费者。
- `PLATFORM_REQUIRED_ENABLED=false` 保持不变。本次频道入口替换不等于启用强制关注。

| 频道 | Telegram ID | 公开链接 |
| --- | --- | --- |
| 土豆抽奖 · 使用说明 | -1004373637247 | https://t.me/potato_lottery_guide |
| 土豆抽奖 · 抽奖导航 | -1004361693914 | https://t.me/potato_lottery_nav |
| 土豆抽奖 · 精选推荐 | -1004477119880 | https://t.me/potato_lottery_featured |

频道由当前 Telegram 桌面账号“早班地铁等天晴”创建。新机器人在三个频道的管理员身份均已通过 `getChatMember` 回读确认。机器人和三个频道均设置土豆礼花头像，头像文件保存在 `artifacts/branding/potato-lottery-logo.png`。

## 已验证与边界

- 切换前只读统计：29 场已开奖、3 场已取消，没有进行中的活动；Outbox 为 confirmed 393、pre_accept_failed 1、cancelled 3，没有待发送或 UNKNOWN 记录。历史失败记录未重发或删除。
- `setMyName`、`setMyProfilePhoto` 成功；`getMe` 回读新名称和身份，`getUserProfilePhotos` 确认头像存在。
- 三个频道的名称、用户名、头像和机器人管理员身份均通过 Telegram API 回读。
- 最终重载后 app/worker 均 healthy；运行容器中的 BOT_ID、三个频道 URL 和 release SHA 已回读。
- Telegram 桌面向新机器人发送 `/start` 和 `/help`，已实际收到首页和使用指南回复；指南显示三个频道按钮，点击使用说明按钮已进入 `@potato_lottery_guide`。
- 更换 Bot 身份不会迁移旧机器人私聊关系、群权限和旧消息按钮；历史业务数据保留。本次未触发真实开奖，未验收报名、开奖公告、中奖私信或手机推送。
- 频道已完成基础资料配置；频道内容运营、自动活动推送与精选审核不属于本次已验证功能。

## 同日补充：频道内容和机器人简介

用户追加授权后，已发布 10 篇频道内容并设置 3 条置顶：

- 使用说明：参与教程、创建教程、门槛与加成、开奖领奖、常见问题，共 5 篇正文，以及带正文链接和按钮的[置顶目录](https://t.me/potato_lottery_guide/9)。
- 抽奖导航：[置顶入口](https://t.me/potato_lottery_nav/4)和活动卡阅读说明。
- 精选推荐：[置顶说明](https://t.me/potato_lottery_featured/4)和推荐信息要求。明确当前暂无已收录活动，不虚构活动、不承诺自动推送或奖品担保。
- 三个频道简介均已更新。机器人资料页短简介和首次打开聊天的详细介绍均已设置默认、zh、en 语言槽位，内容统一为中文。

发布通过 Telegram Bot API 获得 10 条消息的确认回执；三个置顶的消息 ID、正文及频道简介均经 getChat 回读核对。机器人长短简介通过 getMyDescription / getMyShortDescription 在三个语言槽位逐项回读一致。Telegram 桌面已核对三个频道正文，使用说明目录排版及导航跳转正常。

公开文案备份：`artifacts/channel-content-20261018/published-content.md`；操作回执：同目录 `receipts.json`。备份不含 Token。此次只更新 Telegram 内容，无应用代码变更、数据库迁移或服务重启。

## 同日补充：互相提及与简介保留

机器人短简介保留群内定时抽奖、参与进度查询、邀请与活跃加成、开奖结果回查，并列出三个频道的 @ 用户名。详细欢迎介绍保留原有参与、创建、规则和发奖说明，追加导航和精选频道入口。三个频道简介和置顶正文保留原有用途说明，均包含机器人及另外两个频道的 @ 用户名，原有导航按钮保留。

恢复后的短简介为 112 个 Unicode 字符（113 个 UTF-16 代码单元），在 120 字符限制内。默认、zh、en 三个语言槽位以及频道简介和置顶正文的最终回读记录位于 `artifacts/channel-content-20261018/restored-description-receipts.json`。
