# QIQI WeChat Bot

QIQI WeChat Bot 是一个运行在 Windows 上的微信自动化聊天机器人项目，支持中文情感聊天、角色扮演、图片识别、联网搜索、主动消息、提醒和记忆管理等场景。

## 当前版本：8.62

- [下载 QIQI 微信 Bot 8.62 Windows 安装版](https://github.com/xiaochen7808/qiqi-wechat-bot/releases/tag/v8.62)
- 当前 Release 只提供编译后的 EXE，不提供源码附件。
- 消息 API 位于“插件管理”，默认关闭，只有有效的自定义 API（free_api）卡密权益可以启用。
- 好感度、背景故事和语音自动切换默认关闭。

当前仓库只保留项目说明和发布入口。普通用户请从 8.62 Release 下载并安装，不需要下载或运行 Python 源码。

## 主要功能

- 多用户、群聊消息处理
- 角色提示词与人设配置
- 图片和表情包识别
- 联网搜索与网页链接内容提取
- 主动消息、定时提醒和主动话题
- 临时记忆、核心记忆、用户资料和人物故事管理
- 本地 Web 配置后台
- 通过付费卡密解锁的消息 API

## 安装使用

1. 下载 [8.62 Windows 安装版](https://github.com/xiaochen7808/qiqi-wechat-bot/releases/download/v8.62/QIQI-WeChatBot-Setup-8.62.exe)。
2. 安装并启动 QIQI 微信 Bot。
3. 按配置页面提示填写 API 服务商、模型、API Key、监听对象和角色提示词。
4. 保存配置后启动 Bot。

## 安全提醒

不要把真实 API Key、登录密码、Cookie、聊天记录、记忆文件或包含隐私信息的截图提交到仓库。

消息 API 默认关闭；启用前需要有效的自定义 API（free_api）卡密权益。公网访问必须使用 HTTPS 反向代理、加密隧道或 VPN。

## 发布说明

- 当前稳定版本：8.62
- 发布附件：Windows 安装程序 EXE
- 源码目录和运行数据不放在当前主分支中
