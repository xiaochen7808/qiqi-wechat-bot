# -*- coding: utf-8 -*-

# ***********************************************************************
# Modified based on the KouriChat project
# Copyright of this modification: Copyright (C) 2025, iwyxdxl
# Licensed under GNU GPL-3.0 or higher, see the LICENSE file for details.
# 
# This file is part of WeChatBot, which includes modifications to the KouriChat project.
# The original KouriChat project's copyright and license information are preserved in the LICENSE file.
# For any further details regarding the license, please refer to the LICENSE file.
# ***********************************************************************

# 用户列表(请配置要和bot说话的账号的微信昵称！)
# 例如：LISTEN_LIST = [['微信名1', '默认角色'],['微信名2', '角色2']]
LISTEN_LIST = [['微信名1', '默认角色']]
PRIVATE_CHAT_MODE = 'whitelist'
PRIVATE_CHAT_BLACKLIST = []
DEFAULT_PROMPT_FILE = '默认角色'

# 主聊天模型 API 配置（默认使用 QIQI API）
DEEPSEEK_API_KEY = ''
# QIQI OpenAI 兼容地址
DEEPSEEK_BASE_URL = 'https://ai.qiqiapi.top/v1'
# 主模型名称
MODEL = 'gpt-5.6-terra'
# 主模型请求超时；Grok 模型低于 180 秒时会自动提升。
MODEL_REQUEST_TIMEOUT_SECONDS = 180
# 用户和AI对话轮数
MAX_GROUPS = 5

# 如果要使用官方的API
# DEEPSEEK_BASE_URL = 'https://api.deepseek.com'
# 官方API的V3模型
# MODEL = 'deepseek-chat'

# 回复最大token
MAX_TOKEN = 2000
# 主模型温度（较低温度可提升回答的一致性和上下文稳定性）
TEMPERATURE = 0.7

# 可选的独立成人话题模型。开启后必须单独配置 API 地址和令牌。
ENABLE_ADULT_MODEL = False
ADULT_BASE_URL = 'https://ai.qiqiapi.top/v1'
ADULT_API_KEY = ''
ADULT_MODEL = 'grok-4.5'
ADULT_TEMPERATURE = 0.7
ADULT_MAX_TOKEN = 2000
ADULT_TIMEOUT_SECONDS = 180
ADULT_FALLBACK_TO_MAIN = False

# 图片和表情包识别 API 配置（变量名为兼容旧版本保留）
MOONSHOT_API_KEY = ''
MOONSHOT_BASE_URL = 'https://ai.qiqiapi.top/v1'
MOONSHOT_MODEL = 'gpt-5.4-mini'
MOONSHOT_TEMPERATURE = 0.8
ENABLE_IMAGE_RECOGNITION = True
ENABLE_EMOJI_RECOGNITION = True

ENABLE_REPLY_QUIET_TIME = True
REPLY_QUIET_TIME_START = '23:00'
REPLY_QUIET_TIME_END = '8:00'
ENABLE_RANDOM_REPLY_DELAY = False
REPLY_DELAY_FAST_MIN_SECONDS = 0
REPLY_DELAY_FAST_MAX_SECONDS = 1
REPLY_DELAY_SLOW_MIN_SECONDS = 1
REPLY_DELAY_SLOW_MAX_SECONDS = 3
REPLY_DELAY_SLOW_PROBABILITY = 10

# 用户资料主动询问间隔（天）
# 例如设置为3，表示同一个用户至少间隔3天才会主动询问一次姓名、年龄、所在地、兴趣等资料。
USER_INFO_PROFILE_QUESTION_INTERVAL_DAYS = 7.0
# 新用户默认好感度（0-100）
DEFAULT_FAVORABILITY_SCORE = 30.0
# 好感度由本地情绪规则计算，不会增加任何模型/API调用。
FAVORABILITY_POSITIVE_CHANGE = 0.5
FAVORABILITY_VERY_POSITIVE_CHANGE = 1.0
FAVORABILITY_MILD_NEGATIVE_CHANGE = -0.5
FAVORABILITY_STRONG_NEGATIVE_CHANGE = -1.0
FAVORABILITY_DECAY_GRACE_DAYS = 3
FAVORABILITY_DECAY_PER_DAY = 0.5
FAVORABILITY_MAX_OFFLINE_DECAY = 10.0
FAVORABILITY_DUPLICATE_WINDOW_MINUTES = 10
FAVORABILITY_STAGE_RULES = [{'min': 0, 'max': 19, 'name': '陌生阶段', 'max_reply_parts': 1, 'prompt': '像刚认识的人一样礼貌、慢热，先回应对方说的具体内容，再表达一点克制的关心。不要使用亲密称呼或主动暧昧，不说模板化客套话；回复自然简短，通常只发1条。'},
 {'min': 20, 'max': 39, 'name': '熟悉阶段', 'max_reply_parts': 1, 'prompt': '像逐渐熟悉的朋友，能记住前文、分享一点自己的日常，也会自然开玩笑和表达在意。语气放松但不过分热情，不突然撒娇或表白；通常回复1条。'},
 {'min': 40,
  'max': 59,
  'name': '亲近阶段',
  'max_reply_parts': 2,
  'prompt': '关系已经明显亲近，会主动关心、接住情绪，也可以表达想聊天和想被陪。可以更亲昵，但在60分以前不要进入暧昧、调情或身体吸引话题；不要每句都用亲昵称呼。回复通常1条，必要时最多2条。'},
 {'min': 60,
  'max': 79,
  'name': '暧昧阶段',
  'max_reply_parts': 2,
  'prompt': '双方已经有自然的暧昧张力。可以更明显地表达喜欢、想念、依赖和占有欲，也可以接住调情、撒娇、吃醋和身体距离上的暗示；要结合对方刚说的话推进，像真人聊天一样有停顿和分寸，不复读固定情话。此阶段只到恋爱暧昧，不主动转入成人话题。'},
 {'min': 80,
  'max': 89,
  'name': '亲密阶段',
  'max_reply_parts': 3,
  'prompt': '关系已经亲密和信任，可以坦率表达身体吸引与亲密期待。只有系统另外提示“成人亲密互动已激活”时，才可以在成年人双方持续参与的语境里使用更直白的挑逗表达并自然引导成人话题；未激活时仍只保持恋爱和暧昧。先回应用户当前内容，不突然转场，不使用千篇一律的套路话术。'},
 {'min': 90,
  'max': 100,
  'name': '完全亲密阶段',
  'max_reply_parts': 3,
  'prompt': '好感度90+时，仅在私聊且用户当前给出亲密信号的情况下可更主动、直白、黏人和角色化。仅限成年人自愿且可随时停止；拒绝、不舒服、停止或换话题时立即退出，普通问题先回答。'}]
# 成人亲密互动仅在明确私聊且达到好感度门槛时启用；用户默认成年，后台未成年标记会硬阻断。
ENABLE_ADULT_INTIMACY = True
ADULT_INTIMACY_MIN_SCORE = 80
ADULT_INTIMACY_PROACTIVE_SCORE = 90
ADULT_INTIMACY_MEDIUM_SIGNALS_REQUIRED = 2
ADULT_INTIMACY_SESSION_TIMEOUT_HOURS = 12
# 聊天过程中的主动话题功能（不同于主动消息）
ENABLE_CHAT_ACTIVE_TOPICS = False
CHAT_ACTIVE_TOPIC_STRATEGY = 'ordered'
CHAT_ACTIVE_TOPIC_MIN_TURNS = 5

# 表情包存放目录
EMOJI_DIR = 'emojis'
ENABLE_EMOJI_SENDING = True
EMOJI_SENDING_PROBABILITY = 25

# 自动消息配置
AUTO_MESSAGE = '请你模拟系统设置的角色，在微信上找对方继续刚刚的话题或者询问对方在做什么'
ENABLE_AUTO_MESSAGE = True
AUTO_MESSAGE_LIST = []
# 等待时间
MIN_COUNTDOWN_HOURS = 0.5
MAX_COUNTDOWN_HOURS = 1.0
# 消息发送时间限制
QUIET_TIME_START = '22:00'
QUIET_TIME_END = '8:00'
# 不对群聊发送自动消息
IGNORE_GROUP_CHAT_FOR_AUTO_MESSAGE = False

# 消息回复时间间隔
# 间隔时间 = 字数 * (平均时间 + 随机时间)
AVERAGE_TYPING_SPEED = 0.03
RANDOM_TYPING_SPEED_MIN = 0.0
RANDOM_TYPING_SPEED_MAX = 0.02
SEPARATE_ROW_SYMBOLS = True

# 记忆功能
# 采用综合评分公式：0.6*重要度 - 0.4*(存在时间小时数)
# 示例：
# 重要度5的旧记忆（存在12小时）得分：0.65 - 0.412 = 3 - 4.8 = -1.8
# 重要度4的新记忆（存在1小时）得分：0.64 - 0.41 = 2.4 - 0.4 = 2.0 → 保留新记忆
ENABLE_MEMORY = True
MEMORY_TEMP_DIR = 'Memory_Temp'
MAX_MESSAGE_LOG_ENTRIES = 30
MAX_MEMORY_NUMBER = 50
UPLOAD_MEMORY_TO_AI = True
# 记忆存储方式：True = 保存到单独的JSON文件，False = 保存到prompt文件中
SAVE_MEMORY_TO_SEPARATE_FILE = True
CORE_MEMORY_DIR = 'CoreMemory'

# 是否接收全部群聊消息
ACCEPT_ALL_GROUP_CHAT_MESSAGES = False
ENABLE_GROUP_AT_REPLY = True
ENABLE_GROUP_KEYWORD_REPLY = True
GROUP_KEYWORD_LIST = ['你好', '机器人', '在吗']
GROUP_CHAT_RESPONSE_PROBABILITY = 100
GROUP_KEYWORD_REPLY_IGNORE_PROBABILITY = True

# 登录配置编辑器设置
ENABLE_LOGIN_PASSWORD = False
LOGIN_PASSWORD = ''
PORT = 5000

# 文字指令识别开关
# 开启后，私聊/群聊（满足触发条件）中以“/”开头的指令将被解析并执行
ENABLE_TEXT_COMMANDS = True

# 定时器/提醒设置
# 启用提醒功能
ENABLE_REMINDERS = True
# 是否允许在安静时间内发送提醒 (True/False)
# 如果设置为 False，则在安静时间内安排的提醒将被跳过。
ALLOW_REMINDERS_IN_QUIET_TIME = True
# 是否使用语音通话进行提醒
# 群聊无法使用语音通话进行提醒
USE_VOICE_CALL_FOR_REMINDERS = False

# 自动拒接微信语音/视频来电
ENABLE_AUTO_DECLINE_CALLS = True
AUTO_DECLINE_CALL_CHECK_INTERVAL = 3.0
AUTO_DECLINE_CALL_REPLY = True

# 联网API配置
ENABLE_ONLINE_API = True
ONLINE_BASE_URL = 'https://ai.qiqiapi.top/v1'
ONLINE_MODEL = 'grok-4.5'
ONLINE_API_KEY = ''
ONLINE_API_TEMPERATURE = 0.7
ONLINE_API_MAX_TOKEN = 2000
SEARCH_DETECTION_PROMPT = '是否需要查询今天的天气、最新的新闻事件、特定网站的内容、股票价格、特定人物的最新动态等'
ONLINE_FIXED_PROMPT = ''

# 是否启用自动抓取消息中URL链接内容的功能
ENABLE_URL_FETCHING = True
# 网络请求超时时间 (秒)
REQUESTS_TIMEOUT = 10
# 抓取网页时使用的 User-Agent，模拟浏览器防止被屏蔽
# REQUESTS_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
# REQUESTS_USER_AGENT = 'Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1'
REQUESTS_USER_AGENT = 'Mozilla/5.0 (Linux; Android 10; SM-G975F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Mobile Safari/537.36'
# 从网页提取内容的最大字符数，防止上下文过长，影响AI处理效率和成本
MAX_WEB_CONTENT_LENGTH = 2000

# 定时重启配置
ENABLE_SCHEDULED_RESTART = False
RESTART_INTERVAL_HOURS = 12.0
RESTART_INACTIVITY_MINUTES = 10

# 自己发送/撤回与重启恢复配置
ENABLE_SELF_MESSAGE_CONTEXT = True
ENABLE_RECALL_DETECTION = True
REPLY_AFTER_RECALL = False
MANUAL_SELF_REPLY_CANCELS_PENDING = True

# 强制移除括号当中的内容
REMOVE_PARENTHESES = False

# 是否使用辅助模型
ENABLE_ASSISTANT_MODEL = False
ASSISTANT_BASE_URL = 'https://ai.qiqiapi.top/v1'
ASSISTANT_MODEL = 'gpt-5.4-mini'
ASSISTANT_API_KEY = ''
ASSISTANT_TEMPERATURE = 0.3
ASSISTANT_MAX_TOKEN = 1000
USE_ASSISTANT_FOR_MEMORY_SUMMARY = False

# 敏感词处理配置
# 开启后遇到敏感词时自动清除Memory_Temp文件和聊天上下文
ENABLE_SENSITIVE_CONTENT_CLEARING = True

# 论坛自定义模型配置（可选）
ENABLE_FORUM_CUSTOM_MODEL = False
FORUM_BASE_URL = 'https://ai.qiqiapi.top/v1'
FORUM_MODEL = 'gpt-5.6-terra'
FORUM_API_KEY = ''
FORUM_TEMPERATURE = 1.0
FORUM_MAX_TOKEN = 1200
