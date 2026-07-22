# QIQI 微信 Bot 6.9

QIQI 微信 Bot 是一款运行在 Windows 上的中文情感聊天机器人。它通过微信自动化组件收发消息，并调用 QIQI API 等 OpenAI 兼容接口生成回复，支持角色扮演、图片识别、联网搜索、主动消息、临时记忆和核心记忆。

## 下载与交流

- QQ 交流群：`QQ_GROUP_ID_REMOVED`
- 群主 QQ：`GROUP_OWNER_ID_REMOVED`
- 微信 4.1.2、Python 安装包和项目包均可在交流群中获取。
- QIQI API：<https://ai.qiqiapi.top/>

## 运行环境

- 操作系统：Windows 10/11 64 位
- 微信电脑版：`4.1.2`
- Python：64 位 `3.9-3.12`，推荐 `3.11`
- 安装 Python 时请勾选 `Add Python to PATH`

其他微信或 Python 版本未包含在当前发行包的兼容性范围内。

## 快速上手

1. 安装并登录微信电脑版 4.1.2，保持微信在后台运行。
2. 安装 64 位 Python 3.9-3.12。
3. 解压项目包，双击 `Run.bat` 安装依赖并启动配置后台。
4. 在配置页面选择 API 服务商和模型，填写对应 API Key。
5. 配置监听用户、角色提示词、图片识别、联网搜索和记忆功能。
6. 保存配置后点击 `Start Bot`。

完整步骤见 [QIQI微信Bot使用教程.md](QIQI微信Bot使用教程.md)。

## 主要功能

1. 多用户及群聊消息处理，可为不同用户分配独立角色提示词。
2. 中文情感聊天、角色扮演和按好感度调整互动方式。
3. 图片与表情包识别、情绪判断和表情包回复。
4. 联网搜索与网页链接内容提取。
5. 主动消息、聊天主动话题、定时提醒和语音通话提醒。
6. 临时记忆、核心记忆、用户资料和人物故事背景管理。
7. Web 配置后台、角色论坛和配置迁移。
8. 更新器框架保留；当前尚未配置官方代码仓库时会安全跳过更新检查。
9. 可选独立成人话题模型；必须单独配置 API 地址和 Key，后台未成年标记会强制禁用该模式。

## 版本

- 当前版本：`6.9`

## 项目来源与许可证

- 本项目基于 [KouriChat](https://github.com/KouriChat/KouriChat) 修改，KouriChat 源自 My-Dream-Moments 项目。
- 原项目版权归属：Copyright (C) 2025, umaru。
- 历史修改版本版权归属：Copyright (C) 2025, iwyxdxl。
- 本项目继续遵循 **GNU GPL-3.0 或更高版本**；重新分发时必须保留原作者、历史修改者、许可证和修改说明，并提供相应源代码。
- 完整条款见 [LICENSE](LICENSE)，依赖与分发说明见 [DEPENDENCIES.txt](DEPENDENCIES.txt) 和 [LICENSE_COMPLIANCE.md](LICENSE_COMPLIANCE.md)。

微信自动化私有 wheel 使用其自身许可证，不因本项目采用 GPL 而自动变为 GPL。分发前需确认该组件的使用与再分发授权。
