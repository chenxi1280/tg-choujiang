# 运营服务只读联动接口

日期：2026-10-27。开发中，未部署；不代表报名、发言或中奖私信已经验证。

运营端整体需求见 `/Users/xida/PycharmProjects/tg-yunying/docs/03-feature-designs/lottery-participation-integration-prd.md`。
本服务只提供活动事实读取，不修改本场规则、参与人数、有效消息、票账或开奖结果。

## 启动与权限

新增独立命令 `tg-lottery-integration`，默认不随 Bot 启动，不创建第二个 Telegram Polling 消费者。
环境变量：

- `LOTTERY_INTEGRATION_TOKEN`：至少 32 字符的独立高强度凭据；不得复用 Bot Token。
- `LOTTERY_INTEGRATION_CREATOR_IDS`：允许查询的活动创建者 Telegram ID，逗号分隔，禁止为空。
- `LOTTERY_INTEGRATION_HOST`：默认 `127.0.0.1`。
- `LOTTERY_INTEGRATION_PORT`：默认 `8094`。

数据库地址沿用应用 Settings；只读进程使用无持久连接池的独立短连接，最多两个
并发查询，不更改现有 app/worker 的连接池参数。每次查询在 PostgreSQL
REPEATABLE READ / READ ONLY 事务中执行，SQL timeout 5 秒、请求超时 10 秒。
跨服务器部署须使用受限网络和 HTTPS 终结或 SSH 隧道。已获本次发布授权后，
`integration` Compose profile 可在回环地址启动独立只读服务；专用环境文件权限
为 0600，令牌不写入 Git、发布日志或业务报告。缺少获确认的创建者白名单时，
只发布代码、不启用服务。启用后，未来正式发布要随同应用镜像更新此只读进程，
不得启动第二个 Bot polling、变更既有账号或更改开奖/票账。

## 请求

`POST /v1/campaigns/{public_ref}/snapshot`，使用 `Authorization: Bearer <专用凭据>`。
JSON 字段为 `user_ids` 或 `user_digests`（二选一）、`date_from`、`date_to`。摘要为 Telegram 用户数字 ID 的 SHA-256 小写十六进制，不能用运营库账号 ID 代替。
每次 1～100 个唯一正整数用户 ID（支持 64 位）；日期范围最多 31 天；请求体最大 16 KiB。
没有权限的活动与不存在的活动统一返回 404；无凭据返回 401；非法查询返回 400。

## 响应与事实边界

包含版本、Bot ID、活动稳定 ref/ID、观察时间、计数截止、开奖时刻、经过模型校验的规则及 hash。
只返回请求用户的参与状态：登记、计数起点、资格、按群按日有效基础/活跃消息数、按来源票账总量、中奖条目。
Telegram ID 使用十进制字符串返回，避免浏览器数字精度损失。
不存在的参与者明确返回 `registered=false`；不会自动创建参与者。
不返回会话、密钥、手机号、消息正文、加密邀请链接或运维指定中奖信息。
响应设置 `Cache-Control: no-store`，关闭访问日志避免记录参与查询。

计数日期窗口只约束消息汇总；票账是整场按来源总量，中奖是整场结果。
`behavior_qualified` 沿原活动规则版本区分基础门槛与 admission；不能把它单独当最终复核通过。
`notification_delivery` 返回所选参与者的群进度通知投递状态；`winners[].private_notice` 核对中奖条目与收件用户后返回私信状态；`group_result_notifications` 返回同场开奖群公告状态。没有投递证据明确为未观察，不能推断成功。通知不返回正文，群通知上限 1000 条、参与进度通知上限 5000 条，超限显式拒绝。
查询成功仅证明数据库观察时点，不证明群消息可见或通知送达。

## 验证

`tests/test_integration_api.py` 覆盖鉴权先于数据库、请求边界、只读事务和活动创建者作用域。
`tests/test_integration_snapshot_postgres.py` 在上海专用测试库验证有效发言、资格、用户摘要映射、群进度通知、活动授权及读取不改变参与/票账。
运营端客户端校验返回的 Bot、活动、用户集合、日期窗口、规则 hash 和观察时间；不跟随重定向，不允许用户提供任意目的地址。
