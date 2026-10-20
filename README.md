# QIQI WeChat Bot

QIQI WeChat Bot 是一个运行在 Windows 上的微信自动化聊天机器人项目，用于中文情感聊天、角色扮演、图片识别、联网搜索、主动消息、提醒和记忆管理等场景。

## 当前版本：8.62

- [下载 QIQI 微信 Bot 8.62 Windows 安装版](https://github.com/xiaochen7808/qiqi-wechat-bot/releases/tag/v8.62)
- 当前 Release 只提供编译后的 EXE，不提供源码下载附件。
- 消息 API 位于“插件管理”，默认关闭，只有有效的自定义 API（free_api）卡密权益可以启用。
- 好感度、背景故事和语音自动切换默认关闭。

当前仓库是私有备份与版本管理仓库。仓库中的 `WeChatBot-6.5/WeChatBot_qiqi` 是历史目录名称，保留该名称用于兼容旧路径；它不是当前发布版本号。普通用户请从上面的 8.62 Release 下载并安装。

## 主要功能

- 多用户、群聊消息处理
- 角色提示词与人设配置
- 图片和表情包识别
- 联网搜索与网页链接内容提取
- 主动消息、定时提醒和主动话题
- 临时记忆、核心记忆、用户资料和人物故事管理
- 本地 Web 配置后台
- 通过付费卡密解锁的消息 API

## 运行环境

- Windows 10/11 64 位
- 微信电脑版 `4.1.2`
- Python `3.9-3.12`，推荐 `3.11`

安装 Python 时请勾选 `Add Python to PATH`。

## 快速开始

1. 打开并登录微信电脑版。
2. 进入 `WeChatBot-6.5/WeChatBot_qiqi`。
3. 双击运行 `Run.bat`。
4. 按脚本提示打开本地配置页面。
5. 填写 API 服务商、模型、API Key、监听对象和角色提示词。
6. 保存配置后启动 Bot。

详细说明可以看：

- [项目说明](WeChatBot-6.5/WeChatBot_qiqi/readme.md)
- [使用教程](WeChatBot-6.5/WeChatBot_qiqi/QIQI微信Bot使用教程.md)
- [8.62 更新日志](https://github.com/xiaochen7808/qiqi-wechat-bot/releases/tag/v8.62)

## 安全提醒

不要把真实 API Key、登录密码、Cookie、聊天记录、记忆文件或包含隐私信息的截图提交到仓库。

当前 `.gitignore` 已排除常见本地运行文件，包括日志、本地密钥、记忆数据、聊天上下文、提醒状态和备份压缩包等。

如果旧 API Key 曾经出现在外部备份或截图里，建议在对应平台后台撤销旧 Key 并重新创建。

## 许可证

本项目基于 KouriChat 修改，并继续遵循 GPL-3.0-or-later 相关条款。更多信息见：

- [LICENSE](WeChatBot-6.5/WeChatBot_qiqi/LICENSE)
- [DEPENDENCIES.txt](WeChatBot-6.5/WeChatBot_qiqi/DEPENDENCIES.txt)
- [LICENSE_COMPLIANCE.md](WeChatBot-6.5/WeChatBot_qiqi/LICENSE_COMPLIANCE.md)
