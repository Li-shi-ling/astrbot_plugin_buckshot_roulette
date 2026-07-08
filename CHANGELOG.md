# 更新日志

## v1.2.1

- 新增多人轮盘道具 `手铐`、`过期药`、`电话`；手铐为目标型道具，过期药和电话为个人道具但结果公开。
- 新增手铐两段式按钮交互：点击 `手铐` 后发送目标选择消息，目标按钮生成 `轮盘道具 手铐 编号`，并保留手动输入兼容。
- 新增 `放大镜` 到道具池，作为个人道具使用，并在群内公开下一发实弹/空弹。
- 修正手锯效果：下一发若为空弹，手锯效果会落空并消失。
- 扩展轮盘设置：支持道具刷新上限、刷新下限、随机道具刷新数和道具持有上限。

## v1.2.0

- 新增 QQOfficial 群聊多人无庄家“恶魔轮盘”模式，支持 2-10 人房间、公开弹数、回合推进、淘汰和胜负结算。
- 新增 `轮盘绑定` / `轮盘我的名字` / `轮盘改名`，使用插件数据目录下的 `roulette.db` 保存群内 openid 到自定义昵称的绑定。
- 新增 `轮盘创建`、`轮盘加入`、`轮盘开始`、`轮盘开枪`、`轮盘道具`、`轮盘状态`、`轮盘结束` 指令；按钮全部使用 `action.type = 2` 指令按钮。
- 新增 `轮盘设置` 公开默认设置，支持非开房时间修改子弹数、道具刷新量、道具持有上限和开局血量；默认值同步提供 AstrBot 配置模板。
- 道具 MVP 包含啤酒、香烟、手锯、手铐、放大镜、过期药、电话、换向器；放大镜、过期药、电话仍是个人道具，但使用结果会在群内公开。
- 修正手锯效果：下一发子弹若为空弹，手锯效果会落空并消失。
- 补充数据库、规则状态机和 QQOfficial keyboard payload 测试。

## v1.1.8

- 新增 `qqofficial_callback_buttons` 调试指令，发送 `action.type = 1` 的 QQOfficial 回调按钮，便于继续验证 `INTERACTION_CREATE` 是否能触发。
- 保留 `qqofficial_buttons` 的指令按钮路径，两种按钮共用群聊/C2C 被动回复发送逻辑和按钮回调回复文本。

## v1.1.7

- 将 `qqofficial_buttons` 的 A/B 按钮改为 QQOfficial 指令按钮，点击后生成 `qqofficial_button_a` / `qqofficial_button_b` 命令，避免回调按钮依赖 `INTERACTION_CREATE` 导致请求超时。
- 新增 `qqofficial_button_a` 和 `qqofficial_button_b` 指令，分别回复“你按了 A/B”。

## v1.1.6

- 修复按钮点击后无响应的回调接收链路：发送按钮前兜底安装当前 QQOfficial client 的 `on_interaction_create` hook，并补充 `INTERACTION_CREATE` 所需的 interaction intent。
- 增加日志提示：如果 websocket 已连接，补充 interaction intent 后需要重启/重连 QQOfficial 平台才会收到点击事件。

## v1.1.5

- 调整 `qqofficial_buttons` 自定义按钮 payload 为 QQ 官方发送方式中的 `keyboard.content.rows` 结构，避免 API 返回成功但客户端不渲染按钮。

## v1.1.4

- 为 `qqofficial_buttons` 增加详细诊断日志，记录事件类型、原始消息字段、最终发送 payload、API 返回值和按钮回调处理过程。

## v1.1.3

- 修复 `qqofficial_buttons` 发送按钮组件时被识别为主动群消息导致无权限的问题。
- 按钮组件发送会携带原始消息 `msg_id` 和 `msg_seq`，按钮回调回复会携带 `event_id`，优先走 QQOfficial 被动回复路径。
