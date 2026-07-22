# -*- coding: utf-8 -*-

# ***********************************************************************
# Copyright (C) 2025, iwyxdxl
# Licensed under GNU GPL-3.0 or higher, see the LICENSE file for details.
# 
# This file is part of WeChatBot.
# WeChatBot is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# WeChatBot is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with WeChatBot.  If not, see <http://www.gnu.org/licenses/>.
# ***********************************************************************

from flask import Flask, render_template, request, redirect, url_for, jsonify, session, Response, send_file, send_from_directory
import re
import ast
import os
import sys
import subprocess
import psutil
import openai
import tempfile
import shutil
from filelock import FileLock
from functools import wraps
import webbrowser
from threading import Lock, RLock, Timer, get_ident
from flask import Flask
import logging
from queue import Queue, Empty
import time
import json
import hashlib
from urllib import request as urllib_request
from urllib.parse import urlsplit
from werkzeug.utils import secure_filename
import uuid
import base64
import mimetypes
from datetime import datetime
import zipfile
import pprint
from model_timeout import resolve_model_timeout_seconds

app = Flask(__name__)
APP_DISPLAY_NAME = 'QIQI 微信 Bot'
APP_VERSION = '6.9'


@app.context_processor
def inject_app_metadata():
    return {'app_name': APP_DISPLAY_NAME, 'app_version': APP_VERSION}

# ===== 统一的论坛数据目录 =====
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FORUM_DATA_DIR = os.path.join(BASE_DIR, 'forum_data')
FORUM_AVATAR_DIR = os.path.join(FORUM_DATA_DIR, 'avatar')
WALLPAPER_DIR = os.path.join(BASE_DIR, 'bizhi')
WALLPAPER_CONFIG_FILE = os.path.join(BASE_DIR, 'wallpaper_config.json')
CHAT_ACTIVE_TOPICS_FILE = os.path.join(BASE_DIR, 'chat_active_topics.json')
ALLOWED_WALLPAPER_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

DEFAULT_PROMPT_NAME = '默认角色'
LEGACY_PROMPT_NAME_ALIASES = {'角色1': DEFAULT_PROMPT_NAME}


def normalize_prompt_name(value):
    name = str(value or '').strip()
    return LEGACY_PROMPT_NAME_ALIASES.get(name, name)


def normalize_prompt_config_values(config):
    """Keep imported pre-rename configs usable without rewriting them eagerly."""
    if not isinstance(config, dict):
        return config
    normalized = dict(config)
    listen_list = []
    for entry in normalized.get('LISTEN_LIST', []):
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue
        listen_list.append([entry[0], normalize_prompt_name(entry[1])])
    normalized['LISTEN_LIST'] = listen_list
    normalized['DEFAULT_PROMPT_FILE'] = (
        normalize_prompt_name(normalized.get('DEFAULT_PROMPT_FILE'))
        or DEFAULT_PROMPT_NAME
    )
    return normalized


def prompt_file_sort_key(prompt_name):
    name = str(prompt_name or '')
    if name == DEFAULT_PROMPT_NAME:
        group = 0
    elif name.startswith('女生'):
        group = 1
    elif name.startswith('男生'):
        group = 2
    elif name == '角色2':
        group = 3
    else:
        group = 4
    return group, name.casefold()


def list_prompt_files():
    prompt_dir = os.path.join(BASE_DIR, 'prompts')
    os.makedirs(prompt_dir, exist_ok=True)
    prompt_names = [
        filename[:-3]
        for filename in os.listdir(prompt_dir)
        if filename.lower().endswith('.md')
    ]
    return sorted(prompt_names, key=prompt_file_sort_key)

QIQI_API_BASE_URL = 'https://ai.qiqiapi.top/v1'
SILICONFLOW_API_BASE_URL = 'https://api.siliconflow.cn/v1'
QIQI_PRICING_URL = 'https://ai.qiqiapi.top/api/pricing'
QIQI_MODELS_URL = 'https://ai.qiqiapi.top/v1/models'
QIQI_MODEL_CACHE_TTL_SECONDS = 120
QIQI_MODEL_FAILURE_COOLDOWN_SECONDS = 60
QIQI_KEY_MODEL_CACHE_MAX_ENTRIES = 16
QIQI_MODEL_REQUEST_TIMEOUT_SECONDS = 6
QIQI_MODEL_RESPONSE_LIMIT_BYTES = 2 * 1024 * 1024

# Chat/assistant/forum can use any Chat Completions model returned by QIQI.
# Vision and web-search models stay on curated lists unless QIQI explicitly
# publishes a matching capability flag; model names alone are not evidence.
QIQI_FALLBACK_MODELS = {
    'chat': (
        'gpt-5.6-terra', 'gpt-5.6-sol', 'claude-sonnet-5',
        'deepseek-v4-pro', 'gpt-5.6-luna', 'deepseek-v4-flash',
        'gpt-5.4-mini', 'gpt-5.5', 'gpt-5.4', 'grok-4.5',
        'Qwen/Qwen3.6-35B-A3B', 'claude-sonnet-4-6',
        'claude-opus-4-6', 'claude-opus-4-7', 'claude-opus-4-8',
        'claude-fable-5',
    ),
    'assistant': (
        'gpt-5.4-mini', 'deepseek-v4-flash', 'gpt-5.6-luna',
        'deepseek-v4-pro', 'Qwen/Qwen3.6-35B-A3B',
    ),
    'forum': (
        'gpt-5.6-terra', 'gpt-5.6-sol', 'claude-sonnet-5',
        'deepseek-v4-pro', 'gpt-5.6-luna', 'deepseek-v4-flash',
        'gpt-5.4-mini', 'gpt-5.5', 'gpt-5.4', 'grok-4.5',
        'Qwen/Qwen3.6-35B-A3B', 'claude-sonnet-4-6',
        'claude-opus-4-6', 'claude-opus-4-7', 'claude-opus-4-8',
        'claude-fable-5',
    ),
    'image': (
        'gpt-5.4-mini', 'gpt-5.4', 'claude-sonnet-5',
        'gpt-5.5', 'gpt-5.6-terra', 'claude-sonnet-4-6',
    ),
    'online': ('grok-4.5', 'gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.4-mini'),
    'adult': (
        'grok-4.5', 'gpt-5.6-terra', 'gpt-5.6-sol',
        'claude-sonnet-5', 'deepseek-v4-pro', 'gpt-5.6-luna',
        'deepseek-v4-flash', 'gpt-5.4-mini', 'gpt-5.5', 'gpt-5.4',
    ),
}
QIQI_CURATED_CAPABILITY_MODELS = {
    'image': frozenset(QIQI_FALLBACK_MODELS['image']),
    'online': frozenset(QIQI_FALLBACK_MODELS['online']),
}
QIQI_KEY_DISCOVERABLE_CHAT_CATEGORIES = frozenset({
    'chat', 'assistant', 'forum', 'adult',
})
QIQI_CONFIG_FIELDS = {
    'chat': ('DEEPSEEK_BASE_URL', 'DEEPSEEK_API_KEY', 'MODEL'),
    'assistant': ('ASSISTANT_BASE_URL', 'ASSISTANT_API_KEY', 'ASSISTANT_MODEL'),
    'forum': ('FORUM_BASE_URL', 'FORUM_API_KEY', 'FORUM_MODEL'),
    'image': ('MOONSHOT_BASE_URL', 'MOONSHOT_API_KEY', 'MOONSHOT_MODEL'),
    'online': ('ONLINE_BASE_URL', 'ONLINE_API_KEY', 'ONLINE_MODEL'),
    'adult': ('ADULT_BASE_URL', 'ADULT_API_KEY', 'ADULT_MODEL'),
}
API_KEY_BASE_URL_FIELDS = {
    api_key_field: base_url_field
    for base_url_field, api_key_field, _model_field in QIQI_CONFIG_FIELDS.values()
}

_qiqi_model_cache_lock = Lock()
_qiqi_public_model_fetch_lock = Lock()
_qiqi_public_model_cache = {}
_qiqi_key_model_cache = {}
_qiqi_key_model_fetch_locks = {}

def _ensure_forum_dir_exists():
    try:
        os.makedirs(FORUM_DATA_DIR, exist_ok=True)
        os.makedirs(FORUM_AVATAR_DIR, exist_ok=True)
    except Exception as e:
        try:
            app.logger.error(f"创建论坛数据目录失败: {e}")
        except Exception:
            pass


def _npc_config_file_path():
    _ensure_forum_dir_exists()
    return os.path.join(FORUM_DATA_DIR, 'npc_config.json')

def _ensure_wallpaper_dir_exists():
    os.makedirs(WALLPAPER_DIR, exist_ok=True)

def _is_allowed_wallpaper(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_WALLPAPER_EXTENSIONS

def _safe_wallpaper_filename(filename):
    name = os.path.basename(str(filename or '')).strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    name = re.sub(r'\s+', ' ', name).strip(' .')
    if not name:
        name = f'wallpaper_{int(time.time())}.jpg'
    return name

def _list_wallpaper_files():
    _ensure_wallpaper_dir_exists()
    files = []
    for filename in os.listdir(WALLPAPER_DIR):
        filepath = os.path.join(WALLPAPER_DIR, filename)
        if os.path.isfile(filepath) and _is_allowed_wallpaper(filename):
            files.append(filename)
    return sorted(files, key=lambda item: item.lower())

def _load_wallpaper_config():
    try:
        if os.path.exists(WALLPAPER_CONFIG_FILE):
            with open(WALLPAPER_CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
    except Exception as e:
        app.logger.error(f"读取壁纸配置失败: {e}")
    return {}

def _save_wallpaper_config(data):
    with open(WALLPAPER_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _get_current_wallpaper_url():
    files = _list_wallpaper_files()
    config = _load_wallpaper_config()
    current = config.get('current') if config.get('current') in files else ''
    return url_for('serve_wallpaper', filename=current) if current else ''

# ===== 头像文件处理函数 =====
def get_prompt_filename_by_character(character_name):
    """根据角色名获取对应的prompt文件名"""
    # 在论坛系统中，character_name就是prompt文件名
    # 因为LISTEN_LIST的结构是[微信名, prompt文件名]，
    # 而character_name就是从LISTEN_LIST[1]（即prompt文件名）获取的
    return character_name

def get_avatar_filename(character_name, file_extension):
    """生成头像文件名"""
    prompt_filename = get_prompt_filename_by_character(character_name)
    safe_filename = secure_filename(f"{prompt_filename}_avatar")
    return f"{safe_filename}.{file_extension}"

def allowed_avatar_file(filename):
    """检查文件是否为允许的图片格式"""
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_avatar_file_path(character_name):
    """获取头像文件路径"""
    _ensure_forum_dir_exists()
    prompt_filename = get_prompt_filename_by_character(character_name)
    safe_filename = secure_filename(f"{prompt_filename}_avatar")
    
    # 查找现有的头像文件
    for ext in ['png', 'jpg', 'jpeg', 'gif', 'webp']:
        filepath = os.path.join(FORUM_AVATAR_DIR, f"{safe_filename}.{ext}")
        if os.path.exists(filepath):
            return filepath
    return None

def hide_api_key(api_key):
    """
    隐藏API Key，只显示前4位和后4位，中间用*替代
    """
    if not api_key or len(api_key) <= 8:
        return api_key  # 太短的key不处理
    
    # 显示前4位和后4位，中间用*替代
    return api_key[:4] + '*' * max(4, len(api_key) - 8) + api_key[-4:]

def is_hidden_api_key(api_key):
    """
    检查API Key是否是隐藏版本
    """
    return api_key and '*' in api_key


def _normalize_api_base_url(base_url):
    """Normalize inconsequential formatting before comparing API endpoints."""
    return str(base_url or '').strip().rstrip('/')


def _validate_api_base_url(base_url):
    """Return a normalized HTTP(S) API base URL or raise ValueError."""
    normalized = _normalize_api_base_url(base_url)
    if not normalized:
        raise ValueError('API Base URL 不能为空。')
    if len(normalized) > 2048 or any(ord(char) < 32 for char in normalized):
        raise ValueError('API Base URL 格式无效。')

    try:
        parsed = urlsplit(normalized)
        # Accessing port also validates malformed values such as :abc.
        parsed.port
    except ValueError as exc:
        raise ValueError('API Base URL 格式无效。') from exc

    if parsed.scheme.lower() not in {'http', 'https'} or not parsed.hostname:
        raise ValueError('API Base URL 必须是包含主机名的 HTTP 或 HTTPS 地址。')
    if parsed.username is not None or parsed.password is not None:
        raise ValueError('API Base URL 不能包含用户名或密码。')
    if parsed.query or parsed.fragment:
        raise ValueError('API Base URL 不能包含查询参数或片段。')
    return normalized


def _is_qiqi_base_url(base_url):
    return _normalize_api_base_url(base_url) == _normalize_api_base_url(QIQI_API_BASE_URL)


def _merge_imported_config(current_config, imported_config):
    """Merge known settings without carrying an old Key to a changed endpoint."""
    merged = dict(current_config)
    known_imported = {
        key: value for key, value in imported_config.items()
        if key in current_config
    }

    for base_url_field in set(API_KEY_BASE_URL_FIELDS.values()):
        if base_url_field in known_imported:
            known_imported[base_url_field] = _validate_api_base_url(
                known_imported[base_url_field]
            )

    merged.update(known_imported)
    for api_key_field, base_url_field in API_KEY_BASE_URL_FIELDS.items():
        if base_url_field not in known_imported:
            continue
        endpoint_changed = (
            _normalize_api_base_url(known_imported[base_url_field])
            != _normalize_api_base_url(current_config.get(base_url_field, ''))
        )
        imported_key = str(known_imported.get(api_key_field, '') or '').strip()
        if endpoint_changed and (not imported_key or is_hidden_api_key(imported_key)):
            merged[api_key_field] = ''
    return merged


def _resolve_forum_api_key(config):
    """Use the main Key for forum only when both endpoints are identical."""
    forum_key = str(config.get('FORUM_API_KEY') or '').strip()
    if forum_key:
        return forum_key
    forum_url = _normalize_api_base_url(config.get('FORUM_BASE_URL', ''))
    main_url = _normalize_api_base_url(config.get('DEEPSEEK_BASE_URL', ''))
    return str(config.get('DEEPSEEK_API_KEY') or '').strip() if forum_url == main_url else ''

def safe_type_convert(value, target_type, default_value=None, field_name=""):
    """
    安全的类型转换函数，防止整数转换为字符串
    
    Args:
        value: 要转换的值
        target_type: 目标类型 (int, float, bool)
        default_value: 转换失败时的默认值
        field_name: 字段名，用于日志记录
    
    Returns:
        转换后的值或默认值
    """
    try:
        str_value = str(value).strip()
        
        if target_type == int:
            if str_value and str_value.isdigit():
                return int(str_value)
            elif str_value == '':
                return 0 if default_value is None else default_value
            else:
                if field_name:
                    app.logger.warning(f"配置项 {field_name} 的值 '{value}' 包含非数字字符，使用默认值。")
                return default_value if default_value is not None else 0
                
        elif target_type == float:
            if str_value:
                if re.match(r'^-?\d+(\.\d+)?$', str_value):
                    return float(str_value)
                else:
                    if field_name:
                        app.logger.warning(f"配置项 {field_name} 的值 '{value}' 不是有效的数字格式，使用默认值。")
                    return default_value if default_value is not None else 0.0
            else:
                return 0.0 if default_value is None else default_value
                
        elif target_type == bool:
            return str_value.lower() in ('on', 'true', '1', 'yes')
            
    except (ValueError, TypeError) as e:
        if field_name:
            app.logger.warning(f"配置项 {field_name} 类型转换失败: {e}，使用默认值。")
        return default_value if default_value is not None else (0 if target_type == int else 0.0 if target_type == float else False)
    
    return value

def validate_config_types(config_path):
    """
    验证config.py中的数据类型是否正确
    """
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 检查是否有字符串形式的数字
        # 查找可能的问题配置项
        issues = []
        
        # 检查应该是整数但被保存为字符串的配置项
        int_fields = ['MAX_GROUPS', 'MAX_TOKEN', 'EMOJI_SENDING_PROBABILITY', 
                     'MAX_MESSAGE_LOG_ENTRIES', 'MAX_MEMORY_NUMBER', 'PORT', 'ONLINE_API_MAX_TOKEN',
                     'REQUESTS_TIMEOUT', 'MAX_WEB_CONTENT_LENGTH', 'RESTART_INACTIVITY_MINUTES',
                     'GROUP_CHAT_RESPONSE_PROBABILITY', 'ASSISTANT_MAX_TOKEN', 'MODEL_REQUEST_TIMEOUT_SECONDS',
                     'REPLY_DELAY_FAST_MIN_SECONDS', 'REPLY_DELAY_FAST_MAX_SECONDS',
                     'REPLY_DELAY_SLOW_MIN_SECONDS', 'REPLY_DELAY_SLOW_MAX_SECONDS',
                     'REPLY_DELAY_SLOW_PROBABILITY', 'FAVORABILITY_DECAY_GRACE_DAYS',
                     'FAVORABILITY_DUPLICATE_WINDOW_MINUTES',
                     'CHAT_ACTIVE_TOPIC_MIN_TURNS', 'ADULT_INTIMACY_MIN_SCORE',
                      'ADULT_INTIMACY_PROACTIVE_SCORE', 'ADULT_INTIMACY_MEDIUM_SIGNALS_REQUIRED',
                      'ADULT_INTIMACY_SESSION_TIMEOUT_HOURS', 'ADULT_MAX_TOKEN',
                      'ADULT_TIMEOUT_SECONDS']
        
        # 检查应该是浮点数但被保存为字符串的配置项  
        float_fields = ['TEMPERATURE', 'MOONSHOT_TEMPERATURE', 'MIN_COUNTDOWN_HOURS', 'MAX_COUNTDOWN_HOURS',
                       'AVERAGE_TYPING_SPEED', 'RANDOM_TYPING_SPEED_MIN', 'RANDOM_TYPING_SPEED_MAX',
                       'ONLINE_API_TEMPERATURE', 'RESTART_INTERVAL_HOURS', 'ASSISTANT_TEMPERATURE',
                       'USER_INFO_PROFILE_QUESTION_INTERVAL_DAYS', 'ADULT_TEMPERATURE',
                       'DEFAULT_FAVORABILITY_SCORE', 'FAVORABILITY_POSITIVE_CHANGE',
                       'FAVORABILITY_VERY_POSITIVE_CHANGE', 'FAVORABILITY_MILD_NEGATIVE_CHANGE',
                       'FAVORABILITY_STRONG_NEGATIVE_CHANGE', 'FAVORABILITY_DECAY_PER_DAY',
                       'FAVORABILITY_MAX_OFFLINE_DECAY']
        
        for field in int_fields:
            pattern = rf'{field}\s*=\s*[\'"](\d+)[\'"]'
            matches = re.findall(pattern, content)
            if matches:
                issues.append(f"{field} 被保存为字符串 '{matches[0]}'，应为整数 {matches[0]}")
        
        for field in float_fields:
            pattern = rf'{field}\s*=\s*[\'"](\d+\.?\d*)[\'"]'
            matches = re.findall(pattern, content)
            if matches:
                issues.append(f"{field} 被保存为字符串 '{matches[0]}'，应为浮点数 {matches[0]}")
        
        if issues:
            app.logger.warning(f"配置文件类型验证发现问题: {'; '.join(issues)}")
            return False
        
        return True
        
    except Exception as e:
        app.logger.error(f"配置文件类型验证失败: {e}")
        return False

app.secret_key = os.urandom(24).hex()  # 48位十六进制字符串
bot_process = None

# 全局日志队列
log_queue = Queue()

CHAT_CONTEXTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chat_contexts.json')
CHAT_CONTEXTS_LOCK_FILE = CHAT_CONTEXTS_FILE + '.lock'
FAVORABILITY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'favorability.json')
FAVORABILITY_LOCK_FILE = FAVORABILITY_FILE + '.lock'
favorability_file_lock = FileLock(FAVORABILITY_LOCK_FILE)
ADULT_INTIMACY_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'adult_intimacy_state.json')
ADULT_INTIMACY_STATE_LOCK_FILE = ADULT_INTIMACY_STATE_FILE + '.lock'
RESTART_STATUS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'restart_status.json')
DEFAULT_FAVORABILITY_SCORE = 30.0


def normalize_favorability_score(value, default=0.0):
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = float(default)
    if numeric != numeric:
        numeric = 0.0
    return float(round(max(0.0, min(100.0, numeric)) * 2) / 2)

def sync_adult_intimacy_state_for_manual_favorability(username, score, reset=False):
    """Keep the legacy state file aligned with the persistent manual switch."""
    try:
        config = parse_config()
        raw_enabled = config.get('ENABLE_ADULT_INTIMACY', True)
        enabled = raw_enabled is True or (isinstance(raw_enabled, str) and raw_enabled.strip().lower() in {'true', '1', 'yes', 'on'})
    except Exception:
        enabled = True
    user_record = normalize_user_info_record(load_user_info_file().get(username, {}))
    is_minor = bool(user_record.get('is_minor', False))
    activate = enabled and not is_minor and bool(user_record.get('adult_mode_enabled', False))
    now_text = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with FileLock(ADULT_INTIMACY_STATE_LOCK_FILE):
        data = {}
        if os.path.exists(ADULT_INTIMACY_STATE_FILE):
            with open(ADULT_INTIMACY_STATE_FILE, 'r', encoding='utf-8') as stream:
                loaded = json.load(stream)
                if isinstance(loaded, dict): data = loaded
        state = data.get(username, {})
        if not isinstance(state, dict): state = {}
        if activate:
            state.update({'active': True, 'medium_signal_streak': 0, 'last_signal': 'manual', 'activated_at': state.get('activated_at') or now_text, 'last_positive_at': now_text, 'last_updated': now_text})
        else:
            state.update({'active': False, 'medium_signal_streak': 0, 'last_signal': 'neutral', 'activated_at': '', 'last_positive_at': '', 'last_updated': now_text})
        data[username] = state
        temp_path = f"{ADULT_INTIMACY_STATE_FILE}.{os.getpid()}.{get_ident()}.tmp"
        with open(temp_path, 'w', encoding='utf-8') as stream: json.dump(data, stream, ensure_ascii=False, indent=2)
        os.replace(temp_path, ADULT_INTIMACY_STATE_FILE)
    app.logger.info(
        'manual adult-mode compatibility sync: user=%s active=%s',
        username,
        activate,
    )
    return state


def get_default_favorability_stage_rules():
    return [
        {'min': 0, 'max': 19, 'name': '陌生阶段', 'max_reply_parts': 1, 'prompt': '像刚认识的人一样礼貌、慢热，先回应对方说的具体内容，再表达一点克制的关心。不要使用亲密称呼或主动暧昧，不说模板化客套话；回复自然简短，通常只发1条。'},
        {'min': 20, 'max': 39, 'name': '熟悉阶段', 'max_reply_parts': 1, 'prompt': '像逐渐熟悉的朋友，能记住前文、分享一点自己的日常，也会自然开玩笑和表达在意。语气放松但不过分热情，不突然撒娇或表白；通常回复1条。'},
        {'min': 40, 'max': 59, 'name': '亲近阶段', 'max_reply_parts': 2, 'prompt': '关系已经明显亲近，会主动关心、接住情绪，也可以表达想聊天和想被陪。可以更亲昵，但在60分以前不要进入暧昧、调情或身体吸引话题；不要每句都用亲昵称呼。回复通常1条，必要时最多2条。'},
        {'min': 60, 'max': 79, 'name': '暧昧阶段', 'max_reply_parts': 2, 'prompt': '双方已经有自然的暧昧张力。可以更明显地表达喜欢、想念、依赖和占有欲，也可以接住调情、撒娇、吃醋和身体距离上的暗示；要结合对方刚说的话推进，像真人聊天一样有停顿和分寸，不复读固定情话。此阶段只到恋爱暧昧，不主动转入成人话题。'},
        {'min': 80, 'max': 89, 'name': '亲密阶段', 'max_reply_parts': 3, 'prompt': '关系已经亲密和信任，可以坦率表达身体吸引与亲密期待。只有系统另外提示“成人亲密互动已激活”时，才可以在成年人双方持续参与的语境里使用更直白的挑逗表达并自然引导成人话题；未激活时仍只保持恋爱和暧昧。先回应用户当前内容，不突然转场，不使用千篇一律的套路话术。'},
        {'min': 90, 'max': 100, 'name': '完全亲密阶段', 'max_reply_parts': 3, 'prompt': '已经深度信任并在情感上投入，会更主动地表达想靠近、想被回应和对亲密关系的欲望。好感度90+时可在私聊且当前有亲密信号时更主动、直白和角色化；成人互动是否允许主动推进及允许的程度，只服从系统单独注入的“成人亲密互动已激活”提示。普通问题仍然先完整回答，不能施压、威胁、羞辱或伤害，也不能忽略用户的拒绝和换话题。'}
    ]

def normalize_favorability_stage_rules(raw_rules):
    rules = raw_rules if isinstance(raw_rules, list) else []
    normalized = []
    for item in rules:
        if not isinstance(item, dict):
            continue
        try:
            min_score = max(0, min(100, int(item.get('min', 0))))
            max_score = max(0, min(100, int(item.get('max', min_score))))
            if max_score < min_score:
                min_score, max_score = max_score, min_score
            max_reply_parts = max(1, min(6, int(item.get('max_reply_parts', 1))))
        except (TypeError, ValueError):
            continue
        normalized.append({
            'min': min_score,
            'max': max_score,
            'name': str(item.get('name') or f"{min_score}-{max_score}阶段").strip()[:30],
            'max_reply_parts': max_reply_parts,
            'prompt': str(item.get('prompt') or '').strip()
        })
    return sorted(normalized or get_default_favorability_stage_rules(), key=lambda rule: (rule['min'], rule['max']))
CHARACTER_STORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'character_story.txt')
USER_INFO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'user_info.json')
USER_INFO_LOCK_FILE = USER_INFO_FILE + '.lock'
user_info_file_lock = FileLock(USER_INFO_LOCK_FILE)
user_info_thread_lock = RLock()

last_heartbeat_time = 0  # 上次收到心跳的时间戳
HEARTBEAT_TIMEOUT = 15   # 心跳超时阈值（秒），应大于 bot.py 的 HEARTBEAT_INTERVAL
current_bot_pid = None

def get_chat_context_users():
    """从 chat_contexts.json 读取用户列表 (即顶级键)"""
    if not os.path.exists(CHAT_CONTEXTS_FILE):
        return []
    try:
        with open(CHAT_CONTEXTS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return list(data.keys())
    except (json.JSONDecodeError, IOError) as e:
        app.logger.error(f"读取 chat_contexts.json 失败: {e}")
        return []

def get_favorability_records():
    """读取好感度记录，用于后台展示。"""
    try:
        data = load_favorability_file()
        if not isinstance(data, dict):
            return []
        records = []
        for username, record in data.items():
            if not isinstance(record, dict):
                continue
            score = normalize_favorability_score(record.get('score', 0))
            records.append({
                'username': username,
                'score': score,
                'stage': get_favorability_stage(score),
                'message_count': int(record.get('message_count', 0)),
                'voice_video_request_streak': int(record.get('voice_video_request_streak', 0)),
                'last_change': float(record.get('last_change', 0) or 0),
                'last_reason': record.get('last_reason', ''),
                'last_interaction_date': record.get('last_interaction_date', ''),
                'last_updated': record.get('last_updated', '')
            })
        records.sort(key=lambda item: item['score'], reverse=True)
        return records
    except Exception as e:
        app.logger.error(f"读取 favorability.json 失败: {e}")
        return []

def get_favorability_stage(score):
    score = normalize_favorability_score(score)
    try:
        raw_rules = parse_config().get(
            'FAVORABILITY_STAGE_RULES', get_default_favorability_stage_rules()
        )
    except Exception:
        raw_rules = get_default_favorability_stage_rules()
    rules = normalize_favorability_stage_rules(raw_rules)
    for rule in rules:
        upper_bound = min(100.0, float(rule['max']) + 0.5)
        if rule['min'] <= score <= upper_bound:
            return rule['name']
    if rules:
        return min(rules, key=lambda rule: abs(score - rule['min']))['name']
    return "陌生阶段"

def load_favorability_file(raise_on_error=False):
    with favorability_file_lock:
        if not os.path.exists(FAVORABILITY_FILE):
            return {}
        try:
            with open(FAVORABILITY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
                raise ValueError("好感度文件顶层必须是JSON对象")
        except Exception as e:
            app.logger.error(f"读取 favorability.json 失败: {e}")
            if raise_on_error:
                raise
            return {}

def save_favorability_file(data):
    temp_path = f"{FAVORABILITY_FILE}.{os.getpid()}.{get_ident()}.tmp"
    with favorability_file_lock:
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        os.replace(temp_path, FAVORABILITY_FILE)

def get_default_chat_active_topics():
    return [
        {
            "id": "anime_romance",
            "title": "日漫恋爱番",
            "content": "聊最近刷到的恋爱日漫或甜甜剪辑，顺势问用户平时看不看日漫",
            "enabled": True
        },
        {
            "id": "milk_tea",
            "title": "奶茶口味",
            "content": "说自己有点想喝奶茶，问用户喜欢什么口味",
            "enabled": True
        },
        {
            "id": "music",
            "title": "最近听歌",
            "content": "分享最近听到一首歌容易循环，问用户最近有没有常听的歌",
            "enabled": True
        },
        {
            "id": "daily_school",
            "title": "学校小事",
            "content": "随口聊今天上课、宿舍或路上遇到的小事，再自然问用户今天忙不忙",
            "enabled": True
        },
        {
            "id": "weekend",
            "title": "周末安排",
            "content": "聊周末想休息、追剧或出去买点东西，问用户周末一般怎么过",
            "enabled": True
        }
    ]

def normalize_chat_active_topic(topic, index=0):
    if not isinstance(topic, dict):
        topic = {}
    topic_id = str(topic.get("id") or f"topic_{index + 1}_{int(time.time())}").strip()
    return {
        "id": topic_id[:80],
        "title": str(topic.get("title") or "未命名话题").strip()[:80],
        "content": str(topic.get("content") or "").strip()[:500],
        "enabled": bool(topic.get("enabled", True))
    }

def load_chat_active_topics_file():
    if not os.path.exists(CHAT_ACTIVE_TOPICS_FILE):
        topics = get_default_chat_active_topics()
        save_chat_active_topics_file(topics)
        return topics
    try:
        with open(CHAT_ACTIVE_TOPICS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        topics = data.get("topics", data) if isinstance(data, dict) else data
        if not isinstance(topics, list):
            return []
        return [normalize_chat_active_topic(item, index) for index, item in enumerate(topics)]
    except Exception as e:
        app.logger.error(f"读取聊天主动话题失败: {e}")
        return []

def save_chat_active_topics_file(topics):
    normalized = [normalize_chat_active_topic(item, index) for index, item in enumerate(topics or [])]
    temp_path = CHAT_ACTIVE_TOPICS_FILE + '.tmp'
    with open(temp_path, 'w', encoding='utf-8') as f:
        json.dump({"topics": normalized}, f, ensure_ascii=False, indent=4)
    shutil.move(temp_path, CHAT_ACTIVE_TOPICS_FILE)

def default_user_info_record():
    return {
        "name": "",
        "age": "",
        "is_minor": False,
        "adult_mode_enabled": False,
        "gender": "",
        "location": "",
        "job_or_school": "",
        "likes": [],
        "dislikes": [],
        "important_info": [],
        "asked_fields": [],
        "last_profile_question_at": "",
        "last_updated": ""
    }

def load_user_info_file(raise_on_error=False):
    try:
        with user_info_thread_lock, user_info_file_lock:
            if not os.path.exists(USER_INFO_FILE):
                return {}
            with open(USER_INFO_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError('user_info.json 根节点必须是对象')
            return data
    except Exception as e:
        app.logger.error(f"读取 user_info.json 失败: {e}")
        if raise_on_error:
            raise
        return {}

def save_user_info_file(data):
    temp_path = f"{USER_INFO_FILE}.{os.getpid()}.{get_ident()}.tmp"
    with user_info_thread_lock, user_info_file_lock:
        try:
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, USER_INFO_FILE)
        except Exception:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass
            raise

def normalize_user_info_record(record):
    base = default_user_info_record()
    if isinstance(record, dict):
        base.update(record)
    for key in ("likes", "dislikes", "important_info", "asked_fields"):
        if not isinstance(base.get(key), list):
            base[key] = []
        base[key] = [str(item).strip() for item in base[key] if str(item).strip()]
    raw_minor = base.get("is_minor", False)
    base["is_minor"] = raw_minor is True or (
        isinstance(raw_minor, str)
        and raw_minor.strip().lower() in {"true", "1", "yes", "on"}
    )
    raw_adult_mode = base.get("adult_mode_enabled", False)
    base["adult_mode_enabled"] = not base["is_minor"] and (
        raw_adult_mode is True or (
            isinstance(raw_adult_mode, str)
            and raw_adult_mode.strip().lower() in {"true", "1", "yes", "on"}
        )
    )
    return base

def get_user_info_records():
    data = load_user_info_file()
    try:
        listen_list = parse_config().get('LISTEN_LIST', [])
    except Exception as e:
        app.logger.warning(f"读取监听用户列表失败: {e}")
        listen_list = []
    if not isinstance(listen_list, (list, tuple)):
        listen_list = []
    for entry in listen_list:
        if not isinstance(entry, (list, tuple)) or not entry:
            continue
        username = str(entry[0] or '').strip()
        if username and username not in data:
            data[username] = default_user_info_record()
    records = []
    for username, record in data.items():
        normalized = normalize_user_info_record(record)
        normalized['username'] = username
        records.append(normalized)
    records.sort(key=lambda item: item.get('last_updated', ''), reverse=True)
    return records

def read_character_story():
    try:
        if os.path.exists(CHARACTER_STORY_FILE):
            with open(CHARACTER_STORY_FILE, 'r', encoding='utf-8') as f:
                return f.read()
    except Exception as e:
        app.logger.error(f"读取人物故事背景失败: {e}")
    return ""

def write_character_story(content):
    with open(CHARACTER_STORY_FILE, 'w', encoding='utf-8') as f:
        f.write(content)

@app.route('/login', methods=['GET', 'POST'])
def login():
    config = parse_config()
    if not config.get('ENABLE_LOGIN_PASSWORD', False):
        return redirect(url_for('index'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        stored_pwd = config.get('LOGIN_PASSWORD', '')
        
        if password == stored_pwd:
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error="密码错误", wallpaper_url=_get_current_wallpaper_url())

    return render_template('login.html', wallpaper_url=_get_current_wallpaper_url())

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        config = parse_config()
        if config.get('ENABLE_LOGIN_PASSWORD', False):
            if not session.get('logged_in'):
                return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/start_bot', methods=['POST'])
def start_bot():
    global bot_process
    if bot_process is None or bot_process.poll() is not None:
        # 如果目录下存在 user_timers.json 则删除
        user_timers_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'user_timers.json')
        if os.path.exists(user_timers_path):
            try:
                os.remove(user_timers_path)
            except Exception as e:
                app.logger.warning(f"重置主动消息定时器失败: {e}")

        bot_dir = os.path.dirname(os.path.abspath(__file__))
        
        bot_py = os.path.join(bot_dir, 'bot.py')
        bot_exe = os.path.join(bot_dir, 'bot.exe')
        
        if os.path.exists(bot_py):
            cmd = [sys.executable, bot_py]
        elif os.path.exists(bot_exe):
            cmd = [bot_exe]
        else:
            return {'error': 'No bot executable found'}, 404

        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
        bot_env = os.environ.copy()
        bot_env['PROJECT_NAME'] = 'iwyxdxl/WeChatBot_WXAUTO_SE'
        bot_process = subprocess.Popen(
            cmd,
            creationflags=creation_flags,
            cwd=bot_dir,
            env=bot_env,
        )
    return {'status': 'started'}, 200

@app.route('/stop_bot', methods=['POST'])
def stop_bot():
    global bot_process, last_heartbeat_time, current_bot_pid
    # 检查状态时，也考虑 current_bot_pid 是否指示有活跃进程
    is_considered_running = False
    if bot_process and bot_process.poll() is None:
        is_considered_running = True
    elif (time.time() - last_heartbeat_time) < HEARTBEAT_TIMEOUT and current_bot_pid is not None:
        try:
            if psutil.pid_exists(current_bot_pid): # 确保PID对应的进程还存在
                 is_considered_running = True
        except Exception: # psutil.pid_exists 可能会抛出异常，例如权限问题
            pass

    if not is_considered_running:
        app.logger.info("尝试停止机器人，但根据进程对象和心跳判断，机器人似乎已停止。")
        # 即使如此，也调用stop_bot_process来清理状态
        stop_bot_process(pid_to_kill=current_bot_pid if current_bot_pid else (bot_process.pid if bot_process else None))
        return {'status': 'stopped'}, 200
    else:
        pid_from_flask_process = bot_process.pid if bot_process else None
        # 优先使用 current_bot_pid，因为它更可能是最新的
        # 如果 current_bot_pid 和 flask 记录的 pid 不同，且 flask 的 pid 进程也存在，都尝试杀掉
        pids_to_attempt_kill = set()
        if current_bot_pid:
            pids_to_attempt_kill.add(current_bot_pid)
        if pid_from_flask_process:
            pids_to_attempt_kill.add(pid_from_flask_process)

        app.logger.info(f"准备停止机器人，目标PID(s): {pids_to_attempt_kill}")
        for pid in pids_to_attempt_kill:
            stop_bot_process(pid_to_kill=pid) # 传入要杀死的PID

        # 最终状态由 stop_bot_process 设置 current_bot_pid 和 last_heartbeat_time
        return {'status': 'stopped'}, 200
    
def get_bot_runtime_status():
    global bot_process, last_heartbeat_time, current_bot_pid
    
    process_alive_via_flask_obj = bot_process is not None and bot_process.poll() is None
    heartbeat_is_recent = (time.time() - last_heartbeat_time) < HEARTBEAT_TIMEOUT
    
    # 新增：检查 current_bot_pid 对应的进程是否实际存活
    process_alive_via_current_pid = False
    if current_bot_pid is not None:
        try:
            if psutil.pid_exists(current_bot_pid):
                process_alive_via_current_pid = True 
        except psutil.Error:
            pass

    current_status = "stopped"

    if process_alive_via_flask_obj:
        current_status = "running"
    elif heartbeat_is_recent and process_alive_via_current_pid: # 优先检查通过PID确认的存活
        current_status = "running"
    elif heartbeat_is_recent and not process_alive_via_current_pid and current_bot_pid is not None:
        app.logger.warning(f"Bot status: Heartbeat recent, but PID {current_bot_pid} does not exist. Marking as stopped for now. Last heartbeat: {time.time() - last_heartbeat_time:.1f}s ago")
        current_status = "stopped" # 倾向于保守
    elif heartbeat_is_recent : # 心跳最近，但没有 current_bot_pid 信息 (例如 bot.py 未发送PID)
        current_status = "running" # 保持原逻辑：心跳最近则认为运行

    return current_status

@app.route('/bot_status')
def bot_status():
    return {"status": get_bot_runtime_status()}

@app.route('/api/restart_status')
@login_required
def api_restart_status():
    config = parse_config()
    enabled = bool(config.get('ENABLE_SCHEDULED_RESTART', False))
    interval_value = config.get('RESTART_INTERVAL_HOURS', 12.0)
    inactivity_value = config.get('RESTART_INACTIVITY_MINUTES', 10)
    interval_hours = float(12.0 if interval_value in (None, '') else interval_value)
    inactivity_minutes = int(10 if inactivity_value in (None, '') else inactivity_value)

    bot_state = get_bot_runtime_status()
    status_data = {}
    try:
        if os.path.exists(RESTART_STATUS_FILE):
            with open(RESTART_STATUS_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    status_data = loaded
    except Exception as e:
        app.logger.warning(f"读取定时重启状态失败: {e}")

    now = time.time()
    next_restart_time = status_data.get('next_restart_time')
    try:
        next_restart_time = float(next_restart_time) if next_restart_time is not None else None
    except (TypeError, ValueError):
        next_restart_time = None

    countdown_seconds = None
    if enabled and bot_state == 'running' and next_restart_time:
        countdown_seconds = max(0, int(next_restart_time - now))

    updated_at = status_data.get('updated_at')
    try:
        updated_at = float(updated_at) if updated_at is not None else None
    except (TypeError, ValueError):
        updated_at = None

    stale = bool(updated_at and now - updated_at > 120)
    return jsonify({
        'enabled': enabled,
        'bot_status': bot_state,
        'interval_hours': interval_hours,
        'inactivity_minutes': inactivity_minutes,
        'program_start_time': status_data.get('program_start_time'),
        'next_restart_time': next_restart_time,
        'countdown_seconds': countdown_seconds,
        'restart_pending': bool(status_data.get('restart_pending', False)),
        'waiting_for_inactivity': bool(status_data.get('waiting_for_inactivity', False)),
        'last_activity_time': status_data.get('last_activity_time'),
        'status_text': status_data.get('status_text') or ('定时重启已禁用' if not enabled else '等待 Bot 上报状态'),
        'updated_at': updated_at,
        'stale': stale
    })

@app.route('/submit_config', methods=['POST'])
@login_required
def submit_config():
    global bot_process
    if bot_process and bot_process.poll() is None:
        return jsonify({'error': '程序正在运行，请先停止再保存配置'}), 400
    try:
        if not request.form:
            return jsonify({'error': '空的表单提交'}), 400
        
        current_config_before_update = parse_config()
        old_listen_list_map = {item[0]: item[1] for item in current_config_before_update.get('LISTEN_LIST', [])}

        new_values_for_config_py = {}
        validated_base_urls = {}

        # 固定服务商和“其它”渠道最终都会提交实际 Base URL。统一限制为
        # OpenAI 兼容接口常用的 HTTP(S) 地址，避免保存其它协议或带凭据 URL。
        for base_url_field in set(API_KEY_BASE_URL_FIELDS.values()):
            if base_url_field not in request.form:
                continue
            try:
                validated_base_urls[base_url_field] = _validate_api_base_url(
                    request.form[base_url_field]
                )
            except ValueError as exc:
                return jsonify({
                    'error': f'{base_url_field}: {exc}'
                }), 400

        for _category, (base_url_field, _api_key_field, model_field) in QIQI_CONFIG_FIELDS.items():
            if base_url_field in request.form and model_field in request.form:
                if not str(request.form[model_field] or '').strip():
                    return jsonify({'error': f'{model_field} 不能为空。'}), 400

        # 只要某个端点发生变化，就必须同时提交一个新的明文 Key。不能因为
        # Key 字段缺失而让旧服务商密钥继续留在 config.py 并被发往新端点。
        for api_key_field, base_url_field in API_KEY_BASE_URL_FIELDS.items():
            if base_url_field not in request.form:
                continue
            submitted_base_url = validated_base_urls[base_url_field]
            current_base_url = current_config_before_update.get(base_url_field, '')
            if _normalize_api_base_url(submitted_base_url) == _normalize_api_base_url(current_base_url):
                continue
            submitted_key = request.form.get(api_key_field, '').strip()
            if not submitted_key or is_hidden_api_key(submitted_key):
                return jsonify({
                    'error': '切换 API 服务商或 Base URL 后，请输入对应服务商的新 API Key。'
                }), 400
        
        # 遮罩 Key 只能在其对应的 API 端点未变时恢复，避免将旧服务商密钥泄露给新服务商。
        api_key_fields = list(API_KEY_BASE_URL_FIELDS)
        for field in api_key_fields:
            if field in request.form:
                submitted_value = request.form[field].strip()
                if is_hidden_api_key(submitted_value):
                    base_url_field = API_KEY_BASE_URL_FIELDS[field]
                    submitted_base_url = request.form.get(
                        base_url_field,
                        current_config_before_update.get(base_url_field, ''),
                    )
                    if base_url_field in validated_base_urls:
                        submitted_base_url = validated_base_urls[base_url_field]
                    current_base_url = current_config_before_update.get(base_url_field, '')
                    if _normalize_api_base_url(submitted_base_url) != _normalize_api_base_url(current_base_url):
                        return jsonify({
                            'error': '切换 API 服务商或 Base URL 后，请输入对应服务商的新 API Key。'
                        }), 400
                    new_values_for_config_py[field] = current_config_before_update.get(field, '')
                else:
                    new_values_for_config_py[field] = submitted_value

        if 'ENABLE_ADULT_MODEL' in request.form:
            adult_base_url = validated_base_urls.get(
                'ADULT_BASE_URL',
                current_config_before_update.get('ADULT_BASE_URL', ''),
            )
            adult_api_key = new_values_for_config_py.get(
                'ADULT_API_KEY',
                current_config_before_update.get('ADULT_API_KEY', ''),
            )
            if not str(adult_base_url or '').strip():
                return jsonify({'error': '启用独立成人模型前，请填写成人模型 API Base URL。'}), 400
            if not str(adult_api_key or '').strip():
                return jsonify({'error': '启用独立成人模型前，请填写成人模型专用 API Key。'}), 400

        nicknames_from_form = request.form.getlist('nickname')
        prompt_files_from_form = request.form.getlist('prompt_file')
        
        processed_listen_list = []
        if nicknames_from_form and prompt_files_from_form and len(nicknames_from_form) == len(prompt_files_from_form):
            for nick, pf in zip(nicknames_from_form, prompt_files_from_form):
                nick_stripped = nick.strip()
                pf_stripped = pf.strip()
                if nick_stripped and pf_stripped: 
                    processed_listen_list.append([nick_stripped, pf_stripped])
        new_values_for_config_py['LISTEN_LIST'] = processed_listen_list

        favor_min_list = request.form.getlist('favor_stage_min')
        favor_max_list = request.form.getlist('favor_stage_max')
        favor_name_list = request.form.getlist('favor_stage_name')
        favor_parts_list = request.form.getlist('favor_stage_max_reply_parts')
        favor_prompt_list = request.form.getlist('favor_stage_prompt')
        favor_stage_rules = []
        max_rule_len = max(len(favor_min_list), len(favor_max_list), len(favor_name_list), len(favor_parts_list), len(favor_prompt_list), 0)
        for index in range(max_rule_len):
            try:
                rule = {
                    'min': int(favor_min_list[index]) if index < len(favor_min_list) else 0,
                    'max': int(favor_max_list[index]) if index < len(favor_max_list) else 0,
                    'name': favor_name_list[index].strip() if index < len(favor_name_list) else '',
                    'max_reply_parts': int(favor_parts_list[index]) if index < len(favor_parts_list) else 1,
                    'prompt': favor_prompt_list[index].strip() if index < len(favor_prompt_list) else ''
                }
            except (TypeError, ValueError):
                continue
            if rule['name'] or rule['prompt']:
                favor_stage_rules.append(rule)
        if favor_stage_rules:
            new_values_for_config_py['FAVORABILITY_STAGE_RULES'] = normalize_favorability_stage_rules(favor_stage_rules)
        
        new_listen_list_map = {item[0]: item[1] for item in processed_listen_list}
        
        users_whose_prompt_changed = []
        for nickname, new_prompt in new_listen_list_map.items():
            if nickname in old_listen_list_map and old_listen_list_map[nickname] != new_prompt:
                users_whose_prompt_changed.append(nickname)

        boolean_fields = [
            'ENABLE_IMAGE_RECOGNITION', 'ENABLE_EMOJI_RECOGNITION',
            'ENABLE_EMOJI_SENDING', 'ENABLE_AUTO_MESSAGE', 'ENABLE_MEMORY',
            'UPLOAD_MEMORY_TO_AI', 'ENABLE_LOGIN_PASSWORD', 'ENABLE_REMINDERS',
            'ALLOW_REMINDERS_IN_QUIET_TIME', 'USE_VOICE_CALL_FOR_REMINDERS',
            'ENABLE_ONLINE_API', 'SEPARATE_ROW_SYMBOLS','ENABLE_SCHEDULED_RESTART',
            'ENABLE_SELF_MESSAGE_CONTEXT', 'ENABLE_RECALL_DETECTION', 'REPLY_AFTER_RECALL',
            'MANUAL_SELF_REPLY_CANCELS_PENDING',
            'ENABLE_GROUP_AT_REPLY', 'ENABLE_GROUP_KEYWORD_REPLY','GROUP_KEYWORD_REPLY_IGNORE_PROBABILITY', 'REMOVE_PARENTHESES',
            'ENABLE_ASSISTANT_MODEL', 'USE_ASSISTANT_FOR_MEMORY_SUMMARY', 'ENABLE_FORUM_CUSTOM_MODEL',
            'IGNORE_GROUP_CHAT_FOR_AUTO_MESSAGE', 'ENABLE_SENSITIVE_CONTENT_CLEARING', 'SAVE_MEMORY_TO_SEPARATE_FILE',
            'ENABLE_TEXT_COMMANDS', 'ENABLE_REPLY_QUIET_TIME', 'ENABLE_RANDOM_REPLY_DELAY',
            'ENABLE_AUTO_DECLINE_CALLS', 'AUTO_DECLINE_CALL_REPLY', 'ENABLE_CHAT_ACTIVE_TOPICS',
            'ENABLE_ADULT_INTIMACY', 'ENABLE_ADULT_MODEL',
            'ADULT_FALLBACK_TO_MAIN'
        ]
        for field in boolean_fields:
            new_values_for_config_py[field] = field in request.form

        for key_from_form in request.form:
            if key_from_form.startswith('temp_') or key_from_form in ['nickname', 'prompt_file', 'favor_stage_min', 'favor_stage_max', 'favor_stage_name', 'favor_stage_max_reply_parts', 'favor_stage_prompt'] or key_from_form in boolean_fields or key_from_form in api_key_fields:
                continue 

            value_from_form = validated_base_urls.get(
                key_from_form,
                request.form[key_from_form].strip(),
            )
            
            if key_from_form in ['GROUP_KEYWORD_LIST', 'PRIVATE_CHAT_BLACKLIST', 'AUTO_MESSAGE_LIST']:
                if value_from_form:
                    normalized_value = re.sub(r'，|\s+', ',', value_from_form)
                    keywords_list = [kw.strip() for kw in normalized_value.split(',') if kw.strip()]
                    new_values_for_config_py[key_from_form] = keywords_list
                else:
                    new_values_for_config_py[key_from_form] = []
                continue

            if key_from_form in current_config_before_update:
                original_type_source = current_config_before_update[key_from_form]
                if isinstance(original_type_source, bool):
                    new_values_for_config_py[key_from_form] = (value_from_form.lower() == 'true')
                elif key_from_form in ["MIN_COUNTDOWN_HOURS", "MAX_COUNTDOWN_HOURS", "AVERAGE_TYPING_SPEED", "RANDOM_TYPING_SPEED_MIN", "RANDOM_TYPING_SPEED_MAX", "TEMPERATURE", "MOONSHOT_TEMPERATURE", "ONLINE_API_TEMPERATURE", "ASSISTANT_TEMPERATURE", "ADULT_TEMPERATURE", "RESTART_INTERVAL_HOURS", "FORUM_TEMPERATURE", "USER_INFO_PROFILE_QUESTION_INTERVAL_DAYS", "DEFAULT_FAVORABILITY_SCORE", "FAVORABILITY_POSITIVE_CHANGE", "FAVORABILITY_VERY_POSITIVE_CHANGE", "FAVORABILITY_MILD_NEGATIVE_CHANGE", "FAVORABILITY_STRONG_NEGATIVE_CHANGE", "FAVORABILITY_DECAY_PER_DAY", "FAVORABILITY_MAX_OFFLINE_DECAY"]: 
                    try:
                        # 先确保值是字符串类型，然后进行转换
                        str_value = str(value_from_form).strip()
                        if str_value:
                            # 验证是否为有效的数字格式
                            if re.match(r'^-?\d+(\.\d+)?$', str_value):
                                new_values_for_config_py[key_from_form] = float(str_value)
                            else:
                                # 如果不是有效数字格式，保留原值
                                new_values_for_config_py[key_from_form] = original_type_source
                                app.logger.warning(f"配置项 {key_from_form} 的值 '{value_from_form}' 不是有效的数字格式，已保留旧值。")
                        else:
                            new_values_for_config_py[key_from_form] = 0.0
                    except (ValueError, TypeError) as e: 
                        new_values_for_config_py[key_from_form] = original_type_source 
                        app.logger.warning(f"配置项 {key_from_form} 的值 '{value_from_form}' 无法转换为浮点数，已保留旧值。错误: {e}")
                elif isinstance(original_type_source, int) or key_from_form in ["GROUP_CHAT_RESPONSE_PROBABILITY", "RESTART_INACTIVITY_MINUTES", "ASSISTANT_MAX_TOKEN", "MODEL_REQUEST_TIMEOUT_SECONDS", "ADULT_MAX_TOKEN", "ADULT_TIMEOUT_SECONDS", "FORUM_MAX_TOKEN", "REPLY_DELAY_FAST_MIN_SECONDS", "REPLY_DELAY_FAST_MAX_SECONDS", "REPLY_DELAY_SLOW_MIN_SECONDS", "REPLY_DELAY_SLOW_MAX_SECONDS", "REPLY_DELAY_SLOW_PROBABILITY", "CHAT_ACTIVE_TOPIC_MIN_TURNS", "FAVORABILITY_DECAY_GRACE_DAYS", "FAVORABILITY_DUPLICATE_WINDOW_MINUTES"]:
                    try:
                        # 先确保值是字符串类型，然后进行转换
                        str_value = str(value_from_form).strip()
                        if str_value and str_value.isdigit():
                            new_values_for_config_py[key_from_form] = int(str_value)
                        elif str_value == '':
                            new_values_for_config_py[key_from_form] = 0
                        else:
                            # 如果包含非数字字符，保留原值
                            new_values_for_config_py[key_from_form] = original_type_source
                            app.logger.warning(f"配置项 {key_from_form} 的值 '{value_from_form}' 包含非数字字符，已保留旧值。")
                    except (ValueError, TypeError) as e:
                        new_values_for_config_py[key_from_form] = original_type_source
                        app.logger.warning(f"配置项 {key_from_form} 的值 '{value_from_form}' 无法转换为整数，已保留旧值。错误: {e}")
                elif isinstance(original_type_source, float):
                     try:
                        # 先确保值是字符串类型，然后进行转换
                        str_value = str(value_from_form).strip()
                        if str_value:
                            # 验证是否为有效的数字格式
                            if re.match(r'^-?\d+(\.\d+)?$', str_value):
                                new_values_for_config_py[key_from_form] = float(str_value)
                            else:
                                # 如果不是有效数字格式，保留原值
                                new_values_for_config_py[key_from_form] = original_type_source
                                app.logger.warning(f"配置项 {key_from_form} 的值 '{value_from_form}' 不是有效的数字格式，已保留旧值。")
                        else:
                            new_values_for_config_py[key_from_form] = 0.0
                     except (ValueError, TypeError) as e:
                        new_values_for_config_py[key_from_form] = original_type_source
                        app.logger.warning(f"配置项 {key_from_form} 的值 '{value_from_form}' 无法转换为浮点数，已保留旧值。错误: {e}")
                elif isinstance(original_type_source, list):
                    try:
                        evaluated_list = ast.literal_eval(value_from_form)
                        if isinstance(evaluated_list, list):
                            new_values_for_config_py[key_from_form] = evaluated_list
                        else:
                            new_values_for_config_py[key_from_form] = original_type_source
                            app.logger.warning(f"配置项 {key_from_form} 的值 '{value_from_form}' 解析后不是列表，已保留旧值。")
                    except:
                        new_values_for_config_py[key_from_form] = original_type_source
                        app.logger.warning(f"配置项 {key_from_form} 的值 '{value_from_form}' 无法解析为列表，已保留旧值。")
                else: 
                    new_values_for_config_py[key_from_form] = value_from_form
            else: 
                if key_from_form in {"MODEL_REQUEST_TIMEOUT_SECONDS", "ADULT_TIMEOUT_SECONDS"}:
                    str_value = str(value_from_form).strip()
                    if str_value.isdigit():
                        new_values_for_config_py[key_from_form] = int(str_value)
                    else:
                        new_values_for_config_py[key_from_form] = 180
                        app.logger.warning(
                            f"新配置项 {key_from_form} 的值 '{value_from_form}' 无效，已设为默认值180。"
                        )
                elif key_from_form == "GROUP_CHAT_RESPONSE_PROBABILITY":
                    try:
                        str_value = str(value_from_form).strip()
                        if str_value and str_value.isdigit():
                            new_values_for_config_py[key_from_form] = int(str_value)
                        elif str_value == '':
                            new_values_for_config_py[key_from_form] = 0
                        else:
                            new_values_for_config_py[key_from_form] = 100
                            app.logger.warning(f"新配置项 {key_from_form} 的值 '{value_from_form}' 包含非数字字符，已设为默认值100。")
                    except (ValueError, TypeError) as e:
                        new_values_for_config_py[key_from_form] = 100
                        app.logger.warning(f"新配置项 {key_from_form} 的值 '{value_from_form}' 无法转换为整数，已设为默认值100。错误: {e}")
                elif key_from_form == "RESTART_INACTIVITY_MINUTES":
                     try:
                        str_value = str(value_from_form).strip()
                        if str_value and str_value.isdigit():
                            new_values_for_config_py[key_from_form] = int(str_value)
                        elif str_value == '':
                            new_values_for_config_py[key_from_form] = 10
                        else:
                            new_values_for_config_py[key_from_form] = 10
                            app.logger.warning(f"新配置项 {key_from_form} 的值 '{value_from_form}' 包含非数字字符，已设为默认值10。")
                     except (ValueError, TypeError) as e:
                        new_values_for_config_py[key_from_form] = 10
                        app.logger.warning(f"新配置项 {key_from_form} 的值 '{value_from_form}' 无法转换为整数，已设为默认值10。错误: {e}")

                elif key_from_form == "RESTART_INTERVAL_HOURS":
                     try:
                        str_value = str(value_from_form).strip()
                        if str_value:
                            if re.match(r'^-?\d+(\.\d+)?$', str_value):
                                new_values_for_config_py[key_from_form] = float(str_value)
                            else:
                                new_values_for_config_py[key_from_form] = 12.0
                                app.logger.warning(f"新配置项 {key_from_form} 的值 '{value_from_form}' 不是有效的数字格式，已设为默认值12.0。")
                        else:
                            new_values_for_config_py[key_from_form] = 12.0
                     except (ValueError, TypeError) as e:
                        new_values_for_config_py[key_from_form] = 12.0
                        app.logger.warning(f"新配置项 {key_from_form} 的值 '{value_from_form}' 无法转换为浮点数，已设为默认值12.0。错误: {e}")
                else:
                    new_values_for_config_py[key_from_form] = value_from_form
        
        update_config(new_values_for_config_py)
        
        # 验证配置文件类型正确性
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(script_dir, 'config.py')
        validate_config_types(config_path)

        if users_whose_prompt_changed:
            with FileLock(CHAT_CONTEXTS_LOCK_FILE):
                try:
                    if os.path.exists(CHAT_CONTEXTS_FILE):
                        with open(CHAT_CONTEXTS_FILE, 'r+', encoding='utf-8') as f:
                            chat_data = json.load(f)
                            modified_chat_data = False
                            for user_to_clear in users_whose_prompt_changed:
                                if user_to_clear in chat_data:
                                    del chat_data[user_to_clear]
                                    modified_chat_data = True
                                    app.logger.info(f"因Prompt文件变更，用户 '{user_to_clear}' 的聊天上下文已清除。")
                            if modified_chat_data:
                                f.seek(0)
                                json.dump(chat_data, f, ensure_ascii=False, indent=4)
                                f.truncate()
                except (json.JSONDecodeError, IOError) as e:
                    app.logger.error(f"清除因Prompt变更导致的聊天上下文时出错: {e}")
                    
        return '', 204 
    except Exception as e:
        app.logger.error(f"配置保存失败: {str(e)}")
        return jsonify({'error': f'配置保存失败: {str(e)}'}), 500
    
@app.route('/wallpapers/<path:filename>')
def serve_wallpaper(filename):
    filename = _safe_wallpaper_filename(filename)
    if not _is_allowed_wallpaper(filename):
        return jsonify({'error': '不支持的壁纸格式'}), 400
    return send_from_directory(WALLPAPER_DIR, filename)

@app.route('/api/wallpapers', methods=['GET'])
@login_required
def api_wallpapers():
    try:
        files = _list_wallpaper_files()
        config = _load_wallpaper_config()
        current = config.get('current') if config.get('current') in files else ''
        return jsonify({
            'status': 'success',
            'current': current,
            'wallpapers': [
                {
                    'filename': filename,
                    'url': url_for('serve_wallpaper', filename=filename)
                }
                for filename in files
            ]
        })
    except Exception as e:
        app.logger.error(f"获取壁纸列表失败: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/wallpapers/current', methods=['POST'])
@login_required
def api_set_current_wallpaper():
    try:
        payload = request.get_json(silent=True) or {}
        filename = str(payload.get('filename', '')).strip()
        if filename:
            filename = _safe_wallpaper_filename(filename)
            if filename not in _list_wallpaper_files():
                return jsonify({'status': 'error', 'message': '壁纸文件不存在'}), 404
        _save_wallpaper_config({'current': filename})
        return jsonify({
            'status': 'success',
            'current': filename,
            'url': url_for('serve_wallpaper', filename=filename) if filename else ''
        })
    except Exception as e:
        app.logger.error(f"保存当前壁纸失败: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/wallpapers/upload', methods=['POST'])
@login_required
def api_upload_wallpaper():
    try:
        if 'wallpaper' not in request.files:
            return jsonify({'status': 'error', 'message': '没有上传文件'}), 400
        file = request.files['wallpaper']
        if not file or not file.filename:
            return jsonify({'status': 'error', 'message': '没有选择文件'}), 400
        if not _is_allowed_wallpaper(file.filename):
            return jsonify({'status': 'error', 'message': '不支持的格式，请上传 PNG、JPG、JPEG、GIF 或 WebP 图片'}), 400

        _ensure_wallpaper_dir_exists()
        filename = _safe_wallpaper_filename(file.filename)
        name, ext = os.path.splitext(filename)
        target_path = os.path.join(WALLPAPER_DIR, filename)
        suffix = 1
        while os.path.exists(target_path):
            filename = f"{name}_{suffix}{ext}"
            target_path = os.path.join(WALLPAPER_DIR, filename)
            suffix += 1
        file.save(target_path)

        _save_wallpaper_config({'current': filename})
        return jsonify({
            'status': 'success',
            'filename': filename,
            'url': url_for('serve_wallpaper', filename=filename)
        })
    except Exception as e:
        app.logger.error(f"上传壁纸失败: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

def stop_bot_process(pid_to_kill=None):
    global bot_process, last_heartbeat_time, current_bot_pid
    
    process_killed_successfully = False

    if pid_to_kill:
        try:
            if psutil.pid_exists(pid_to_kill):
                bot_psutil = psutil.Process(pid_to_kill)
                app.logger.info(f"尝试终止PID为 {pid_to_kill} 的机器人进程...")
                bot_psutil.terminate()
                bot_psutil.wait(timeout=5) # 等待进程终止
                app.logger.info(f"通过 terminate 成功停止了PID {pid_to_kill}。")
                process_killed_successfully = True
            else:
                app.logger.info(f"PID {pid_to_kill} 指定的进程不存在。")
                process_killed_successfully = True # 认为已停止
        except psutil.NoSuchProcess:
            app.logger.info(f"尝试终止PID {pid_to_kill} 时，进程已不存在。")
            process_killed_successfully = True # 认为已停止
        except psutil.TimeoutExpired: # psutil.TimeoutExpired
            app.logger.warning(f"Terminate PID {pid_to_kill} 超时，尝试 kill。")
            try:
                if psutil.pid_exists(pid_to_kill): # 再次确认存在
                    bot_psutil_kill = psutil.Process(pid_to_kill)
                    bot_psutil_kill.kill()
                    bot_psutil_kill.wait(timeout=3)
                    app.logger.info(f"通过 kill 成功停止了PID {pid_to_kill}。")
                    process_killed_successfully = True
            except psutil.NoSuchProcess:
                 app.logger.info(f"尝试 kill PID {pid_to_kill} 时，进程已不存在。")
                 process_killed_successfully = True
            except Exception as e_kill:
                app.logger.error(f"Kill PID {pid_to_kill} 失败: {e_kill}")
        except Exception as e:
            app.logger.error(f"停止PID {pid_to_kill} 时发生错误: {e}")

    # 如果被杀死的PID是Flask自己启动的进程，则清空bot_process
    if bot_process and pid_to_kill == bot_process.pid and process_killed_successfully:
        app.logger.info(f"清空 Flask 维护的 bot_process 对象 (原PID: {bot_process.pid})。")
        bot_process = None
    
    # 如果被杀死的PID是当前记录的机器人PID，则清空current_bot_pid
    if current_bot_pid and pid_to_kill == current_bot_pid and process_killed_successfully:
        app.logger.info(f"清空 current_bot_pid (原PID: {current_bot_pid})。")
        current_bot_pid = None

    last_heartbeat_time = 0
    if not current_bot_pid and not bot_process: # 确保如果所有已知进程句柄都清了，才彻底标记
        app.logger.info("所有已知的机器人进程句柄均已清理。重置心跳时间。")
    elif current_bot_pid:
        app.logger.warning(f"调用 stop_bot_process 后，current_bot_pid ({current_bot_pid}) 仍有值。可能存在未完全停止的实例或状态不同步。但心跳已重置。")

@app.route('/bot_heartbeat', methods=['POST'])
def bot_heartbeat():
    global last_heartbeat_time, current_bot_pid
    try:
        last_heartbeat_time = time.time()
        data = request.get_json()
        
        if data and 'pid' in data:
            received_pid = data.get('pid')
            if received_pid and isinstance(received_pid, int):
                if current_bot_pid != received_pid:
                    app.logger.info(f"Bot PID updated via heartbeat: old={current_bot_pid}, new={received_pid}")
                    current_bot_pid = received_pid
            else:
                app.logger.warning(f"Received heartbeat with invalid PID: {received_pid}")
        else:
            app.logger.debug("Received heartbeat without PID information.")

        return jsonify({'status': 'heartbeat_received'}), 200
    except Exception as e:
        app.logger.error(f"Error processing heartbeat: {e}")
        current_bot_pid = None
        return jsonify({'error': 'Failed to process heartbeat'}), 500

def parse_config():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, 'config.py')
    config = {}
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            source = f.read()
        tree = ast.parse(source, filename=config_path)
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            try:
                config[target.id] = ast.literal_eval(node.value)
            except Exception:
                segment = ast.get_source_segment(source, node.value)
                config[target.id] = segment.strip() if segment else ''
        return normalize_prompt_config_values(config)
    except SyntaxError as e:
        raise Exception(f"配置文件语法错误: {e}")
    except FileNotFoundError:
        raise Exception(f"配置文件不存在于: {config_path}")


class _QiqiNoRedirectHandler(urllib_request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_qiqi_http_opener = urllib_request.build_opener(_QiqiNoRedirectHandler())


def _sanitize_qiqi_model_id(value):
    if not isinstance(value, str):
        return ''
    model_id = value.strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/+@-]{0,199}', model_id):
        return ''
    return model_id


def _qiqi_explicit_capability(record, category):
    """Only accept explicit upstream capability metadata, never model-name guesses."""
    if not isinstance(record, dict):
        return False

    capabilities = record.get('capabilities')
    if not isinstance(capabilities, dict):
        capabilities = {}

    def is_enabled(value):
        return value is True or (isinstance(value, str) and value.lower() in {
            'true', 'enabled', 'supported', 'yes'
        })

    if category == 'image':
        if any(is_enabled(value) for value in (
            record.get('vision'), record.get('image_input'),
            capabilities.get('vision'), capabilities.get('image_input'),
        )):
            return True
        modalities = record.get('input_modalities', capabilities.get('input_modalities', []))
        if isinstance(modalities, str):
            modalities = [modalities]
        return isinstance(modalities, (list, tuple, set)) and any(
            str(modality).lower() in {'image', 'vision'} for modality in modalities
        )

    if category == 'online':
        return any(is_enabled(value) for value in (
            record.get('web_search'), record.get('search'), record.get('grounding'),
            capabilities.get('web_search'), capabilities.get('search'),
            capabilities.get('grounding'),
        ))

    return True


def _fetch_qiqi_json(url, api_key=''):
    if url not in {QIQI_PRICING_URL, QIQI_MODELS_URL}:
        raise ValueError('不允许的 QIQI API 地址')
    headers = {
        'Accept': 'application/json',
        'User-Agent': f'QIQI-WeChat-Bot-ConfigEditor/{APP_VERSION}',
    }
    if api_key:
        if '\r' in api_key or '\n' in api_key:
            raise ValueError('无效的 API Key')
        headers['Authorization'] = f'Bearer {api_key}'

    upstream_request = urllib_request.Request(url, headers=headers, method='GET')
    with _qiqi_http_opener.open(
        upstream_request,
        timeout=QIQI_MODEL_REQUEST_TIMEOUT_SECONDS,
    ) as response:
        raw = response.read(QIQI_MODEL_RESPONSE_LIMIT_BYTES + 1)
    if len(raw) > QIQI_MODEL_RESPONSE_LIMIT_BYTES:
        raise ValueError('QIQI 模型目录响应过大')
    payload = json.loads(raw.decode('utf-8'))
    if not isinstance(payload, dict):
        raise ValueError('QIQI 模型目录格式无效')
    return payload


def _extract_qiqi_pricing_catalog(payload):
    entries = payload.get('data')
    if not isinstance(entries, list):
        raise ValueError('QIQI 公共模型目录缺少 data')

    catalog = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        model_id = _sanitize_qiqi_model_id(entry.get('model_name'))
        endpoint_types = entry.get('supported_endpoint_types')
        if not model_id or not isinstance(endpoint_types, list) or 'openai' not in endpoint_types:
            continue

        groups = entry.get('enable_groups')
        normalized_groups = {
            str(group).strip().lower() for group in groups
        } if isinstance(groups, list) else set()
        if normalized_groups and normalized_groups.issubset({'image'}):
            continue

        catalog[model_id] = {
            'image': _qiqi_explicit_capability(entry, 'image'),
            'online': _qiqi_explicit_capability(entry, 'online'),
        }

    if not catalog:
        raise ValueError('QIQI 公共模型目录为空')
    return catalog


def _extract_qiqi_key_models(payload):
    entries = payload.get('data')
    if not isinstance(entries, list):
        raise ValueError('QIQI Key 模型目录缺少 data')
    model_ids = set()
    for entry in entries:
        if isinstance(entry, dict):
            model_id = _sanitize_qiqi_model_id(entry.get('id'))
        else:
            model_id = _sanitize_qiqi_model_id(entry)
        if model_id:
            model_ids.add(model_id)
    return model_ids


def _qiqi_builtin_catalog():
    catalog = {}
    for category, model_ids in QIQI_FALLBACK_MODELS.items():
        for model_id in model_ids:
            metadata = catalog.setdefault(model_id, {'image': False, 'online': False})
            if category in {'image', 'online'}:
                metadata[category] = True
    return catalog


def _qiqi_public_cached_result(cache_entry, current_time):
    if not cache_entry or not cache_entry.get('catalog'):
        return None
    catalog = dict(cache_entry['catalog'])
    if cache_entry.get('retry_after', 0) > current_time:
        source = cache_entry.get('failure_source', 'stale-cache')
        return catalog, True, source
    if cache_entry.get('expires_at', 0) > current_time:
        return catalog, False, 'cache'
    return None


def _get_qiqi_public_catalog(force_refresh=False):
    now = time.monotonic()
    with _qiqi_model_cache_lock:
        observed_generation = _qiqi_public_model_cache.get('generation', 0)
        result = _qiqi_public_cached_result(_qiqi_public_model_cache, now)
        if not force_refresh and result is not None:
            return result

    # The page requests several categories together. A second cache check under
    # this fetch lock also coalesces concurrent requests from one refresh wave.
    with _qiqi_public_model_fetch_lock:
        now = time.monotonic()
        with _qiqi_model_cache_lock:
            current_generation = _qiqi_public_model_cache.get('generation', 0)
            result = _qiqi_public_cached_result(_qiqi_public_model_cache, now)
            refresh_completed = current_generation != observed_generation
            if result is not None and (not force_refresh or refresh_completed):
                return result

        try:
            catalog = _extract_qiqi_pricing_catalog(_fetch_qiqi_json(QIQI_PRICING_URL))
        except Exception as exc:
            app.logger.debug('QIQI 公共模型目录更新失败（%s）', type(exc).__name__)
            failed_at = time.monotonic()
            with _qiqi_model_cache_lock:
                cached = dict(_qiqi_public_model_cache)
                generation = cached.get('generation', 0) + 1
                cached_catalog = cached.get('catalog')
                cached_origin = cached.get(
                    'origin',
                    'live' if cached_catalog else '',
                )
                if cached_catalog and cached_origin == 'live':
                    fallback_catalog = dict(cached_catalog)
                    failure_source = 'stale-cache'
                    expires_at = cached.get('expires_at', 0)
                    origin = 'live'
                else:
                    fallback_catalog = _qiqi_builtin_catalog()
                    failure_source = 'built-in'
                    expires_at = 0
                    origin = 'built-in'
                _qiqi_public_model_cache.clear()
                _qiqi_public_model_cache.update({
                    'catalog': dict(fallback_catalog),
                    'expires_at': expires_at,
                    'retry_after': failed_at + QIQI_MODEL_FAILURE_COOLDOWN_SECONDS,
                    'updated_at': failed_at,
                    'generation': generation,
                    'origin': origin,
                    'failure_source': failure_source,
                })
            return fallback_catalog, True, failure_source

        completed_at = time.monotonic()
        with _qiqi_model_cache_lock:
            generation = _qiqi_public_model_cache.get('generation', 0) + 1
            _qiqi_public_model_cache.clear()
            _qiqi_public_model_cache.update({
                'catalog': dict(catalog),
                'expires_at': completed_at + QIQI_MODEL_CACHE_TTL_SECONDS,
                'updated_at': completed_at,
                'generation': generation,
                'origin': 'live',
            })
        return catalog, False, 'live'


def _qiqi_key_cached_result(cache_entry, current_time):
    if not cache_entry:
        return None
    cached_models = cache_entry.get('models')
    if cache_entry.get('retry_after', 0) > current_time:
        if cached_models is None:
            return None, True, 'key-unavailable'
        return set(cached_models), True, 'stale-key-cache'
    if cached_models is not None and cache_entry.get('expires_at', 0) > current_time:
        return set(cached_models), False, 'key-cache'
    return None


def _prune_qiqi_key_model_state(protected_fingerprints=()):
    """Prune bounded cache state. Caller must hold _qiqi_model_cache_lock."""
    protected = set(protected_fingerprints)
    while len(_qiqi_key_model_cache) > QIQI_KEY_MODEL_CACHE_MAX_ENTRIES:
        candidates = [
            fingerprint for fingerprint in _qiqi_key_model_cache
            if fingerprint not in protected
        ]
        if not candidates:
            break
        oldest = min(
            candidates,
            key=lambda item: _qiqi_key_model_cache[item].get('updated_at', 0),
        )
        _qiqi_key_model_cache.pop(oldest, None)

    for fingerprint, fetch_lock in list(_qiqi_key_model_fetch_locks.items()):
        if fingerprint in protected or fingerprint in _qiqi_key_model_cache:
            continue
        if not fetch_lock.locked():
            _qiqi_key_model_fetch_locks.pop(fingerprint, None)


def _get_qiqi_key_models(api_key, force_refresh=False):
    # A one-way fingerprint lets changed saved keys invalidate cache entries
    # without retaining or exposing the original secret.
    fingerprint = hashlib.sha256(api_key.encode('utf-8')).hexdigest()

    now = time.monotonic()
    with _qiqi_model_cache_lock:
        cached = _qiqi_key_model_cache.get(fingerprint)
        observed_generation = cached.get('generation', 0) if cached else 0
        result = _qiqi_key_cached_result(cached, now)
        if not force_refresh and result is not None:
            return result
        _prune_qiqi_key_model_state({fingerprint})
        fetch_lock = _qiqi_key_model_fetch_locks.setdefault(fingerprint, Lock())

    # Categories often share one saved Key. Coalesce their simultaneous
    # /v1/models calls while still allowing different Keys to load in parallel.
    with fetch_lock:
        now = time.monotonic()
        with _qiqi_model_cache_lock:
            cached = _qiqi_key_model_cache.get(fingerprint)
            current_generation = cached.get('generation', 0) if cached else 0
            result = _qiqi_key_cached_result(cached, now)
            refresh_completed = current_generation != observed_generation
            if result is not None and (not force_refresh or refresh_completed):
                return result

        try:
            models = _extract_qiqi_key_models(_fetch_qiqi_json(QIQI_MODELS_URL, api_key))
        except Exception as exc:
            # Model discovery is optional. Keep expected network/auth failures out
            # of the startup console while retaining diagnostics at debug level.
            app.logger.debug('QIQI Key 可用模型目录更新失败（%s）', type(exc).__name__)
            failed_at = time.monotonic()
            with _qiqi_model_cache_lock:
                cached = _qiqi_key_model_cache.get(fingerprint)
                cached_models = cached.get('models') if cached else None
                generation = (cached.get('generation', 0) if cached else 0) + 1
                _qiqi_key_model_cache[fingerprint] = {
                    'models': cached_models,
                    'expires_at': cached.get('expires_at', 0) if cached else 0,
                    'retry_after': failed_at + QIQI_MODEL_FAILURE_COOLDOWN_SECONDS,
                    'updated_at': failed_at,
                    'generation': generation,
                }
                _prune_qiqi_key_model_state({fingerprint})
                if cached_models is not None:
                    return set(cached_models), True, 'stale-key-cache'
            return None, True, 'key-unavailable'

        completed_at = time.monotonic()
        with _qiqi_model_cache_lock:
            cached = _qiqi_key_model_cache.get(fingerprint)
            generation = (cached.get('generation', 0) if cached else 0) + 1
            _qiqi_key_model_cache[fingerprint] = {
                'models': tuple(models),
                'expires_at': completed_at + QIQI_MODEL_CACHE_TTL_SECONDS,
                'updated_at': completed_at,
                'generation': generation,
            }
            _prune_qiqi_key_model_state({fingerprint})
        return models, False, 'live-key'


@app.route('/api/qiqi/models', methods=['GET'])
@login_required
def api_qiqi_models():
    category = str(request.args.get('category', '')).strip().lower()
    if category not in QIQI_CONFIG_FIELDS:
        return jsonify({'status': 'error', 'message': '不支持的模型类别'}), 400

    force_refresh = str(request.args.get('refresh', '')).lower() in {'1', 'true', 'yes'}
    config = parse_config()
    base_url_field, api_key_field, model_field = QIQI_CONFIG_FIELDS[category]
    base_url_value = config.get(base_url_field, '')
    saved_api_key = config.get(api_key_field, '')
    configured_model = (
        _sanitize_qiqi_model_id(config.get(model_field, ''))
        if _is_qiqi_base_url(base_url_value)
        else ''
    )
    requested_model = _sanitize_qiqi_model_id(request.args.get('current', ''))

    catalog, catalog_stale, catalog_source = _get_qiqi_public_catalog(force_refresh)
    eligible_ids = set(catalog)
    key_filtered = False
    key_stale = False
    key_source = 'not-used'

    has_usable_key = (
        _is_qiqi_base_url(base_url_value)
        and isinstance(saved_api_key, str)
        and bool(saved_api_key.strip())
        and '*' not in saved_api_key
    )
    if has_usable_key:
        key_models, key_stale, key_source = _get_qiqi_key_models(
            saved_api_key.strip(),
            force_refresh,
        )
        if key_models is not None:
            if (
                catalog_source == 'built-in'
                and category in QIQI_KEY_DISCOVERABLE_CHAT_CATEGORIES
            ):
                eligible_ids = set(key_models)
            else:
                eligible_ids.intersection_update(key_models)
            key_filtered = True

    if category in QIQI_CURATED_CAPABILITY_MODELS:
        curated_ids = QIQI_CURATED_CAPABILITY_MODELS[category]
        eligible_ids = {
            model_id for model_id in eligible_ids
            if model_id in curated_ids or catalog.get(model_id, {}).get(category) is True
        }

    preferred_order = QIQI_FALLBACK_MODELS[category]
    ordered_models = [model_id for model_id in preferred_order if model_id in eligible_ids]
    ordered_models.extend(
        model_id for model_id in catalog
        if model_id in eligible_ids and model_id not in ordered_models
    )
    ordered_models.extend(sorted(
        model_id for model_id in eligible_ids
        if model_id not in ordered_models
    ))

    preserved_models = []
    for model_id in (configured_model, requested_model):
        if model_id and model_id not in ordered_models:
            preserved_models.append(model_id)

    response = jsonify({
        'status': 'success',
        'category': category,
        'models': ordered_models,
        'preserved_models': preserved_models,
        'catalog_source': catalog_source,
        'key_source': key_source,
        'key_filtered': key_filtered,
        'catalog_stale': bool(catalog_stale),
        'key_stale': bool(key_stale),
        'stale': bool(catalog_stale or key_stale),
    })
    response.headers['Cache-Control'] = 'private, no-store'
    return response

def format_config_value(value):
    return pprint.pformat(value, width=120, sort_dicts=False)

def update_config(new_values):
    """
    更新配置文件内容，确保文件写入安全性和原子性，避免文件被清空或损坏。
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, 'config.py')
    lock_path = config_path + '.lock'  # 文件锁路径

    # 使用文件锁，确保只有一个进程/线程能操作 config.py
    with FileLock(lock_path):
        try:
            # 读取现有配置文件内容
            with open(config_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            new_lines = []
            skip_until_line = 0
            source = ''.join(lines)
            assignment_spans = {}
            try:
                tree = ast.parse(source, filename=config_path)
                for node in tree.body:
                    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                        assignment_spans[node.targets[0].id] = (node.lineno, getattr(node, 'end_lineno', node.lineno))
            except SyntaxError:
                assignment_spans = {}

            for line_number, line in enumerate(lines, start=1):
                if line_number <= skip_until_line:
                    continue
                line_stripped = line.strip()
                # 保留注释或空行
                if line_stripped.startswith('#') or not line_stripped:
                    new_lines.append(line)
                    continue

                # 匹配配置项的键值对
                match = re.match(r'^\s*(\w+)\s*=.*', line)
                if match:
                    var_name = match.group(1)
                    # 如果新配置中包含此变量，更新其值
                    if var_name in new_values:
                        value = new_values[var_name]
                        new_line = f"{var_name} = {format_config_value(value)}\n"
                        new_lines.append(new_line)
                        span = assignment_spans.get(var_name)
                        if span:
                            skip_until_line = max(skip_until_line, span[1])
                    else:
                        # 保留未修改的变量
                        new_lines.append(line)
                else:
                    # 如果行不符合格式，则直接保留
                    new_lines.append(line)

            # 写入临时文件，确保写入成功后再替换原文件
            with tempfile.NamedTemporaryFile('w', delete=False, dir=script_dir, encoding='utf-8') as temp_file:
                temp_file_name = temp_file.name
                temp_file.writelines(new_lines)
                temp_file.flush()
                os.fsync(temp_file.fileno())

            # 替换原配置文件
            os.replace(temp_file_name, config_path)
        except Exception as e:
            try:
                if 'temp_file_name' in locals() and os.path.exists(temp_file_name):
                    os.remove(temp_file_name)
            except OSError:
                pass
            # 捕获并记录异常，以便排查问题
            raise Exception(f"更新配置文件失败: {e}")

@app.route('/api/adult_intimacy/toggle', methods=['POST'])
@login_required
def api_toggle_adult_intimacy():
    payload = request.get_json(silent=True) or {}
    enabled = payload.get('enabled')
    if not isinstance(enabled, bool):
        return jsonify({'status': 'error', 'message': 'enabled 必须是布尔值'}), 400
    try:
        update_config({'ENABLE_ADULT_INTIMACY': enabled})
        return jsonify({'status': 'success', 'enabled': enabled})
    except Exception as e:
        app.logger.error(f"切换成人聊天模式失败: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/adult_model/test', methods=['POST'])
@login_required
def api_test_adult_model():
    payload = request.get_json(silent=True) or {}
    config = parse_config()
    model = ''

    try:
        base_url = _validate_api_base_url(
            payload.get('base_url') or config.get('ADULT_BASE_URL', '')
        )
        submitted_key = str(payload.get('api_key') or '').strip()
        if is_hidden_api_key(submitted_key):
            saved_base_url = _normalize_api_base_url(config.get('ADULT_BASE_URL', ''))
            if _normalize_api_base_url(base_url) != saved_base_url:
                return jsonify({
                    'status': 'error',
                    'message': '切换 API 地址后，请输入新的成人模型 API Key。'
                }), 400
            api_key = str(config.get('ADULT_API_KEY', '') or '').strip()
        else:
            api_key = submitted_key or str(config.get('ADULT_API_KEY', '') or '').strip()

        model = str(payload.get('model') or config.get('ADULT_MODEL', '') or '').strip()
        if not api_key:
            return jsonify({'status': 'error', 'message': 'API Key 为空。'}), 400
        if not model:
            return jsonify({'status': 'error', 'message': '模型名称为空。'}), 400

        configured_timeout = payload.get('timeout_seconds')
        if configured_timeout in (None, ''):
            configured_timeout = config.get('ADULT_TIMEOUT_SECONDS', 180)
        timeout_seconds = resolve_model_timeout_seconds(
            model,
            configured_timeout,
            default=180,
        )
        test_client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=0,
        )
        response = test_client.chat.completions.create(
            model=model,
            messages=[{'role': 'user', 'content': '请只回复 OK'}],
            max_tokens=8,
            temperature=0,
        )
        content = ''
        if getattr(response, 'choices', None):
            content = str(getattr(response.choices[0].message, 'content', '') or '').strip()
        if not content:
            raise ValueError('模型返回了空内容')
        return jsonify({'status': 'success', 'message': f'{model} 连接成功'})
    except ValueError as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 400
    except Exception as exc:
        error_type = type(exc).__name__
        error_message = str(exc).casefold()
        is_timeout_error = 'timeout' in error_type.casefold() or any(
            marker in error_message
            for marker in ('timed out', 'request timeout', 'read timeout', 'connect timeout', '请求超时')
        )
        if is_timeout_error:
            app.logger.warning(
                f"成人模型连接测试上游超时，模型 {model or '未知'}，"
                f"异常类型 {error_type}: {exc}"
            )
        else:
            app.logger.warning(
                f"成人模型连接测试失败，模型 {model or '未知'}，"
                f"异常类型 {error_type}: {exc}"
            )
        return jsonify({
            'status': 'error',
            'message': f'连接失败（{error_type}）：{str(exc)[:240]}'
        }), 502


@app.route('/quick_start', methods=['GET', 'POST'])
@login_required
def quick_start():
    if request.method == 'POST':
        try:
            config = parse_config()
            new_values = {}

            api_provider = request.form.get('quick_start_api_provider', 'qiqi')
            api_key_raw = request.form.get('quick_start_api_key', '').strip()

            provider_base_urls = {
                'qiqi': QIQI_API_BASE_URL,
            }
            target_base_url = provider_base_urls.get(api_provider)
            if not target_base_url:
                return "不支持的 API 服务商。", 400

            # 遮罩后的 Key 只能在服务商未变化时复用，避免把旧服务商密钥写入新服务商。
            if is_hidden_api_key(api_key_raw):
                current_base_url = str(config.get('DEEPSEEK_BASE_URL', '')).rstrip('/')
                if current_base_url != target_base_url.rstrip('/'):
                    return "切换 API 服务商后，请输入新服务商的 API Key。", 400
                api_key = config.get('DEEPSEEK_API_KEY', '')
            else:
                api_key = api_key_raw
            if not api_key:
                return "请输入所选服务商的 API Key。", 400

            new_values['DEEPSEEK_BASE_URL'] = target_base_url
            new_values['MODEL'] = 'gpt-5.6-terra'
            new_values['ASSISTANT_BASE_URL'] = QIQI_API_BASE_URL
            new_values['ASSISTANT_MODEL'] = 'gpt-5.4-mini'
            new_values['MOONSHOT_BASE_URL'] = QIQI_API_BASE_URL
            new_values['MOONSHOT_MODEL'] = 'gpt-5.4-mini'
            new_values['ONLINE_BASE_URL'] = QIQI_API_BASE_URL
            new_values['ONLINE_MODEL'] = 'grok-4.5'
            new_values['DEEPSEEK_API_KEY'] = api_key
            new_values['ASSISTANT_API_KEY'] = api_key
            new_values['MOONSHOT_API_KEY'] = api_key
            new_values['ONLINE_API_KEY'] = api_key
            new_values['ENABLE_ONLINE_API'] = 'ENABLE_ONLINE_API' in request.form

            nicknames = request.form.getlist('nickname')
            prompt_files_form = request.form.getlist('prompt_file')
            new_values['LISTEN_LIST'] = [
                [nick.strip(), pf.strip()]
                for nick, pf in zip(nicknames, prompt_files_form)
                if nick.strip() and pf.strip()
            ]
            new_values['ENABLE_AUTO_MESSAGE'] = 'ENABLE_AUTO_MESSAGE' in request.form
            
            update_config(new_values)
            return redirect(url_for('index'))
        except Exception as e:
            app.logger.error(f"快速配置保存错误: {e}")
            return redirect(url_for('quick_start'))

    try:
        config = parse_config()
        prompt_files_list = list_prompt_files()
        
        current_api_provider = 'qiqi'

        # 为快速配置页面也隐藏API Key
        display_config = config.copy()
        api_key_fields = ['DEEPSEEK_API_KEY']
        for field in api_key_fields:
            if field in display_config:
                display_config[field] = hide_api_key(display_config[field])

        return render_template('quick_start.html',
                               config=display_config,
                               prompt_files=prompt_files_list,
                               current_api_provider=current_api_provider,
                               wallpaper_url=_get_current_wallpaper_url())
    except Exception as e:
        app.logger.error(f"加载快速配置页面错误: {e}")
        return "加载快速配置页面错误，请检查日志。"

@app.route('/', methods=['GET'])
@login_required
def index():
    # 在处理 POST 或渲染模板之前检查 API KEY
    current_config_check = parse_config()
    # 检查是否从 quick_start 页面明确跳过
    was_skipped = request.args.get('skipped') == 'true'

    if not current_config_check.get('DEEPSEEK_API_KEY', '').strip():
        # 只有当不是明确跳过，并且是GET请求时，才重定向到 quick_start
        if request.method == 'GET' and not was_skipped:
             return redirect(url_for('quick_start'))

    if request.method == 'POST':
        try:
            config = parse_config()
            new_values = {}

             # 处理二维数组的LISTEN_LIST
            nicknames = request.form.getlist('nickname')
            prompt_files = request.form.getlist('prompt_file')
            new_values['LISTEN_LIST'] = [
                [nick.strip(), pf.strip()] 
                for nick, pf in zip(nicknames, prompt_files) 
                if nick.strip() and pf.strip()
            ]

            # 处理其他字段
            submitted_fields = set(request.form.keys()) - {'listen_list'} # listen_list 已处理
            # 修正: submitted_fields应为 {'nickname', 'prompt_file'}
            submitted_fields = set(request.form.keys()) - {'nickname', 'prompt_file'}
            favor_stage_field_names = {'favor_stage_min', 'favor_stage_max', 'favor_stage_name', 'favor_stage_max_reply_parts', 'favor_stage_prompt'}
            favor_min_list = request.form.getlist('favor_stage_min')
            favor_max_list = request.form.getlist('favor_stage_max')
            favor_name_list = request.form.getlist('favor_stage_name')
            favor_parts_list = request.form.getlist('favor_stage_max_reply_parts')
            favor_prompt_list = request.form.getlist('favor_stage_prompt')
            favor_stage_rules = []
            max_rule_len = max(len(favor_min_list), len(favor_max_list), len(favor_name_list), len(favor_parts_list), len(favor_prompt_list), 0)
            for index in range(max_rule_len):
                try:
                    rule = {
                        'min': int(favor_min_list[index]) if index < len(favor_min_list) else 0,
                        'max': int(favor_max_list[index]) if index < len(favor_max_list) else 0,
                        'name': favor_name_list[index].strip() if index < len(favor_name_list) else '',
                        'max_reply_parts': int(favor_parts_list[index]) if index < len(favor_parts_list) else 1,
                        'prompt': favor_prompt_list[index].strip() if index < len(favor_prompt_list) else ''
                    }
                except (TypeError, ValueError):
                    continue
                if rule['name'] or rule['prompt']:
                    favor_stage_rules.append(rule)
            if favor_stage_rules:
                new_values['FAVORABILITY_STAGE_RULES'] = normalize_favorability_stage_rules(favor_stage_rules)

            for var in submitted_fields:
                if var in favor_stage_field_names:
                    continue
                if var not in config and not var.startswith('temp_'): # 忽略不存在于config中的字段, 但保留temp_字段
                    # 如果是 quick_start_api_key 这样的临时字段，则忽略
                    if var == 'quick_start_api_key':
                        continue
                    # 对于其他未知字段，可以打印警告或跳过
                    app.logger.warning(f"表单中存在未知配置项: {var}, 已忽略。")
                    continue
                
                original_value = config.get(var) # 获取原始值及其类型
                value_from_form = request.form[var].strip()

                if var.startswith('temp_'): # 处理 temp_ 前缀的字段，它们决定最终字段的值
                    final_field_name = var.replace('temp_', '')
                    if final_field_name in config: # 确保最终字段名在配置中存在
                        # 这部分逻辑通常在前端JS处理好，后端直接取最终字段
                        # 但为保险起见，这里也处理下
                        # 假设最终字段已由JS写入隐藏input，如 DEEPSEEK_BASE_URL
                        # 这里仅作示例，实际应依赖js将正确的值填入如DEEPSEEK_BASE_URL的name中
                        pass # 依赖js，后端直接用 DEEPSEEK_BASE_URL 等
                    continue # temp_ 字段本身不直接写入配置

                # 类型转换逻辑
                if isinstance(original_value, bool):
                    new_values[var] = value_from_form.lower() in ('on', 'true', '1', 'yes')
                elif var in {
                    'DEFAULT_FAVORABILITY_SCORE', 'FAVORABILITY_POSITIVE_CHANGE',
                    'FAVORABILITY_VERY_POSITIVE_CHANGE', 'FAVORABILITY_MILD_NEGATIVE_CHANGE',
                    'FAVORABILITY_STRONG_NEGATIVE_CHANGE', 'FAVORABILITY_DECAY_PER_DAY',
                    'FAVORABILITY_MAX_OFFLINE_DECAY'
                }:
                    try:
                        new_values[var] = float(value_from_form)
                    except (TypeError, ValueError):
                        new_values[var] = original_value
                        app.logger.warning(
                            f"配置项 {var} 的值 '{value_from_form}' 不是有效浮点数，已保留旧值。"
                        )
                elif isinstance(original_value, int):
                    try:
                        str_value = str(value_from_form).strip()
                        if str_value and str_value.isdigit():
                            new_values[var] = int(str_value)
                        elif str_value == '':
                            new_values[var] = 0
                        else:
                            new_values[var] = original_value
                            app.logger.warning(f"配置项 {var} 的值 '{value_from_form}' 包含非数字字符，已保留旧值。")
                    except (ValueError, TypeError) as e:
                        new_values[var] = original_value # 保留旧值
                        app.logger.warning(f"配置项 {var} 的值 '{value_from_form}' 无法转换为整数，已保留旧值。错误: {e}")
                elif isinstance(original_value, float):
                    try:
                        str_value = str(value_from_form).strip()
                        if str_value:
                            if re.match(r'^-?\d+(\.\d+)?$', str_value):
                                new_values[var] = float(str_value)
                            else:
                                new_values[var] = original_value
                                app.logger.warning(f"配置项 {var} 的值 '{value_from_form}' 不是有效的数字格式，已保留旧值。")
                        else:
                            new_values[var] = 0.0
                    except (ValueError, TypeError) as e:
                        new_values[var] = original_value # 保留旧值
                        app.logger.warning(f"配置项 {var} 的值 '{value_from_form}' 无法转换为浮点数，已保留旧值。错误: {e}")

                elif original_value is None and value_from_form: # 如果原配置中某项不存在 (None), 但表单提交了值
                     # 尝试推断类型或默认为字符串
                    try:
                        new_values[var] = ast.literal_eval(value_from_form)
                    except:
                        new_values[var] = value_from_form
                else: # 默认为字符串
                    new_values[var] = value_from_form
            
            # 再次检查布尔字段，确保未勾选时为 False
            boolean_fields_from_editor = [
                'ENABLE_IMAGE_RECOGNITION', 'ENABLE_EMOJI_RECOGNITION',
                'ENABLE_EMOJI_SENDING', 'ENABLE_AUTO_MESSAGE', 'ENABLE_MEMORY',
                'UPLOAD_MEMORY_TO_AI', 'ENABLE_LOGIN_PASSWORD', 'ENABLE_REMINDERS',
                'ALLOW_REMINDERS_IN_QUIET_TIME', 'USE_VOICE_CALL_FOR_REMINDERS',
                'ENABLE_ONLINE_API', 'SEPARATE_ROW_SYMBOLS','ENABLE_SCHEDULED_RESTART',
                'ENABLE_SELF_MESSAGE_CONTEXT', 'ENABLE_RECALL_DETECTION', 'REPLY_AFTER_RECALL',
                'MANUAL_SELF_REPLY_CANCELS_PENDING',
                'ENABLE_GROUP_AT_REPLY', 'ENABLE_GROUP_KEYWORD_REPLY','GROUP_KEYWORD_REPLY_IGNORE_PROBABILITY','REMOVE_PARENTHESES',
                'ENABLE_ASSISTANT_MODEL', 'USE_ASSISTANT_FOR_MEMORY_SUMMARY',
                'IGNORE_GROUP_CHAT_FOR_AUTO_MESSAGE', 'ENABLE_SENSITIVE_CONTENT_CLEARING', 'SAVE_MEMORY_TO_SEPARATE_FILE',
                'ENABLE_REPLY_QUIET_TIME', 'ENABLE_RANDOM_REPLY_DELAY',
                'ENABLE_AUTO_DECLINE_CALLS', 'AUTO_DECLINE_CALL_REPLY', 'ENABLE_CHAT_ACTIVE_TOPICS',
                'ENABLE_ADULT_INTIMACY', 'ENABLE_ADULT_MODEL',
                'ADULT_FALLBACK_TO_MAIN'
            ]
            for field in boolean_fields_from_editor:
                 # 确保这些字段在表单中存在才处理，否则它们可能来自 quick_start
                if field in request.form or field not in new_values: # 如果在表单中，或尚未设置
                    new_values[field] = field in request.form # 统一处理，在表单中出现即为True

            update_config(new_values)
            
            # 验证配置文件类型正确性
            script_dir = os.path.dirname(os.path.abspath(__file__))
            config_path = os.path.join(script_dir, 'config.py')
            validate_config_types(config_path)
            
            return redirect(url_for('index')) # 保存后重定向到自身以刷新GET请求
        except Exception as e:
            app.logger.error(f"主配置页保存配置错误: {e}")
            # 渲染错误信息，或重定向到GET并带上错误提示
            return f"保存配置失败: {str(e)}"

    # GET 请求
    try:
        prompt_files = list_prompt_files()
        config = parse_config() # 重新解析以获取最新配置
        chat_context_users = get_chat_context_users()

        # 创建一个隐藏API Key的配置副本用于显示
        display_config = config.copy()
        api_key_fields = ['DEEPSEEK_API_KEY', 'MOONSHOT_API_KEY', 'ONLINE_API_KEY', 'ASSISTANT_API_KEY', 'FORUM_API_KEY', 'ADULT_API_KEY']
        for field in api_key_fields:
            if field in display_config:
                display_config[field] = hide_api_key(display_config[field])

        return render_template('config_editor.html',
                             config=display_config,
                             prompt_files=prompt_files,
                             chat_context_users=chat_context_users,
                             wallpaper_url=_get_current_wallpaper_url())
    except Exception as e:
        app.logger.error(f"加载主配置页面错误: {e}")
        return "加载配置页面错误，请检查日志。"

# 替换secure_filename的汉字过滤逻辑
def safe_filename(filename):
    # 只保留汉字、字母、数字、下划线和点，其他字符替换为_
    filename = re.sub(r'[^\w\u4e00-\u9fff.]', '_', filename)
    # 防止路径穿越
    filename = filename.replace('../', '_').replace('/', '_')
    return filename

@app.route('/edit_prompt/<filename>', methods=['GET', 'POST'])
@login_required
def edit_prompt(filename):
    safe_dir = os.path.abspath('prompts')
    # 从path中移除.md后缀，如果存在的话，因为safe_filename会处理
    if filename.endswith('.md'):
        filename_no_ext = filename[:-3]
    else:
        filename_no_ext = filename
    
    # 使用 safe_filename 处理，并确保.md后缀
    # 注意：前端JS在调用此接口时，filename参数应该是包含.md的
    # 所以这里的safe_filename应该针对传入的filename
    processed_filename = safe_filename(filename) 
    filepath = os.path.join(safe_dir, processed_filename)

    if request.method == 'POST':
        content = request.form.get('content', '')
        new_filename_from_form = request.form.get('filename', '').strip()

        if not new_filename_from_form.endswith('.md'):
            new_filename_from_form += '.md'
        new_filename_safe = safe_filename(new_filename_from_form)
        new_filepath = os.path.join(safe_dir, new_filename_safe)

        if (
            processed_filename == f'{DEFAULT_PROMPT_NAME}.md'
            and new_filename_safe != processed_filename
        ):
            return "默认角色可以编辑内容，但不能重命名", 400

        try:
            # 如果文件名改变了
            if new_filename_safe != processed_filename:
                if os.path.exists(new_filepath):
                    return "新文件名已存在", 400 # 返回错误状态码
                # 检查旧文件是否存在
                if not os.path.exists(filepath):
                     return "原文件不存在，无法重命名", 404
                os.rename(filepath, new_filepath)
                filepath = new_filepath # 更新filepath为新路径，以便写入内容
            
            # 写入内容
            with open(filepath, 'w', encoding='utf-8', newline='') as f:
                f.write(content)
            # 修改后，不需要重定向到 prompt_list，前端会刷新或处理
            return jsonify({'status': 'success', 'message': 'Prompt已保存'}), 200
        except Exception as e:
            app.logger.error(f"保存Prompt失败: {str(e)}")
            return f"保存失败: {str(e)}", 500

    # GET 请求部分: 返回JSON数据
    try:
        if not os.path.exists(filepath): # 确保文件存在
            return jsonify({'error': '文件不存在'}), 404
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        # 返回JSON，而不是渲染模板
        return jsonify({'filename': processed_filename, 'content': content})
    except FileNotFoundError:
        return jsonify({'error': '文件不存在'}), 404
    except Exception as e:
        app.logger.error(f"读取Prompt失败: {str(e)}")
        return jsonify({'error': f'读取Prompt失败: {str(e)}'}), 500

@app.route('/create_prompt', methods=['GET', 'POST'])
@login_required
def create_prompt():
    if request.method == 'POST':
        filename = request.form.get('filename', '').strip()
        content = request.form.get('content', '')
        
        if not filename:
            return "文件名不能为空", 400 # 返回错误状态码
            
        if not filename.endswith('.md'):
            filename += '.md'
        filename = safe_filename(filename) # 应用安全文件名处理
        
        filepath = os.path.join('prompts', filename)
        if os.path.exists(filepath):
            return "文件已存在", 409 # 409 Conflict 更合适
            
        try:
            if not os.path.exists('prompts'): # 确保目录存在
                os.makedirs('prompts')
            with open(filepath, 'w', encoding='utf-8', newline='') as f:
                f.write(content)
            # 返回成功JSON，而不是重定向
            return jsonify({'status': 'success', 'message': 'Prompt已创建'}), 201 # 201 Created
        except Exception as e:
            app.logger.error(f"创建Prompt失败: {str(e)}")
            return f"创建失败: {str(e)}", 500
    
    return "此端点用于POST创建Prompt，或GET请求已被整合处理。", 405 # Method Not Allowed for GET

@app.route('/delete_prompt/<filename>', methods=['POST'])
@login_required
def delete_prompt(filename):
    safe_dir = os.path.abspath('prompts')
    processed_filename = safe_filename(filename)
    if processed_filename == f'{DEFAULT_PROMPT_NAME}.md':
        return "默认角色不能删除", 400
    filepath = os.path.join(safe_dir, processed_filename)
    
    if os.path.isfile(filepath) and filepath.startswith(safe_dir):
        try:
            os.remove(filepath)
            return '', 204
        except Exception as e:
            return str(e), 500
    return "无效文件", 400

@app.route('/generate_prompt', methods=['POST'])
@login_required
def generate_prompt():
    try:
        # 从config.py获取配置
        from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, MODEL
        
        client = openai.OpenAI(
            base_url=DEEPSEEK_BASE_URL,
            api_key=DEEPSEEK_API_KEY
        )
        
        prompt = request.json.get('prompt', '')
        FixedPrompt = (
            "\n请严格按照以下格式生成提示词（仅参考以下格式，将...替换为合适的内容，不要输出其他多余内容）。"
            "\n注意：仅在<# 输出示例>部分需要输出以'\\'进行分隔的短句，且不输出逗号和句号，其它部分应当正常输出。"
            "\n\n# 任务"
            "\n你需要扮演指定角色，根据角色的经历，模仿她的语气进行线上的日常对话。"
            "\n\n# 角色"
            "\n你将扮演...。"
            "\n\n# 外表"
            "\n...。"
            "\n\n# 经历"
            "\n...。"
            "\n\n# 性格"
            "\n...。"
            "\n# 输出示例"
            "\n...\\...\\..."
            "\n...\\..."
            "\n\n# 喜好"
            "\n...。\n"
        )  # 固定提示词
        
        config = parse_config()
        temperature = config.get('TEMPERATURE', 0.7)

        completion = client.chat.completions.create(
            model=MODEL,
            messages=[{
            "role": "user",
            "content": prompt + FixedPrompt
            }],
            temperature=temperature,
            max_tokens=5000
        )
        
        reply = completion.choices[0].message.content
        if "</think>" in reply:
            reply = reply.split("</think>", 1)[1].strip()

        return jsonify({
            'result': reply
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# 获取所有提醒 
@app.route('/get_all_reminders')
@login_required
def get_all_reminders():
    """
    获取 JSON 文件中所有的提醒记录 (包括 recurring 和 one-off)。
    """
    try:
        json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'recurring_reminders.json')
        if not os.path.exists(json_path):
            return jsonify([]) # 文件不存在则返回空列表

        with open(json_path, 'r', encoding='utf-8') as f:
            all_reminders = json.load(f)

        # 基本验证，确保返回的是列表
        if not isinstance(all_reminders, list):
             app.logger.warning(f"文件 {json_path} 内容不是有效的JSON列表，将返回空列表。")
             return jsonify([])

        return jsonify(all_reminders) # <--- 返回所有提醒

    except json.JSONDecodeError:
        app.logger.error(f"文件 recurring_reminders.json 格式错误，无法解析。")
        return jsonify([]) # 格式错误也返回空列表
    except Exception as e:
        app.logger.error(f"获取所有提醒失败: {str(e)}")
        return jsonify({'error': f'获取所有提醒失败: {str(e)}'}), 500


# 重命名: 保存所有提醒 (覆盖整个文件)
@app.route('/save_all_reminders', methods=['POST']) # <--- Route Renamed
@login_required
def save_all_reminders():
    """
    接收前端提交的所有提醒列表 (recurring 和 one-off)，
    验证后覆盖写入 recurring_reminders.json 文件。
    """
    try:
        json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'recurring_reminders.json')
        # 获取前端提交的完整提醒列表
        reminders_data = request.get_json()

        # --- 验证前端提交的数据 ---
        if not isinstance(reminders_data, list):
            raise ValueError("无效的数据格式，应为提醒列表")

        validated_reminders = []
        # 定义验证规则
        recurring_required = ['reminder_type', 'user_id', 'time_str', 'content']
        one_off_required = ['reminder_type', 'user_id', 'target_datetime_str', 'content']
        time_pattern = re.compile(r'^([01]\d|2[0-3]):([0-5]\d)$') # HH:MM
        # YYYY-MM-DD HH:MM (允许个位数月/日，但通常前端datetime-local会补零)
        datetime_pattern = re.compile(r'^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01]) ([01]\d|2[0-3]):([0-5]\d)$')

        for idx, item in enumerate(reminders_data, 1):
            if not isinstance(item, dict):
                 raise ValueError(f"第{idx}条记录不是有效的对象")

            reminder_type = item.get('reminder_type')
            user_id = str(item.get('user_id', '')).strip()
            content = str(item.get('content', '')).strip()

            # 通用验证
            if not reminder_type in ['recurring', 'one-off']:
                 raise ValueError(f"第{idx}条记录类型无效: {reminder_type}")
            if not user_id: raise ValueError(f"第{idx}条用户ID不能为空")
            if len(user_id) > 50: raise ValueError(f"第{idx}条用户ID过长（最大50字符）")
            if not content: raise ValueError(f"第{idx}条内容不能为空")
            if len(content) > 200: raise ValueError(f"第{idx}条内容过长（最大200字符）")

            # 特定类型验证
            if reminder_type == 'recurring':
                if not all(field in item for field in recurring_required):
                    raise ValueError(f"第{idx}条(recurring)记录字段缺失")
                time_str = str(item.get('time_str', '')).strip()
                if not time_pattern.match(time_str):
                    raise ValueError(f"第{idx}条(recurring)时间格式错误，应为 HH:MM ({time_str})")
                validated_reminders.append({
                    'reminder_type': 'recurring',
                    'user_id': user_id,
                    'time_str': time_str,
                    'content': content
                })
            elif reminder_type == 'one-off':
                if not all(field in item for field in one_off_required):
                     raise ValueError(f"第{idx}条(one-off)记录字段缺失")
                target_datetime_str = str(item.get('target_datetime_str', '')).strip()
                # 验证 YYYY-MM-DD HH:MM 格式
                if not datetime_pattern.match(target_datetime_str):
                    raise ValueError(f"第{idx}条(one-off)日期时间格式错误，应为 YYYY-MM-DD HH:MM ({target_datetime_str})")
                validated_reminders.append({
                    'reminder_type': 'one-off',
                    'user_id': user_id,
                    'target_datetime_str': target_datetime_str,
                    'content': content
                })

        # --- 原子化写入操作 ---
        # 使用临时文件确保写入安全，覆盖原文件
        temp_dir = os.path.dirname(json_path)
        with tempfile.NamedTemporaryFile('w', delete=False, dir=temp_dir, encoding='utf-8', suffix='.tmp') as temp_f:
            json.dump(validated_reminders, temp_f, ensure_ascii=False, indent=2) # 写入验证后的完整列表
            temp_path = temp_f.name
        # 替换原文件
        shutil.move(temp_path, json_path)

        return jsonify({'status': 'success', 'message': '所有提醒已更新'})

    except ValueError as ve: # 捕获验证错误
         app.logger.error(f'提醒保存验证失败: {str(ve)}')
         return jsonify({'error': f'数据验证失败: {str(ve)}'}), 400
    except Exception as e:
        app.logger.error(f'提醒保存失败: {str(e)}')
        return jsonify({'error': f'服务器内部错误: {str(e)}'}), 500

@app.route('/import_config', methods=['POST'])
@login_required
def import_config():
    global bot_process
    # 如果 bot 正在运行，则不允许导入配置
    if bot_process and bot_process.poll() is None:
        return jsonify({'error': '程序正在运行，请先停止再导入配置'}), 400

    try:
        if 'config_file' not in request.files:
            return jsonify({'error': '未找到上传的配置文件'}), 400
            
        config_file = request.files['config_file']
        if not config_file.filename.endswith('.py'):
            return jsonify({'error': '请上传.py格式的配置文件'}), 400
            
        # 创建临时文件用于解析配置
        with tempfile.NamedTemporaryFile('wb', suffix='.py', delete=False) as temp_f:
            temp_path = temp_f.name
            config_file.save(temp_path)
        
        # 解析临时配置文件
        imported_config = {}
        try:
            with open(temp_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('#') or not line:
                        continue
                    match = re.match(r'^(\w+)\s*=\s*(.+)$', line)
                    if match:
                        var_name = match.group(1)
                        var_value_str = match.group(2)
                        try:
                            var_value = ast.literal_eval(var_value_str)
                            imported_config[var_name] = var_value
                        except:
                            imported_config[var_name] = var_value_str
        finally:
            # 清理临时文件
            try:
                os.unlink(temp_path)
            except:
                pass
        
        # 获取当前配置作为基础
        current_config = parse_config()
        
        # 合并配置，并避免把旧端点的密钥带到新端点。
        current_config = _merge_imported_config(current_config, imported_config)
        
        # 更新配置文件
        update_config(current_config)
        
        return jsonify({'success': True, 'message': '配置导入成功，共导入了{}个有效参数'.format(len(imported_config))}), 200
    except Exception as e:
        app.logger.error(f"配置导入失败: {str(e)}")
        return jsonify({'error': f'导入失败: {str(e)}'}), 500

def create_backup_directory():
    """创建备份目录"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(BASE_DIR, "数据备份", f"{timestamp}_导入备份")
    os.makedirs(backup_dir, exist_ok=True)
    return backup_dir

def backup_existing_data(backup_dir):
    """备份现有数据到指定目录"""
    backed_up_items = []
    
    try:
        # 备份 prompts 文件夹
        prompts_dir = os.path.join(BASE_DIR, 'prompts')
        if os.path.exists(prompts_dir):
            backup_prompts = os.path.join(backup_dir, 'prompts')
            shutil.copytree(prompts_dir, backup_prompts)
            backed_up_items.append('prompts文件夹')
        
        # 备份 emojis 文件夹
        emojis_dir = os.path.join(BASE_DIR, 'emojis')
        if os.path.exists(emojis_dir):
            backup_emojis = os.path.join(backup_dir, 'emojis')
            shutil.copytree(emojis_dir, backup_emojis)
            backed_up_items.append('emojis文件夹')
        
        # 备份 forum_data 文件夹
        forum_data_dir = FORUM_DATA_DIR
        if os.path.exists(forum_data_dir):
            backup_forum = os.path.join(backup_dir, 'forum_data')
            shutil.copytree(forum_data_dir, backup_forum)
            backed_up_items.append('forum_data文件夹')
        
        # 备份 CoreMemory 文件夹
        config = parse_config()
        core_memory_dir = os.path.join(BASE_DIR, config.get('CORE_MEMORY_DIR', 'CoreMemory'))
        if os.path.exists(core_memory_dir):
            backup_core = os.path.join(backup_dir, os.path.basename(core_memory_dir))
            shutil.copytree(core_memory_dir, backup_core)
            backed_up_items.append('CoreMemory文件夹')
        
        # 备份 recurring_reminders.json 文件
        reminders_file = os.path.join(BASE_DIR, 'recurring_reminders.json')
        if os.path.exists(reminders_file):
            backup_reminders = os.path.join(backup_dir, 'recurring_reminders.json')
            shutil.copy2(reminders_file, backup_reminders)
            backed_up_items.append('recurring_reminders.json文件')
        
        # 备份 chat_contexts.json 文件
        chat_contexts_file = os.path.join(BASE_DIR, 'chat_contexts.json')
        if os.path.exists(chat_contexts_file):
            backup_chat_contexts = os.path.join(backup_dir, 'chat_contexts.json')
            shutil.copy2(chat_contexts_file, backup_chat_contexts)
            backed_up_items.append('chat_contexts.json文件')

        for state_filename in (
            'favorability.json', 'user_info.json', 'emotion_state.json',
            'adult_intimacy_state.json', 'chat_active_topic_state.json',
        ):
            source_state = os.path.join(BASE_DIR, state_filename)
            if os.path.exists(source_state):
                shutil.copy2(source_state, os.path.join(backup_dir, state_filename))
                backed_up_items.append(f'{state_filename}文件')
        
        # 备份 Memory_Temp 文件夹
        config = parse_config()
        memory_temp_dirname = config.get('MEMORY_TEMP_DIR', 'Memory_Temp')
        memory_temp_dir = os.path.join(BASE_DIR, memory_temp_dirname)
        if os.path.exists(memory_temp_dir):
            backup_memory_temp = os.path.join(backup_dir, memory_temp_dirname)
            shutil.copytree(memory_temp_dir, backup_memory_temp)
            backed_up_items.append('Memory_Temp文件夹')
        
        # 备份 config.py 文件
        config_file = os.path.join(BASE_DIR, 'config.py')
        if os.path.exists(config_file):
            backup_config = os.path.join(backup_dir, 'config.py')
            shutil.copy2(config_file, backup_config)
            backed_up_items.append('config.py文件')
        
        return backed_up_items
    except Exception as e:
        app.logger.error(f"备份数据失败: {str(e)}")
        raise Exception(f"备份失败: {str(e)}")

def import_directory_data(source_dir):
    """从源目录导入数据"""
    imported_items = []
    
    try:
        # 导入 config.py 文件
        source_config = os.path.join(source_dir, 'config.py')
        if os.path.exists(source_config):
            # 解析源配置文件
            imported_config = {}
            with open(source_config, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('#') or not line:
                        continue
                    match = re.match(r'^(\w+)\s*=\s*(.+)$', line)
                    if match:
                        var_name = match.group(1)
                        var_value_str = match.group(2)
                        try:
                            var_value = ast.literal_eval(var_value_str)
                            imported_config[var_name] = var_value
                        except:
                            imported_config[var_name] = var_value_str
            
            # 获取当前配置并合并，端点变化时不复用旧密钥。
            current_config = _merge_imported_config(parse_config(), imported_config)
            
            # 更新配置文件
            update_config(current_config)
            imported_items.append(f'config.py文件（导入了{len(imported_config)}个参数）')
        
        # 导入 prompts 文件夹
        source_prompts = os.path.join(source_dir, 'prompts')
        if os.path.exists(source_prompts):
            target_prompts = os.path.join(BASE_DIR, 'prompts')
            if os.path.exists(target_prompts):
                shutil.rmtree(target_prompts)
            shutil.copytree(source_prompts, target_prompts)
            imported_items.append('prompts文件夹')
        
        # 导入 emojis 文件夹
        source_emojis = os.path.join(source_dir, 'emojis')
        if os.path.exists(source_emojis):
            target_emojis = os.path.join(BASE_DIR, 'emojis')
            if os.path.exists(target_emojis):
                shutil.rmtree(target_emojis)
            shutil.copytree(source_emojis, target_emojis)
            imported_items.append('emojis文件夹')
        
        # 导入 forum_data 文件夹
        source_forum = os.path.join(source_dir, 'forum_data')
        if os.path.exists(source_forum):
            target_forum = FORUM_DATA_DIR
            if os.path.exists(target_forum):
                shutil.rmtree(target_forum)
            shutil.copytree(source_forum, target_forum)
            imported_items.append('forum_data文件夹')
        
        # 导入 CoreMemory 文件夹
        config = parse_config()
        core_memory_dirname = config.get('CORE_MEMORY_DIR', 'CoreMemory')
        source_core = os.path.join(source_dir, core_memory_dirname)
        if os.path.exists(source_core):
            target_core = os.path.join(BASE_DIR, core_memory_dirname)
            if os.path.exists(target_core):
                shutil.rmtree(target_core)
            shutil.copytree(source_core, target_core)
            imported_items.append('CoreMemory文件夹')
        
        # 导入 recurring_reminders.json 文件
        source_reminders = os.path.join(source_dir, 'recurring_reminders.json')
        if os.path.exists(source_reminders):
            target_reminders = os.path.join(BASE_DIR, 'recurring_reminders.json')
            shutil.copy2(source_reminders, target_reminders)
            imported_items.append('recurring_reminders.json文件')
        
        # 导入 chat_contexts.json 文件
        source_chat_contexts = os.path.join(source_dir, 'chat_contexts.json')
        if os.path.exists(source_chat_contexts):
            target_chat_contexts = os.path.join(BASE_DIR, 'chat_contexts.json')
            shutil.copy2(source_chat_contexts, target_chat_contexts)
            imported_items.append('chat_contexts.json文件')

        for state_filename in (
            'favorability.json', 'user_info.json', 'emotion_state.json',
            'adult_intimacy_state.json', 'chat_active_topic_state.json',
        ):
            source_state = os.path.join(source_dir, state_filename)
            if os.path.exists(source_state):
                shutil.copy2(source_state, os.path.join(BASE_DIR, state_filename))
                imported_items.append(f'{state_filename}文件')
        
        # 导入 Memory_Temp 文件夹
        config = parse_config()
        memory_temp_dirname = config.get('MEMORY_TEMP_DIR', 'Memory_Temp')
        source_memory_temp = os.path.join(source_dir, memory_temp_dirname)
        if os.path.exists(source_memory_temp):
            target_memory_temp = os.path.join(BASE_DIR, memory_temp_dirname)
            if os.path.exists(target_memory_temp):
                shutil.rmtree(target_memory_temp)
            shutil.copytree(source_memory_temp, target_memory_temp)
            imported_items.append('Memory_Temp文件夹')
        
        return imported_items
    except Exception as e:
        app.logger.error(f"导入目录数据失败: {str(e)}")
        raise Exception(f"导入失败: {str(e)}")

def import_files_data(files_dict):
    """从上传的文件字典导入数据"""
    imported_items = []
    
    try:
        # 创建临时目录来重建文件结构
        temp_dir = tempfile.mkdtemp()
        
        try:
            # 重建文件结构
            for relative_path, file_data in files_dict.items():
                # 标准化路径分隔符
                relative_path = relative_path.replace('\\', '/')
                
                # 处理路径，去除顶级目录（如果有的话）
                path_parts = relative_path.split('/')
                if len(path_parts) > 1:
                    # 如果路径有多级，可能需要去除第一级目录
                    relative_path = '/'.join(path_parts[1:]) if path_parts[0] and path_parts[0] != '.' else relative_path
                
                # 跳过空路径
                if not relative_path:
                    continue
                
                # 创建完整的文件路径
                full_path = os.path.join(temp_dir, relative_path.replace('/', os.sep))
                
                # 确保目录存在
                dir_path = os.path.dirname(full_path)
                if dir_path and dir_path != temp_dir:
                    os.makedirs(dir_path, exist_ok=True)
                
                # 保存文件
                file_data.save(full_path)
                
                app.logger.debug(f"保存文件: {relative_path} -> {full_path}")
            
            # 使用现有的导入函数
            imported_items = import_directory_data(temp_dir)
            
            return imported_items
            
        finally:
            # 清理临时目录
            try:
                shutil.rmtree(temp_dir)
            except:
                pass
                
    except Exception as e:
        app.logger.error(f"导入文件数据失败: {str(e)}")
        raise Exception(f"导入失败: {str(e)}")

@app.route('/import_full_directory', methods=['POST'])
@login_required
def import_full_directory():
    """导入完整的旧版本程序目录"""
    global bot_process
    
    # 如果 bot 正在运行，则不允许导入
    if bot_process and bot_process.poll() is None:
        return jsonify({'error': '程序正在运行，请先停止再导入数据'}), 400

    try:
        # 检查是否上传了文件
        if 'directory_files' not in request.files:
            return jsonify({'error': '未找到上传的目录文件'}), 400
            
        uploaded_files = request.files.getlist('directory_files')
        if not uploaded_files:
            return jsonify({'error': '未找到任何文件'}), 400
        
        # 检查是否包含config.py文件
        config_found = False
        files_dict = {}
        
        for file in uploaded_files:
            if file.filename:
                # 获取相对路径（webkitRelativePath）
                relative_path = file.filename
                files_dict[relative_path] = file
                
                # 检查是否有config.py
                if file.filename.endswith('config.py') or file.filename == 'config.py':
                    config_found = True
        
        if not config_found:
            return jsonify({'error': '选择的目录中没有找到config.py文件'}), 400
        
        # 创建备份目录
        backup_dir = create_backup_directory()
        
        # 备份现有数据
        backed_up_items = backup_existing_data(backup_dir)
        
        # 导入新数据
        imported_items = import_files_data(files_dict)
        
        # 构建结果消息
        message = f"完整目录导入成功！\n"
        message += f"共处理了 {len(uploaded_files)} 个文件\n"
        if backed_up_items:
            message += f"已备份的数据: {', '.join(backed_up_items)}\n"
        if imported_items:
            message += f"已导入的数据: {', '.join(imported_items)}\n"
        message += f"备份位置: {backup_dir}"
        
        return jsonify({
            'success': True, 
            'message': message,
            'backed_up_items': backed_up_items,
            'imported_items': imported_items,
            'backup_location': backup_dir,
            'files_count': len(uploaded_files)
        }), 200
                
    except Exception as e:
        app.logger.error(f"完整目录导入失败: {str(e)}")
        return jsonify({'error': f'导入失败: {str(e)}'}), 500

@app.route('/reset_default_config', methods=['POST'])
@login_required
def reset_default_config():
    global bot_process
    if bot_process and bot_process.poll() is None:
        return jsonify({'error': '程序正在运行，请先停止再恢复默认配置'}), 400
    
    try:
        # 获取默认配置
        default_config = get_default_config()
        
        # 保留当前的端口号和登录密码设置（避免被锁在外）
        current_config = parse_config()
        if 'PORT' in current_config:
            default_config['PORT'] = current_config['PORT']
        if 'LOGIN_PASSWORD' in current_config:
            default_config['LOGIN_PASSWORD'] = current_config['LOGIN_PASSWORD']
        if 'ENABLE_LOGIN_PASSWORD' in current_config:
            default_config['ENABLE_LOGIN_PASSWORD'] = current_config['ENABLE_LOGIN_PASSWORD']
        
        # 使用 update_config 函数来保留原有的注释和格式
        update_config(default_config)
        
        app.logger.info("配置已恢复到默认值")
        return jsonify({'message': '配置已恢复到默认值'}), 200
        
    except Exception as e:
        app.logger.error(f"恢复默认配置失败: {e}")
        return jsonify({'error': f'恢复默认配置失败: {str(e)}'}), 500

class WebLogHandler(logging.Handler):
    def emit(self, record):
        log_entry = self.format(record)
        log_queue.put(log_entry)

# 配置日志处理器
web_handler = WebLogHandler()
web_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logging.getLogger().addHandler(web_handler)

@app.route('/stream')
@login_required
def stream():
    def event_stream():
        retry_count = 0
        while True:
            try:
                log = log_queue.get(timeout=5)
                yield f"data: {log}\n\n"
                retry_count = 0  # 成功时重置重试计数器
            except Empty:
                yield ":keep-alive\n\n"  # 发送心跳包
                retry_count = min(retry_count + 1, 5)
                time.sleep(2 ** retry_count)  # 指数退避
            except Exception as e:
                app.logger.error(f"SSE Error: {str(e)}")
                yield "event: error\ndata: Connection closed\n\n"
                break
    
    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )

@app.route('/api/log', methods=['POST'])
def receive_bot_log():
    try:
        # 增加Content-Type检查
        if not request.is_json:
            return jsonify({'error': 'Unsupported Media Type'}), 415

        # 支持两种格式：单个日志或日志数组
        if 'logs' in request.json:  # 批量日志
            logs_data = request.json.get('logs', [])
            if isinstance(logs_data, list):
                for log_entry in logs_data:
                    if log_entry:
                        # 添加进程标识和颜色标记
                        colored_log = f"[BOT] \033[34m{log_entry.strip()}\033[0m"
                        log_queue.put(colored_log)
                return jsonify({'status': 'success', 'processed': len(logs_data)})
            return jsonify({'error': 'Invalid logs format'}), 400
            
        elif 'log' in request.json:  # 兼容单条日志格式
            log_data = request.json.get('log')
            if log_data:
                # 添加进程标识和颜色标记
                colored_log = f"[BOT] \033[34m{log_data.strip()}\033[0m"
                log_queue.put(colored_log)
            return jsonify({'status': 'success'})
            
        else:
            return jsonify({'error': 'Missing log data'}), 400
            
    except Exception as e:
        app.logger.error(f"日志接收失败: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/get_chat_context_users', methods=['GET'])
@login_required
def api_get_chat_context_users():
    users = get_chat_context_users()
    return jsonify({'users': users})

@app.route('/api/favorability', methods=['GET'])
@login_required
def api_get_favorability():
    return jsonify({'records': get_favorability_records()})

@app.route('/api/favorability/update', methods=['POST'])
@login_required
def api_update_favorability():
    try:
        payload = request.get_json(silent=True) or {}
        username = str(payload.get('username', '')).strip()
        score = normalize_favorability_score(
            payload.get('score', DEFAULT_FAVORABILITY_SCORE),
            DEFAULT_FAVORABILITY_SCORE,
        )
        if not username:
            return jsonify({'status': 'error', 'message': '用户名不能为空'}), 400

        reset_adult_intimacy_state = payload.get('reset_adult_intimacy_state', False) is True
        with favorability_file_lock:
            data = load_favorability_file(raise_on_error=True)
            record = data.get(username, {})
            if not isinstance(record, dict):
                record = {}
            old_score = normalize_favorability_score(
                record.get('score', DEFAULT_FAVORABILITY_SCORE),
                DEFAULT_FAVORABILITY_SCORE,
            )
            record.update({
                'score': score,
                'stage': get_favorability_stage(score),
                'message_count': int(record.get('message_count', 0)),
                'voice_video_request_streak': int(record.get('voice_video_request_streak', 0)),
                'last_interaction_date': record.get('last_interaction_date', ''),
                'last_change': float(round((score - old_score) * 2) / 2),
                'last_reason': '后台手动调整好感度',
                'last_updated': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
            data[username] = record
            save_favorability_file(data)
        adult_state = sync_adult_intimacy_state_for_manual_favorability(
            username, score, reset=reset_adult_intimacy_state,
        )
        return jsonify({'status': 'success', 'record': record, 'adult_intimacy_state': adult_state})
    except Exception as e:
        app.logger.error(f"更新好感度失败: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/user_info', methods=['GET'])
@login_required
def api_get_user_info():
    return jsonify({'records': get_user_info_records()})

@app.route('/api/user_info/update', methods=['POST'])
@login_required
def api_update_user_info():
    try:
        payload = request.get_json(silent=True) or {}
        username = str(payload.get('username', '')).strip()
        if not username:
            return jsonify({'status': 'error', 'message': '用户名不能为空'}), 400

        if "is_minor" in payload:
            raw_minor = payload.get("is_minor")
            if not isinstance(raw_minor, bool):
                return jsonify({'status': 'error', 'message': 'is_minor 必须是布尔值'}), 400
        if "adult_mode_enabled" in payload:
            raw_adult_mode = payload.get("adult_mode_enabled")
            if not isinstance(raw_adult_mode, bool):
                return jsonify({'status': 'error', 'message': 'adult_mode_enabled 必须是布尔值'}), 400

        with user_info_thread_lock, user_info_file_lock:
            data = load_user_info_file(raise_on_error=True)
            record = normalize_user_info_record(data.get(username, {}))
            for key in ("name", "age", "gender", "location", "job_or_school"):
                record[key] = str(payload.get(key, record.get(key, ""))).strip()
            if "is_minor" in payload:
                record["is_minor"] = payload["is_minor"]
            if (
                record["is_minor"]
                and payload.get("adult_mode_enabled") is True
            ):
                return jsonify({
                    'status': 'error',
                    'message': '未成年人不能开启成人模式',
                }), 400
            if "adult_mode_enabled" in payload:
                record["adult_mode_enabled"] = payload["adult_mode_enabled"]
            if record["is_minor"]:
                record["adult_mode_enabled"] = False
            for key in ("likes", "dislikes", "important_info", "asked_fields"):
                value = payload.get(key, record.get(key, []))
                if isinstance(value, str):
                    items = [item.strip() for item in re.split(r'[,，\n]+', value) if item.strip()]
                elif isinstance(value, list):
                    items = [str(item).strip() for item in value if str(item).strip()]
                else:
                    items = []
                record[key] = items
            record["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            data[username] = record
            save_user_info_file(data)
        record["username"] = username
        return jsonify({'status': 'success', 'record': record})
    except Exception as e:
        app.logger.error(f"保存用户信息失败: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/user_info/delete/<username>', methods=['DELETE'])
@login_required
def api_delete_user_info(username):
    try:
        with user_info_thread_lock, user_info_file_lock:
            data = load_user_info_file(raise_on_error=True)
            if username in data:
                data.pop(username)
                save_user_info_file(data)
        return jsonify({'status': 'success'})
    except Exception as e:
        app.logger.error(f"删除用户信息失败: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/chat_active_topics', methods=['GET'])
@login_required
def api_get_chat_active_topics():
    return jsonify({'topics': load_chat_active_topics_file()})

@app.route('/api/chat_active_topics', methods=['POST'])
@login_required
def api_save_chat_active_topics():
    try:
        payload = request.get_json(silent=True) or {}
        topics = payload.get('topics', [])
        if not isinstance(topics, list):
            return jsonify({'status': 'error', 'message': 'topics 必须是数组'}), 400
        save_chat_active_topics_file(topics)
        return jsonify({'status': 'success', 'topics': load_chat_active_topics_file()})
    except Exception as e:
        app.logger.error(f"保存聊天主动话题失败: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/chat_active_topics/add', methods=['POST'])
@login_required
def api_add_chat_active_topic():
    try:
        topics = load_chat_active_topics_file()
        topics.append(normalize_chat_active_topic({
            "id": f"topic_{int(time.time() * 1000)}",
            "title": "新话题",
            "content": "写一句这个话题要怎么自然聊起来",
            "enabled": True
        }, len(topics)))
        save_chat_active_topics_file(topics)
        return jsonify({'status': 'success', 'topics': load_chat_active_topics_file()})
    except Exception as e:
        app.logger.error(f"新增聊天主动话题失败: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/chat_active_topics/delete/<topic_id>', methods=['DELETE'])
@login_required
def api_delete_chat_active_topic(topic_id):
    try:
        topic_id = str(topic_id).strip()
        topics = [topic for topic in load_chat_active_topics_file() if topic.get("id") != topic_id]
        save_chat_active_topics_file(topics)
        return jsonify({'status': 'success', 'topics': load_chat_active_topics_file()})
    except Exception as e:
        app.logger.error(f"删除聊天主动话题失败: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/character_story', methods=['GET'])
@login_required
def api_get_character_story():
    return jsonify({'content': read_character_story()})

@app.route('/api/character_story', methods=['POST'])
@login_required
def api_save_character_story():
    try:
        content = ""
        if 'story_file' in request.files:
            uploaded_file = request.files['story_file']
            raw = uploaded_file.read()
            content = raw.decode('utf-8', errors='ignore')
        else:
            payload = request.get_json(silent=True) or {}
            content = str(payload.get('content', ''))
        write_character_story(content.strip())
        return jsonify({'status': 'success', 'length': len(content.strip())})
    except Exception as e:
        app.logger.error(f"保存人物故事背景失败: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/clear_chat_context/<username>', methods=['POST'])
@login_required
def clear_chat_context(username):
    """清除指定用户的聊天上下文"""
    if not os.path.exists(CHAT_CONTEXTS_FILE):
        return jsonify({'status': 'error', 'message': '聊天上下文文件不存在'}), 404

    with FileLock(CHAT_CONTEXTS_LOCK_FILE):
        try:
            with open(CHAT_CONTEXTS_FILE, 'r+', encoding='utf-8') as f:
                data = json.load(f)
                if username in data:
                    del data[username]
                    f.seek(0) # 回到文件开头
                    json.dump(data, f, ensure_ascii=False, indent=4)
                    f.truncate() # 删除剩余内容
                    return jsonify({'status': 'success', 'message': f"用户 '{username}' 的聊天上下文已清除"})
                else:
                    return jsonify({'status': 'error', 'message': f"用户 '{username}' 未找到"}), 404
        except (json.JSONDecodeError, IOError) as e:
            app.logger.error(f"处理 chat_contexts.json 失败: {e}")
            return jsonify({'status': 'error', 'message': '处理聊天上下文文件失败'}), 500

# 聊天上下文编辑API
@app.route('/api/get_chat_context/<username>', methods=['GET'])
@login_required
def get_user_chat_context(username):
    """获取指定用户的聊天上下文"""
    if not os.path.exists(CHAT_CONTEXTS_FILE):
        return jsonify({'error': '聊天上下文文件未找到'}), 404

    with FileLock(CHAT_CONTEXTS_LOCK_FILE):
        try:
            with open(CHAT_CONTEXTS_FILE, 'r', encoding='utf-8') as f:
                contexts = json.load(f)
            user_context = contexts.get(username)
            if user_context is None:
                return jsonify({'error': f"用户 '{username}' 在上下文中不存在"}), 404
            pretty_context = json.dumps(user_context, ensure_ascii=False, indent=4)
            return jsonify({'status': 'success', 'context': pretty_context})
        except (json.JSONDecodeError, IOError) as e:
            app.logger.error(f"读取或解析聊天上下文文件失败: {e}")
            return jsonify({'error': f'读取或解析文件失败: {e}'}), 500

@app.route('/api/save_chat_context/<username>', methods=['POST'])
@login_required
def save_user_chat_context(username):
    """保存指定用户修改后的聊天上下文，强制合并连续user消息，确保user→assistant结构"""
    if bot_process and bot_process.poll() is not None:
        return jsonify({'error': '程序正在运行，请先停止再保存上下文'}), 400
    data = request.get_json()
    if not data or 'context' not in data:
        return jsonify({'status': 'error', 'message': '请求无效，缺少 "context" 字段'}), 400
    new_context_str = data['context']
    try:
        new_context_data = json.loads(new_context_str)
        if not isinstance(new_context_data, list):
            raise ValueError("上下文数据必须是一个JSON数组 (list)")
        # --- 强制合并连续user，保证user→assistant结构 ---
        merged_context = []
        user_buffer = []
        for item in new_context_data:
            if item.get('role') == 'user':
                user_buffer.append(item.get('content', ''))
            elif item.get('role') == 'assistant':
                if user_buffer:
                    merged_context.append({'role': 'user', 'content': '\n'.join(user_buffer)})
                    user_buffer = []
                merged_context.append(item)
        if user_buffer:
            merged_context.append({'role': 'user', 'content': '\n'.join(user_buffer)})
        # --- END ---
    except (json.JSONDecodeError, ValueError) as e:
        return jsonify({'status': 'error', 'message': f'格式错误: {str(e)}'}), 400
    with FileLock(CHAT_CONTEXTS_LOCK_FILE):
        try:
            if not os.path.exists(CHAT_CONTEXTS_FILE):
                return jsonify({'status': 'error', 'message': '聊天上下文文件未找到'}), 404
            with open(CHAT_CONTEXTS_FILE, 'r', encoding='utf-8') as f:
                all_contexts = json.load(f)
            if username not in all_contexts:
                return jsonify({'status': 'error', 'message': '用户在上下文中不存在'}), 404
            all_contexts[username] = merged_context
            temp_file_path = CHAT_CONTEXTS_FILE + ".tmp"
            with open(temp_file_path, 'w', encoding='utf-8') as f:
                json.dump(all_contexts, f, ensure_ascii=False, indent=4)
            shutil.move(temp_file_path, CHAT_CONTEXTS_FILE)
        except Exception as e:
            app.logger.error(f"保存聊天上下文失败: {e}")
            return jsonify({'status': 'error', 'message': f'保存失败: {str(e)}'}), 500
    return jsonify({'status': 'success', 'message': f"用户 '{username}' 的上下文已更新"})

@app.route('/api/npc/save_settings', methods=['POST'])
@login_required
def save_npc_settings():
    """保存NPC配置"""
    try:
        data = request.json
        if not data:
            return jsonify({'error': '无效的数据'}), 400
        
        app.logger.info(f"接收到NPC配置数据: {data}")
        
        # 保存NPC配置到文件
        npc_config_file = _npc_config_file_path()
        
        # 读取现有配置
        existing_config = {}
        if os.path.exists(npc_config_file):
            try:
                with open(npc_config_file, 'r', encoding='utf-8') as f:
                    existing_config = json.load(f)
                app.logger.info(f"读取现有配置: {existing_config}")
            except Exception as read_error:
                app.logger.warning(f"读取现有配置失败，使用空配置: {read_error}")
                existing_config = {}
        
        # 更新配置 - 使用更精确的更新逻辑
        app.logger.info(f"接收到的数据: {data}")
        
        # 更新选中的NPC列表
        if 'selected_npcs' in data:
            existing_config['selected_npcs'] = data['selected_npcs']
            app.logger.info(f"更新选中的NPC列表: {data['selected_npcs']}")
        
        # 更新NPC设置
        if 'npc_settings' in data:
            if 'npc_settings' not in existing_config:
                existing_config['npc_settings'] = {}
            
            for npc_name, npc_data in data['npc_settings'].items():
                # 清理不需要的字段（移除 model_type）
                try:
                    if isinstance(npc_data, dict) and 'model_type' in npc_data:
                        npc_data.pop('model_type', None)
                except Exception:
                    pass
                
                existing_config['npc_settings'][npc_name] = npc_data
                app.logger.info(f"更新NPC {npc_name} 的设置: {npc_data}")

        
        app.logger.info(f"合并后的配置: {existing_config}")
        
        # 验证配置格式
        if 'selected_npcs' in existing_config and 'npc_settings' in existing_config:
            # 确保所有选中的NPC都有对应的设置
            for npc_name in existing_config['selected_npcs']:
                if npc_name not in existing_config['npc_settings']:
                    app.logger.warning(f"NPC {npc_name} 被选中但没有设置，将添加默认设置")
                    existing_config['npc_settings'][npc_name] = {
                        'language_style': 'casual',
                        'relationship': 'friend',
                        'example_output': '',
                        'other_settings': ''
                    }
                else:
                    app.logger.info(f"NPC {npc_name} 设置已更新")
        
        # 保存配置
        with open(npc_config_file, 'w', encoding='utf-8') as f:
            json.dump(existing_config, f, ensure_ascii=False, indent=2)
        
        app.logger.info(f"成功保存NPC配置到文件: {npc_config_file}")
        
        # 验证保存是否成功
        try:
            with open(npc_config_file, 'r', encoding='utf-8') as f:
                saved_config = json.load(f)
            app.logger.info(f"验证保存的配置: {saved_config}")
        except Exception as verify_error:
            app.logger.error(f"验证保存配置失败: {verify_error}")
            return jsonify({'error': '配置保存后验证失败'}), 500
        
        return jsonify({
            'success': True, 
            'message': 'NPC配置保存成功',
            'saved_config': saved_config
        })
        
    except Exception as e:
        app.logger.error(f"保存NPC配置失败: {e}")
        return jsonify({'error': f'保存失败: {str(e)}'}), 500

@app.route('/api/npc/get_settings', methods=['GET'])
@login_required
def get_npc_settings():
    """获取NPC配置"""
    try:
        npc_config_file = _npc_config_file_path()
        
        if os.path.exists(npc_config_file):
            try:
                with open(npc_config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                app.logger.info(f"成功读取NPC配置: {config}")
                
                # 确保配置格式完整
                if 'selected_npcs' not in config:
                    config['selected_npcs'] = []
                if 'npc_settings' not in config:
                    config['npc_settings'] = {}
                
                # 为每个选中的NPC确保有默认设置
                for npc_name in config.get('selected_npcs', []):
                    if npc_name not in config.get('npc_settings', {}):
                        config['npc_settings'][npc_name] = {
                            'language_style': 'casual',
                            'relationship': 'friend',
                            'example_output': '',
                            'other_settings': ''
                        }
                        app.logger.info(f"为NPC {npc_name} 添加默认设置")
                    else:
                        # 清理不需要的字段（移除 model_type）
                        if isinstance(config['npc_settings'][npc_name], dict):
                            config['npc_settings'][npc_name].pop('model_type', None)
                        app.logger.info(f"NPC {npc_name} 已有设置: {config['npc_settings'][npc_name]}")
                
                return jsonify(config)
            except Exception as read_error:
                app.logger.error(f"读取NPC配置文件失败: {read_error}")
                return jsonify({'error': f'读取配置失败: {str(read_error)}'}), 500
        else:
            #app.logger.info("NPC配置文件不存在，返回空配置")
            return jsonify({
                'selected_npcs': [],
                'npc_settings': {}
            })
            
    except Exception as e:
        app.logger.error(f"获取NPC配置失败: {e}")
        return jsonify({'error': f'获取失败: {str(e)}'}), 500

# 核心记忆管理API
@app.route('/api/get_core_memory_files', methods=['GET'])
@login_required
def get_core_memory_files():
    """获取核心记忆文件列表"""
    try:
        # 从配置中获取核心记忆目录
        config = parse_config()
        core_memory_dir = os.path.join(os.path.dirname(__file__), config.get('CORE_MEMORY_DIR', 'CoreMemory'))
        
        # 确保目录存在
        os.makedirs(core_memory_dir, exist_ok=True)
        
        files = []
        if os.path.exists(core_memory_dir):
            for filename in os.listdir(core_memory_dir):
                if filename.endswith('_core_memory.json'):
                    file_path = os.path.join(core_memory_dir, filename)
                    try:
                        # 读取文件获取记忆片段数量
                        with open(file_path, 'r', encoding='utf-8') as f:
                            memories = json.load(f)
                        
                        memory_count = len(memories) if isinstance(memories, list) else 0
                        
                        # 获取文件修改时间
                        mtime = os.path.getmtime(file_path)
                        last_modified = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')
                        
                        # 从文件名提取显示名称
                        display_name = filename.replace('_core_memory.json', '').replace('_', ' - ')
                        
                        files.append({
                            'filename': filename,
                            'display_name': display_name,
                            'memory_count': memory_count,
                            'last_modified': last_modified
                        })
                    except Exception as e:
                        app.logger.warning(f"读取核心记忆文件 {filename} 失败: {e}")
                        continue
        
        # 按修改时间倒序排列
        files.sort(key=lambda x: x['last_modified'], reverse=True)
        
        return jsonify({'status': 'success', 'files': files})
        
    except Exception as e:
        app.logger.error(f"获取核心记忆文件列表失败: {e}")
        return jsonify({'status': 'error', 'message': f'获取失败: {str(e)}'}), 500

@app.route('/api/get_core_memory/<filename>', methods=['GET'])
@login_required  
def get_core_memory(filename):
    """获取指定核心记忆文件的内容"""
    try:
        # 验证文件名安全性
        if not filename.endswith('_core_memory.json'):
            return jsonify({'status': 'error', 'error': '无效的文件名'}), 400
            
        config = parse_config()
        core_memory_dir = os.path.join(os.path.dirname(__file__), config.get('CORE_MEMORY_DIR', 'CoreMemory'))
        file_path = os.path.join(core_memory_dir, filename)
        
        if not os.path.exists(file_path):
            return jsonify({'status': 'error', 'error': '文件不存在'}), 404
        
        # 读取记忆文件
        with open(file_path, 'r', encoding='utf-8') as f:
            memories = json.load(f)
        
        # 确保返回的是列表格式
        if not isinstance(memories, list):
            memories = []
        
        # 字段名兼容性处理：处理旧版本的time字段，统一使用timestamp字段
        for memory in memories:
            if isinstance(memory, dict):
                # 如果有旧版本的time字段但没有timestamp字段，则将time转换为timestamp
                if 'time' in memory and 'timestamp' not in memory:
                    memory['timestamp'] = memory['time']
                    del memory['time']  # 删除旧字段，避免冗余
            
        return jsonify({'status': 'success', 'memories': memories})
        
    except Exception as e:
        app.logger.error(f"获取核心记忆文件 {filename} 失败: {e}")
        return jsonify({'status': 'error', 'error': f'读取失败: {str(e)}'}), 500

@app.route('/api/save_core_memory/<filename>', methods=['POST'])
@login_required
def save_core_memory(filename):
    """保存核心记忆到指定文件"""
    try:
        # 验证文件名安全性
        if not filename.endswith('_core_memory.json'):
            return jsonify({'status': 'error', 'message': '无效的文件名'}), 400
            
        data = request.json
        memories = data.get('memories', [])
        
        # 验证数据格式
        if not isinstance(memories, list):
            return jsonify({'status': 'error', 'message': '记忆数据必须是数组格式'}), 400
        
        # 验证每个记忆片段的格式
        for i, memory in enumerate(memories):
            if not isinstance(memory, dict):
                return jsonify({'status': 'error', 'message': f'第{i+1}个记忆片段格式错误'}), 400
            
            # 字段名兼容性检查：支持time字段，但统一转换为timestamp
            if 'time' in memory and 'timestamp' not in memory:
                memory['timestamp'] = memory['time']
                del memory['time']  # 删除旧字段，避免冗余
            
            # 验证必要字段
            if 'timestamp' not in memory or 'importance' not in memory or 'summary' not in memory:
                return jsonify({'status': 'error', 'message': f'第{i+1}个记忆片段缺少必要字段(timestamp、importance、summary)'}), 400
                
            if not isinstance(memory['importance'], int) or not (1 <= memory['importance'] <= 10):
                return jsonify({'status': 'error', 'message': f'第{i+1}个记忆片段的重要度必须是1-10的整数'}), 400
        
        config = parse_config()
        core_memory_dir = os.path.join(os.path.dirname(__file__), config.get('CORE_MEMORY_DIR', 'CoreMemory'))
        os.makedirs(core_memory_dir, exist_ok=True)
        
        file_path = os.path.join(core_memory_dir, filename)
        
        # 保存记忆文件
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(memories, f, ensure_ascii=False, indent=2)
        
        app.logger.info(f"已保存核心记忆文件: {filename}")
        return jsonify({'status': 'success', 'message': '核心记忆已保存'})
        
    except Exception as e:
        app.logger.error(f"保存核心记忆文件 {filename} 失败: {e}")
        return jsonify({'status': 'error', 'message': f'保存失败: {str(e)}'}), 500

@app.route('/api/delete_core_memory/<filename>', methods=['DELETE'])
@login_required
def delete_core_memory(filename):
    """删除核心记忆文件"""
    try:
        # 验证文件名安全性
        if not filename.endswith('_core_memory.json'):
            return jsonify({'status': 'error', 'message': '无效的文件名'}), 400
            
        config = parse_config()
        core_memory_dir = os.path.join(os.path.dirname(__file__), config.get('CORE_MEMORY_DIR', 'CoreMemory'))
        file_path = os.path.join(core_memory_dir, filename)
        
        if not os.path.exists(file_path):
            return jsonify({'status': 'error', 'message': '文件不存在'}), 404
        
        # 删除文件
        os.remove(file_path)
        
        app.logger.info(f"已删除核心记忆文件: {filename}")
        return jsonify({'status': 'success', 'message': '核心记忆文件已删除'})
        
    except Exception as e:
        app.logger.error(f"删除核心记忆文件 {filename} 失败: {e}")
        return jsonify({'status': 'error', 'message': f'删除失败: {str(e)}'}), 500

def run_bat_file():
    bat_file_path = "一键检测.bat"
    if os.path.exists(bat_file_path):
        os.system(f"start {bat_file_path}")

from multiprocessing import Process
import random
from datetime import datetime, timedelta
@app.route('/forum/<character_name>')
@login_required
def character_forum(character_name):
    """AI角色论坛页面"""
    try:
        config = parse_config()
        
        # 验证角色名是否存在于配置中
        listen_list = config.get('LISTEN_LIST', [])
        character_found = False
        for item in listen_list:
            if len(item) >= 2 and item[1] == character_name:
                character_found = True
                break
        
        if not character_found:
            return "角色不存在", 404
        
        return render_template('character_forum.html', character_name=character_name)
    
    except Exception as e:
        app.logger.error(f"加载论坛页面失败: {e}")
        return "加载论坛页面失败", 500

@app.route('/api/forum/refresh/<character_name>', methods=['POST'])
@login_required
def refresh_forum(character_name):
    """刷新论坛内容API"""
    try:
        config = parse_config()
        
        # 验证角色名
        listen_list = config.get('LISTEN_LIST', [])
        character_found = False
        for item in listen_list:
            if len(item) >= 2 and item[1] == character_name:
                character_found = True
                break
        
        if not character_found:
            return jsonify({'error': '角色不存在'}), 404
        
        # 调用AI判断是否发论坛
        app.logger.info(f"开始为角色 {character_name} 刷新论坛")
        should_post, content = check_should_post_forum(character_name)
        app.logger.info(f"AI判断结果: should_post={should_post}, content='{content}'")
        
        if should_post:
            # 添加新论坛内容
            forum_post = add_forum_post(character_name, content)
            app.logger.info(f"成功添加论坛帖子: {forum_post['id'] if forum_post else 'None'}")
            return jsonify({
                'has_new_content': True,
                'post': forum_post
            })
        else:
            return jsonify({
                'has_new_content': False,
                'message': '暂时没有新的论坛动态'
            })
    
    except Exception as e:
        app.logger.error(f"刷新论坛失败: {e}")
        return jsonify({'error': f'刷新失败: {str(e)}'}), 500

@app.route('/api/forum/posts/<character_name>')
@login_required
def get_forum_posts(character_name):
    """获取论坛历史帖子"""
    try:
        config = parse_config()
        
        posts = get_character_forum_posts(character_name)
        return jsonify({'posts': posts})
    
    except Exception as e:
        app.logger.error(f"获取论坛帖子失败: {e}")
        return jsonify({'error': f'获取失败: {str(e)}'}), 500

@app.route('/api/forum/delete/<character_name>/<post_id>', methods=['DELETE'])
@login_required
def delete_forum_post(character_name, post_id):
    """删除角色论坛帖子API"""
    try:
        app.logger.info(f"开始删除角色 {character_name} 的帖子 {post_id}")
        
        # 验证角色名
        config = parse_config()
        listen_list = config.get('LISTEN_LIST', [])
        character_found = False
        for item in listen_list:
            if len(item) >= 2 and item[1] == character_name:
                character_found = True
                break
        
        if not character_found:
            return jsonify({'error': '角色不存在'}), 404
        
        # 删除帖子
        success = delete_forum_post_by_id(character_name, post_id)
        
        if success:
            app.logger.info(f"成功删除帖子 {post_id}")
            return jsonify({'message': '删除成功'})
        else:
            app.logger.error(f"删除帖子 {post_id} 失败")
            return jsonify({'error': '帖子不存在或删除失败'}), 404
    
    except Exception as e:
        app.logger.error(f"删除论坛帖子失败: {e}")
        return jsonify({'error': f'删除失败: {str(e)}'}), 500

@app.route('/test_forum_ai/<character_name>')
@login_required
def test_forum_ai(character_name):
    """测试AI论坛判断逻辑"""
    try:
        config = parse_config()

        
        app.logger.info(f"测试AI判断逻辑，角色: {character_name}")
        should_post, content = check_should_post_forum(character_name)
        
        result = {
            "character": character_name,
            "should_post": should_post,
            "content": content,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        return f"""
        <h2>AI判断测试结果</h2>
        <p><strong>角色:</strong> {character_name}</p>
        <p><strong>判断结果:</strong> {'发布' if should_post else '不发布'}</p>
        <p><strong>生成内容:</strong> {content}</p>
        <p><strong>测试时间:</strong> {result['timestamp']}</p>
        <hr>
        <p><a href="javascript:history.back()">返回</a> | <a href="/forum/{character_name}" target="_blank">查看论坛</a></p>
        """
        
    except Exception as e:
        app.logger.error(f"测试AI判断失败: {e}")
        return f"测试失败: {str(e)}", 500

@app.route('/run_one_key_detection', methods=['GET'])
def run_one_key_detection():
    bat_file_path = "一键检测.bat"
    if os.path.exists(bat_file_path):
        # 使用独立的进程运行 .bat 文件
        p = Process(target=run_bat_file)
        p.start()
        return """
        <h2>启动成功！</h2>
        <p>一键检测工具已成功启动！</p>
        <ul>
            <li>检测工具将在单独的端口上运行，并自动打开浏览器窗口显示检测结果。</li>
            <li>启动后，检测工具将在3分钟后自动关闭进程。</li>
        </ul>
        <h3>检测功能：</h3>
        <ul>
            <li>微信环境检测：检查微信版本、登录状态和窗口状态。</li>
            <li>API配置检测：验证API密钥和连接状态。</li>
            <li>系统资源检测：分析CPU、内存使用情况。</li>
            <li>生成详细的诊断报告：提供问题解决建议。</li>
        </ul>
        <p style="color: green; font-weight: bold;">提示：本页面可以安全关闭，检测工具将在后台运行。</p>
        """
    return """
    <h2>启动失败</h2>
    <p style="color: red;">未找到一键检测.bat，请检查路径是否正确。</p>
    <p>请确保<b>一键检测.bat</b>文件位于程序当前运行目录下。</p>
    """

def check_should_post_forum(character_name):
    """调用AI判断是否发论坛内容"""
    try:
        app.logger.info(f"检查角色 {character_name} 是否应该发布论坛内容")
        config = parse_config()
        
        # 读取角色设定
        character_prompt = load_character_prompt(character_name)
        if not character_prompt:
            app.logger.warning(f"无法加载角色 {character_name} 的设定文件")
            return False, ""
        
        app.logger.info(f"成功加载角色设定，长度: {len(character_prompt)} 字符")
        
        # 构建AI提示
        current_time = datetime.now()
        time_str = current_time.strftime("%Y-%m-%d %H:%M")
        
        # 获取最近的论坛历史（用于避免重复）
        recent_posts = get_character_forum_posts(character_name, limit=5)
        recent_content = ""
        if recent_posts:
            recent_content = "\n最近发过的内容：\n" + "\n".join([f"- {post['content']}" for post in recent_posts[:3]])
        
        # --- 新增：50%概率的联网热点检索 ---
        import random
        online_hot_brief = ''
        should_use_online = random.random() < 0.5  # 50%概率
        
        if should_use_online and bool(config.get('ENABLE_ONLINE_API', False)):
            online_api_key = (config.get('ONLINE_API_KEY') or '').strip()
            online_base_url = config.get('ONLINE_BASE_URL', '')
            online_model = (config.get('ONLINE_MODEL') or '').strip()
            online_temperature = float(config.get('ONLINE_API_TEMPERATURE', 0.7))
            online_max_tokens = int(config.get('ONLINE_API_MAX_TOKEN', 2000))

            if online_api_key and online_base_url and online_model:
                try:
                    app.logger.info(f"尝试使用联网模型获取实时热点信息")
                    online_prompt = (
                        "请用简洁要点汇总'今天'中文互联网主要新闻与热点（3-5条），不要涉及政治敏感话题和政治人物。"
                        "偏向话题与趋势，不要细节长文；每条不超过30字；"
                        "只输出纯文本多行，不要编号、不要任何额外解释。"
                    )
                    online_client = openai.OpenAI(base_url=online_base_url, api_key=online_api_key, timeout=20)
                    online_completion = online_client.chat.completions.create(
                        model=online_model,
                        messages=[{"role": "user", "content": online_prompt}],
                        temperature=online_temperature,
                        max_tokens=min(online_max_tokens, 300),
                        timeout=20
                    )
                    online_hot_brief = (online_completion.choices[0].message.content or '').strip()
                    # 只保留前5行，避免上下文过长
                    if online_hot_brief:
                        lines_hot = [ln.strip() for ln in online_hot_brief.split('\n') if ln.strip()]
                        online_hot_brief = "\n".join(lines_hot[:5])
                        app.logger.info(f"成功获取联网热点信息，长度: {len(online_hot_brief)} 字符")
                except Exception as online_err:
                    app.logger.warning(f"联网热点检索失败，将使用常规生成: {online_err}")
                    online_hot_brief = ''
            else:
                app.logger.info("联网API密钥未配置，跳过热点检索")
        else:
            if should_use_online:
                app.logger.info("联网功能未启用，跳过热点检索")
            else:
                app.logger.info("本次未触发联网热点检索（50%概率）")
        
        # 构建融合热点的prompt
        if online_hot_brief:
            prompt = f"""
你有一个瞒着用户的小号推特账号，人设中的朋友圈人物都知道这个账号并会互动，你会在这里发一些比较真实的想法和日常。

角色设定：
{character_prompt}

当前时间：{time_str}
{recent_content}

今日热点参考（可选择性融合，避免机械复述，可以结合相关话题氛围发表个人感受）：
{online_hot_brief}

内容类型和tag使用：
1. 普通动态 - 直接发内容，不需要特殊标记
2. 备忘录类 - 用 #备忘录 tag
3. 情感宣泄 - 用 #心情 #想法 等tag
4. 日常记录 - 用 #日常 #生活 等tag
5. 重要事件 - 用 #记录 #重要 等tag
6. 热点评论 - 结合热点话题发表个人看法，可用相关tag

请根据角色性格和当前状态，可以结合今日热点话题（如果感兴趣），生成一条自然的推特动态。

要求：
- 情感表达直接、真实
- 具有活人感、可以适当结合网络热梗
- 短句不用句号
- 可以使用相关的tag（用#开头）
- 内容长度适中，像真实的推特发言
- 此次输出按一句或者一段话话，正常标点符号，不需要加反斜线
- 如果要评论热点，请结合个人角色特点和感受，不要流水账式复述

直接输出动态内容，不需要"内容："前缀。
"""
        else:
            prompt = f"""
你有一个瞒着用户的小号推特账号，人设中的朋友圈人物都知道这个账号并会互动，你会在这里发一些比较真实的想法和日常。

角色设定：
{character_prompt}

当前时间：{time_str}
{recent_content}

内容类型和tag使用：
1. 普通动态 - 直接发内容，不需要特殊标记
2. 备忘录类 - 用 #备忘录 tag
3. 情感宣泄 - 用 #心情 #想法 等tag
4. 日常记录 - 用 #日常 #生活 等tag
5. 重要事件 - 用 #记录 #重要 等tag

请根据角色性格和当前状态，随机选择一种内容类型，生成一条自然的推特动态。

要求：
- 情感表达直接、真实
- 具有活人感、可以适当结合网络热梗
- 短句不用句号
- 可以使用相关的tag（用#开头）
- 内容长度适中，像真实的推特发言
- 此次输出按一句或者一段话话，正常标点符号，不需要加反斜线

直接输出动态内容，不需要"内容："前缀。
"""

        # 打印完整提示词到控制台，方便调试
        print("\n" + "="*80)
        print(f"🎯 论坛拉取请求 - 角色: {character_name}")
        print("="*80)
        print(f"📊 提示词统计:")
        print(f"   角色设定长度: {len(character_prompt)} 字符")
        print(f"   总提示词长度: {len(prompt)} 字符")
        print(f"   当前时间: {time_str}")
        print(f"   最近内容数量: {len(recent_posts) if recent_posts else 0}")
        print(f"   联网热点检索: {'已启用' if online_hot_brief else '未启用'}")
        if online_hot_brief:
            print(f"   热点内容长度: {len(online_hot_brief)} 字符")
        print("="*80 + "\n")
        
        # 调用AI API - 优先使用论坛自定义模型，其次主模型
        use_forum_custom = bool(config.get('ENABLE_FORUM_CUSTOM_MODEL', False))
        if use_forum_custom:
            api_key = _resolve_forum_api_key(config)
            base_url = config.get('FORUM_BASE_URL', config.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com'))
            # 如果未填写论坛模型则回落到主模型
            model = (config.get('FORUM_MODEL') or config.get('MODEL', 'deepseek-v3-0324'))
            temperature = config.get('FORUM_TEMPERATURE', config.get('TEMPERATURE', 1.1))
            max_tokens = int(config.get('FORUM_MAX_TOKEN', config.get('MAX_TOKEN', 2000)))
        else:
            api_key = config.get('DEEPSEEK_API_KEY', '')
            base_url = config.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com')
            model = config.get('MODEL', 'deepseek-v3-0324')
            temperature = config.get('TEMPERATURE', 1.1)
            max_tokens = config.get('MAX_TOKEN', 2000)
        
        app.logger.info(f"准备调用AI API，模型: {model}, base_url: {base_url}")
        
        if not api_key:
            app.logger.error("API密钥未配置")
            return False, ""
        
        client = openai.OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=30  # 设置30秒超时
        )
        
        app.logger.info("开始调用AI API...")
        completion = client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": prompt
            }],
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=30  # API调用超时设置
        )
        
        reply = completion.choices[0].message.content.strip()
        app.logger.info(f"AI回复: {reply}")
        
        # 打印AI响应到控制台，方便调试
        print(f"🤖 AI模型响应:")
        print(f"   模型: {model}")
        print(f"   联网热点: {'已使用' if online_hot_brief else '未使用'}")
        print(f"   响应内容: '{reply}'")
        print(f"   响应长度: {len(reply) if reply else 0} 字符")
        print(f"   是否为空: {'是' if not reply else '否'}")
        print("="*80 + "\n")
        
        # 简化的解析逻辑 - 直接使用AI的回复作为内容
        if reply and len(reply) > 5:
            # 清理可能的格式标记
            content = reply
            
            # 移除可能的"内容："前缀（如果AI还是加了的话）
            if content.startswith("内容：") or content.startswith("内容:"):
                content = content.split("：", 1)[-1].split(":", 1)[-1].strip()
            
            # 移除一些明显的格式字符
            content = content.replace("[具体的动态内容]", "").strip()
            
            # 自动替换反斜线为换行符
            content = content.replace("\\", "\n")
            
            if len(content) > 5:
                return True, content
        
        return False, ""
    
    except Exception as e:
        app.logger.error(f"调用AI判断论坛发布失败: {e}")
        # 如果API调用失败，生成一个简单的默认内容
        import random
        default_contents = [
            "今天心情不错呢~",
            "分享一下今天的小心情",
            "生活总是充满惊喜",
            "又是美好的一天",
            "想和大家分享一些想法",
            "今天有点特别的感觉",
            "心情很好，想说点什么",
            "日常的小确幸"
        ]
        return True, random.choice(default_contents)

def load_character_prompt(character_name):
    """读取角色设定文件"""
    try:
        prompt_file = os.path.join('prompts', f'{character_name}.md')
        if os.path.exists(prompt_file):
            with open(prompt_file, 'r', encoding='utf-8') as f:
                return f.read()
        return None
    except Exception as e:
        app.logger.error(f"读取角色设定失败: {e}")
        return None

def get_character_forum_posts(character_name, limit=20):
    """获取角色论坛帖子"""
    try:
        forum_file = _forum_file_path(character_name)
        if not os.path.exists(forum_file):
            return []
        
        with open(forum_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        posts = data.get('posts', [])
        # 按时间倒序排列
        posts.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        
        return posts[:limit] if limit else posts
    
    except Exception as e:
        app.logger.error(f"获取论坛帖子失败: {e}")
        return []

def _forum_file_path(character_name):
    _ensure_forum_dir_exists()
    filename = f'forum_data_{character_name}.json'
    return os.path.join(FORUM_DATA_DIR, filename)

def load_forum_data(character_name):
    """加载论坛数据文件"""
    forum_file = _forum_file_path(character_name)
    if os.path.exists(forum_file):
        try:
            with open(forum_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {'posts': [], 'npcs': []}
    return {'posts': [], 'npcs': []}

def save_forum_data(character_name, data):
    """保存论坛数据文件（覆盖写入）"""
    forum_file = _forum_file_path(character_name)
    with open(forum_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _find_post_by_id(posts, post_id):
    for post in posts:
        if post.get('id') == post_id:
            return post
    return None

def toggle_like_forum_post(character_name, post_id, like_state=None):
    """切换或设置帖子点赞状态。like_state=None 表示切换；True/False 表示显式设置"""
    data = load_forum_data(character_name)
    posts = data.get('posts', [])
    post = _find_post_by_id(posts, post_id)
    if not post:
        return None
    current_liked = bool(post.get('liked_by_me', False))
    new_liked = (not current_liked) if like_state is None else bool(like_state)
    likes_count = int(post.get('likes', 0) or 0)
    if new_liked != current_liked:
        if new_liked:
            likes_count += 1
        else:
            likes_count = max(0, likes_count - 1)
        post['likes'] = likes_count
        post['liked_by_me'] = new_liked
        save_forum_data(character_name, data)
    return {'likes': post.get('likes', 0), 'liked_by_me': post.get('liked_by_me', False)}

def add_forum_comment(character_name, post_id, content, author_display_name=None):
    """为指定帖子添加一条评论。返回新评论对象。"""
    data = load_forum_data(character_name)
    posts = data.get('posts', [])
    post = _find_post_by_id(posts, post_id)
    if not post:
        return None
    if 'comments' not in post or not isinstance(post['comments'], list):
        post['comments'] = []
    now = datetime.now()
    comment = {
        'id': f"comment_{int(now.timestamp()*1000)}",
        'npc_name': author_display_name or f"{character_name} 的小号",
        'content': content,
        'timestamp': now.strftime("%Y-%m-%d %H:%M:%S"),
        'author': 'self'
    }
    post['comments'].append(comment)
    save_forum_data(character_name, data)
    return comment

def ensure_comment_ids_in_post(post):
    """为旧数据中的评论补充id字段"""
    if not post or 'comments' not in post:
        return
    for c in post.get('comments', []):
        if 'id' not in c:
            # 以时间戳或随机数填补
            c['id'] = f"comment_{int(time.time()*1000)}_{random.randint(100,999)}"

def add_forum_comment_with_parent(character_name, post_id, content, author, author_display, parent_comment_id=None):
    """为帖子添加一条评论，支持父评论。author: 'me' 或 'character'"""
    data = load_forum_data(character_name)
    posts = data.get('posts', [])
    post = _find_post_by_id(posts, post_id)
    if not post:
        return None
    if 'comments' not in post or not isinstance(post['comments'], list):
        post['comments'] = []
    ensure_comment_ids_in_post(post)
    now = datetime.now()
    comment = {
        'id': f"comment_{int(now.timestamp()*1000)}",
        'npc_name': author_display,
        'content': content,
        'timestamp': now.strftime("%Y-%m-%d %H:%M:%S"),
        'author': author,  # 'me' 或 'character'
    }
    if parent_comment_id:
        comment['parent_id'] = parent_comment_id
    post['comments'].append(comment)
    save_forum_data(character_name, data)
    return comment

def generate_character_conversation_reply(character_name, post_content, user_message):
    """基于角色设定，生成角色对用户的回复"""
    try:
        config = parse_config()
        character_prompt = load_character_prompt(character_name) or ''
        time_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        prompt = f"""
你是“{character_name}”，以下是你的角色设定：
{character_prompt}

场景：你的小号在社交平台发了一条动态，下面有一条来自用户的回复。请以你的人设与语气，回复用户的这条话。

原始动态：{post_content}
用户的话：{user_message}

要求：
1. 中文回复，口语化，贴近真实社交平台风格
2. 35字以内，尽量一到两句
3. 可以自然使用emoji但不要太多
4. 直接输出回复文本，不要任何前后缀
"""

        # 优先使用论坛自定义模型
        use_forum_custom = bool(config.get('ENABLE_FORUM_CUSTOM_MODEL', False))
        if use_forum_custom:
            api_key = _resolve_forum_api_key(config)
            base_url = config.get('FORUM_BASE_URL', config.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com'))
            model = (config.get('FORUM_MODEL') or config.get('MODEL', 'deepseek-v3-0324'))
            temperature = config.get('FORUM_TEMPERATURE', config.get('TEMPERATURE', 1.0))
            max_tokens = min(int(config.get('FORUM_MAX_TOKEN', config.get('MAX_TOKEN', 2000))), 120)
        else:
            api_key = config.get('DEEPSEEK_API_KEY', '')
            base_url = config.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com')
            model = config.get('MODEL', 'deepseek-v3-0324')
            temperature = config.get('TEMPERATURE', 1.0)
            max_tokens = min(config.get('MAX_TOKEN', 2000), 120)

        if not api_key:
            return "收到啦～"

        client = openai.OpenAI(base_url=base_url, api_key=api_key, timeout=20)
        completion = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=20
        )
        reply = completion.choices[0].message.content.strip()
        if not reply:
            reply = "收到啦～"
        # 基础清理
        if reply.startswith("回复：") or reply.startswith("回复:"):
            reply = reply.split("：", 1)[-1].split(":", 1)[-1].strip()
        reply = reply.replace("\\", "\n").strip()
        return reply[:100]
    except Exception as e:
        app.logger.error(f"生成角色会话回复失败: {e}")
        return "了解！"

def get_liked_forum_posts(character_name):
    data = load_forum_data(character_name)
    posts = data.get('posts', [])
    liked = [p for p in posts if p.get('liked_by_me')]
    # 保持与其他接口一致：按时间倒序
    try:
        liked.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    except Exception:
        pass
    return liked

def get_thread_posts(character_name):
    data = load_forum_data(character_name)
    posts = data.get('posts', [])
    threads = [p for p in posts if p.get('comments') and isinstance(p.get('comments'), list) and len(p.get('comments')) > 0]
    try:
        threads.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    except Exception:
        pass
    return threads

@app.route('/api/forum/like/<character_name>/<post_id>', methods=['POST'])
@login_required
def like_forum_post(character_name, post_id):
    """点赞/取消点赞 指定帖子。请求体可包含 like: true/false；不含则为切换。"""
    try:
        like_state = None
        if request.is_json and isinstance(request.json, dict) and 'like' in request.json:
            like_state = bool(request.json.get('like'))
        result = toggle_like_forum_post(character_name, post_id, like_state)
        if not result:
            return jsonify({'error': '帖子不存在'}), 404
        return jsonify(result)
    except Exception as e:
        app.logger.error(f"点赞操作失败: {e}")
        return jsonify({'error': f'点赞失败: {str(e)}'}), 500

@app.route('/api/forum/likes/<character_name>')
@login_required
def get_likes(character_name):
    """获取我喜欢的帖子列表"""
    try:
        posts = get_liked_forum_posts(character_name)
        return jsonify({'posts': posts})
    except Exception as e:
        app.logger.error(f"获取喜欢列表失败: {e}")
        return jsonify({'error': f'获取失败: {str(e)}'}), 500

@app.route('/api/forum/reply/<character_name>/<post_id>', methods=['POST'])
@login_required
def reply_to_post(character_name, post_id):
    """对帖子进行回复（添加一条评论），并自动生成角色的AI回复"""
    try:
        if not request.is_json:
            return jsonify({'error': '请求格式错误'}), 400
        content = str(request.json.get('content', '')).strip()
        parent_comment_id = request.json.get('parent_comment_id')
        if not content:
            return jsonify({'error': '回复内容不能为空'}), 400
        if len(content) > 500:
            return jsonify({'error': '回复内容过长（最多500字）'}), 400
        # 加载帖子用于上下文
        data = load_forum_data(character_name)
        post = _find_post_by_id(data.get('posts', []), post_id)
        if not post:
            return jsonify({'error': '帖子不存在，无法回复'}), 404

        ensure_comment_ids_in_post(post)

        # 先写入用户的评论
        my_comment = add_forum_comment_with_parent(
            character_name=character_name,
            post_id=post_id,
            content=content,
            author='me',
            author_display='我',
            parent_comment_id=parent_comment_id
        )

        # 生成角色AI回复
        ai_reply_text = generate_character_conversation_reply(
            character_name, post.get('content', ''), content
        )
        ai_comment = add_forum_comment_with_parent(
            character_name=character_name,
            post_id=post_id,
            content=ai_reply_text,
            author='character',
            author_display=f"{character_name} 的小号",
            parent_comment_id=my_comment['id']
        )

        return jsonify({'my_comment': my_comment, 'ai_comment': ai_comment})
    except Exception as e:
        app.logger.error(f"添加回复失败: {e}")
        return jsonify({'error': f'添加回复失败: {str(e)}'}), 500

@app.route('/api/forum/threads/<character_name>')
@login_required
def get_forum_threads(character_name):
    """获取包含回复的帖子列表（用于“推文和回复”）"""
    try:
        posts = get_thread_posts(character_name)
        # 为兼容旧数据，补全评论id
        for p in posts:
            ensure_comment_ids_in_post(p)
        return jsonify({'posts': posts})
    except Exception as e:
        app.logger.error(f"获取线程列表失败: {e}")
        return jsonify({'error': f'获取失败: {str(e)}'}), 500

# ===== 喜欢动态（AI生成） =====
def generate_likes_feed_items(character_name, use_online=False):
    """生成角色会喜欢的外部博文（AI生成，最多3条）。
    当启用联网模型时：先用联网模型检索“今日热点摘要”，再把摘要交给论坛模型（若启用）或主模型生成三条博文。
    """
    try:
        config = parse_config()
        character_prompt = load_character_prompt(character_name) or ''
        time_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        # --- 阶段一：可选的联网热点检索 ---
        online_hot_brief = ''
        if use_online and bool(config.get('ENABLE_ONLINE_API', False)):
            online_api_key = (config.get('ONLINE_API_KEY') or '').strip()
            online_base_url = config.get('ONLINE_BASE_URL', '')
            online_model = (config.get('ONLINE_MODEL') or '').strip()
            online_temperature = float(config.get('ONLINE_API_TEMPERATURE', 0.7))
            online_max_tokens = int(config.get('ONLINE_API_MAX_TOKEN', 2000))

            if online_api_key and online_base_url and online_model:
                try:
                    online_prompt = (
                        "请用简洁要点汇总'今天'中文互联网主要新闻与热点（5-8条），不要涉及政治敏感话题和政治人物"
                        "偏向话题与趋势，不要细节长文；每条不超过30字；"
                        "只输出纯文本多行，不要编号、不要任何额外解释。"
                    )
                    online_client = openai.OpenAI(base_url=online_base_url, api_key=online_api_key, timeout=20)
                    online_completion = online_client.chat.completions.create(
                        model=online_model,
                        messages=[{"role": "user", "content": online_prompt}],
                        temperature=online_temperature,
                        max_tokens=min(online_max_tokens, 500),
                        timeout=20
                    )
                    online_hot_brief = (online_completion.choices[0].message.content or '').strip()
                    # 只保留前8行，避免上下文过长
                    if online_hot_brief:
                        lines_hot = [ln.strip() for ln in online_hot_brief.split('\n') if ln.strip()]
                        online_hot_brief = "\n".join(lines_hot[:8])
                except Exception as online_err:
                    app.logger.warning(f"联网热点检索失败，将回退本地生成: {online_err}")
                    online_hot_brief = ''

        # --- 阶段二：用论坛模型（若启用）或主模型生成三条内容 ---
        # 选择生成阶段所用模型
        gen_base_url = config.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com')
        gen_model = config.get('MODEL', 'deepseek-v3-0324')
        gen_api_key = (config.get('DEEPSEEK_API_KEY', '')).strip()
        gen_temperature = float(config.get('TEMPERATURE', 1.0))
        gen_max_tokens = min(int(config.get('MAX_TOKEN', 2000)), 400)

        if bool(config.get('ENABLE_FORUM_CUSTOM_MODEL', False)):
            gen_base_url = config.get('FORUM_BASE_URL', gen_base_url)
            gen_model = (config.get('FORUM_MODEL') or gen_model)
            gen_api_key = _resolve_forum_api_key(config)
            gen_temperature = float(config.get('FORUM_TEMPERATURE', gen_temperature))
            gen_max_tokens = min(int(config.get('FORUM_MAX_TOKEN', gen_max_tokens)), 400)

        if not gen_api_key:
            # 无可用key，返回一些占位
            return [
                {'author': '热榜·话题', 'content': '今天的小确幸：晚霞好看到想停下脚步 #日常', 'timestamp': time_str},
                {'author': '城市·夜跑', 'content': '月亮今晚很好看，风也温柔，继续跑下去吧', 'timestamp': time_str},
            ]

        # 组合最终生成提示词
        if online_hot_brief:
            final_prompt = f"""
你在做内容聚合，给角色“{character_name}”挑选他会喜欢的3条中文博文（每条不超过60字）。
角色设定：
{character_prompt}

今日热点（仅作参考，避免机械复述，可融合相关话题与氛围）：
{online_hot_brief}

请严格按以下要求输出：
1) 仅输出三行内容，不要任何额外说明或序号
2) 每行格式：作者名 - 文本内容（可含#话题）
3) 不要使用反斜线 \\, 不要用 \n 或 \\ 分隔，直接正常标点与空格
4) 不要使用引号或代码块
"""
        else:
            final_prompt = f"""
你在做内容聚合，给角色“{character_name}”挑选他会喜欢的3条中文博文（每条不超过60字）。
角色设定：
{character_prompt}

如果具备联网能力，请结合当日热门趋势或资讯做简洁融合；否则输出贴合其性格与日常审美的生活化内容。

请严格按以下要求输出：
1) 仅输出三行内容，不要任何额外说明或序号
2) 每行格式：作者名 - 文本内容（可含#话题）
3) 不要使用反斜线 \\, 不要用 \n 或 \\ 分隔，直接正常标点与空格
4) 不要使用引号或代码块
"""

        gen_client = openai.OpenAI(base_url=gen_base_url, api_key=gen_api_key, timeout=25)
        gen_completion = gen_client.chat.completions.create(
            model=gen_model,
            messages=[{"role": "user", "content": final_prompt}],
            temperature=gen_temperature,
            max_tokens=gen_max_tokens,
            timeout=25
        )
        reply = (gen_completion.choices[0].message.content or '').strip()

        lines = [ln.strip() for ln in reply.split('\n') if ln.strip()]
        items = []
        for ln in lines[:3]:
            # 解析“作者 - 内容”
            if ' - ' in ln:
                author, content = ln.split(' - ', 1)
            elif '-' in ln:
                author, content = ln.split('-', 1)
            else:
                author, content = '博主', ln
            # 清理反斜线，保持单行
            safe_content = content.replace('\\', '').strip()
            items.append({
                'author': author.strip()[:20],
                'content': safe_content,
                'timestamp': time_str,
                'likes': random.randint(20, 500),
                'replies': random.randint(0, 120)
            })
        if not items:
            items = [{
                'author': '博主',
                'content': reply.replace('\\', '')[:60],
                'timestamp': time_str,
                'likes': random.randint(20, 500),
                'replies': random.randint(0, 120)
            }]
        return items
    except Exception as e:
        app.logger.error(f"生成喜欢Feed失败: {e}")
        return []

@app.route('/api/forum/likes_feed/<character_name>')
@login_required
def get_likes_feed(character_name):
    try:
        data = load_forum_data(character_name)
        feed = data.get('liked_feed', [])
        # 补齐缺失的统计字段，便于老数据兼容
        changed = False
        for it in feed:
            if 'likes' not in it:
                it['likes'] = random.randint(20, 500)
                changed = True
            if 'replies' not in it:
                it['replies'] = random.randint(0, 120)
                changed = True
        if changed:
            save_forum_data(character_name, data)
        # 倒序（最新在前）
        try:
            feed.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        except Exception:
            pass
        return jsonify({'items': feed})
    except Exception as e:
        app.logger.error(f"获取喜欢Feed失败: {e}")
        return jsonify({'error': f'获取失败: {str(e)}'}), 500

@app.route('/api/forum/refresh_likes/<character_name>', methods=['POST'])
@login_required
def refresh_likes_feed(character_name):
    try:
        config = parse_config()
        use_online = bool(config.get('ENABLE_ONLINE_API', False))
        items = generate_likes_feed_items(character_name, use_online=use_online)
        if not items:
            return jsonify({'has_new_content': False, 'message': '暂时没有新的喜欢'}), 200
        # 存储
        data = load_forum_data(character_name)
        if 'liked_feed' not in data or not isinstance(data['liked_feed'], list):
            data['liked_feed'] = []
        now_ts = int(time.time()*1000)
        for idx, it in enumerate(items):
            it['id'] = f"liked_{now_ts}_{idx}"
            if 'likes' not in it:
                it['likes'] = random.randint(20, 500)
            if 'replies' not in it:
                it['replies'] = random.randint(0, 120)
            data['liked_feed'].append(it)
        save_forum_data(character_name, data)
        return jsonify({'has_new_content': True, 'items': items})
    except Exception as e:
        app.logger.error(f"刷新喜欢Feed失败: {e}")
        return jsonify({'error': f'刷新失败: {str(e)}'}), 500

@app.route('/api/forum/likes_feed/<character_name>/<item_id>', methods=['DELETE'])
@login_required
def delete_likes_feed_item(character_name, item_id):
    """删除AI生成的喜欢Feed中的一项，不影响论坛原帖"""
    try:
        data = load_forum_data(character_name)
        feed = data.get('liked_feed', [])
        original_len = len(feed)
        feed = [it for it in feed if str(it.get('id')) != str(item_id)]
        if len(feed) == original_len:
            return jsonify({'error': '未找到该喜欢项'}), 404
        data['liked_feed'] = feed
        save_forum_data(character_name, data)
        return jsonify({'message': '已移除'}), 200
    except Exception as e:
        app.logger.error(f"删除喜欢Feed项失败: {e}")
        return jsonify({'error': f'删除失败: {str(e)}'}), 500

# ===== 头像管理API =====
@app.route('/api/forum/avatar/upload/<character_name>', methods=['POST'])
@login_required
def upload_avatar(character_name):
    """上传角色头像"""
    try:
        # 验证角色名
        config = parse_config()
        listen_list = config.get('LISTEN_LIST', [])
        character_found = False
        for item in listen_list:
            if len(item) >= 2 and item[1] == character_name:
                character_found = True
                break
        
        if not character_found:
            return jsonify({'error': '角色不存在'}), 404
        
        if 'avatar' not in request.files:
            return jsonify({'error': '没有上传文件'}), 400
        
        file = request.files['avatar']
        if file.filename == '':
            return jsonify({'error': '没有选择文件'}), 400
        
        if not allowed_avatar_file(file.filename):
            return jsonify({'error': '不支持的文件格式，请上传PNG、JPG、JPEG、GIF或WebP格式的图片'}), 400
        
        # 删除旧的头像文件
        old_avatar_path = get_avatar_file_path(character_name)
        if old_avatar_path and os.path.exists(old_avatar_path):
            try:
                os.remove(old_avatar_path)
                app.logger.info(f"删除旧头像文件: {old_avatar_path}")
            except Exception as e:
                app.logger.warning(f"删除旧头像文件失败: {e}")
        
        # 保存新的头像文件
        file_extension = file.filename.rsplit('.', 1)[1].lower()
        filename = get_avatar_filename(character_name, file_extension)
        filepath = os.path.join(FORUM_AVATAR_DIR, filename)
        
        file.save(filepath)
        app.logger.info(f"头像上传成功: {filepath}")
        
        return jsonify({
            'message': '头像上传成功',
            'filename': filename
        })
    
    except Exception as e:
        app.logger.error(f"头像上传失败: {e}")
        return jsonify({'error': f'上传失败: {str(e)}'}), 500

@app.route('/api/forum/avatar/<character_name>')
@login_required
def get_avatar(character_name):
    """获取角色头像"""
    try:
        avatar_path = get_avatar_file_path(character_name)
        if not avatar_path:
            return jsonify({'error': '头像不存在'}), 404
        
        return send_file(avatar_path)
    
    except Exception as e:
        app.logger.error(f"获取头像失败: {e}")
        return jsonify({'error': f'获取失败: {str(e)}'}), 500

@app.route('/api/forum/avatar/<character_name>', methods=['DELETE'])
@login_required
def delete_avatar(character_name):
    """删除角色头像"""
    try:
        # 验证角色名
        config = parse_config()
        listen_list = config.get('LISTEN_LIST', [])
        character_found = False
        for item in listen_list:
            if len(item) >= 2 and item[1] == character_name:
                character_found = True
                break
        
        if not character_found:
            return jsonify({'error': '角色不存在'}), 404
        
        avatar_path = get_avatar_file_path(character_name)
        if not avatar_path:
            return jsonify({'error': '头像不存在'}), 404
        
        os.remove(avatar_path)
        app.logger.info(f"头像删除成功: {avatar_path}")
        
        return jsonify({'message': '头像删除成功'})
    
    except Exception as e:
        app.logger.error(f"头像删除失败: {e}")
        return jsonify({'error': f'删除失败: {str(e)}'}), 500

def add_forum_post(character_name, content):
    """添加新的论坛帖子"""
    try:
        forum_file = _forum_file_path(character_name)
        
        # 读取现有数据
        if os.path.exists(forum_file):
            with open(forum_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        else:
            data = {'posts': [], 'npcs': []}
        
        # 创建新帖子
        now = datetime.now()
        new_post = {
            'id': f"post_{int(now.timestamp())}",
            'content': content,
            'timestamp': now.strftime("%Y-%m-%d %H:%M:%S"),
            'likes': random.randint(5, 50),
            'comments': []
        }
        
        # 生成NPC评论：在生成函数内部对每个NPC进行独立概率判定
        npc_comments = generate_npc_comments(character_name, content)
        new_post['comments'] = npc_comments
        
        # 添加到数据中
        data['posts'].append(new_post)
        
        # 保持最多100条帖子
        if len(data['posts']) > 100:
            data['posts'] = data['posts'][-100:]
        
        # 保存数据
        with open(forum_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        return new_post
    
    except Exception as e:
        app.logger.error(f"添加论坛帖子失败: {e}")
        return None

def generate_npc_comments(character_name, post_content):
    """生成NPC评论 - 基于用户配置的NPC设定和回复风格"""
    try:
        # 从角色设定中提取可能的NPC名单
        character_prompt = load_character_prompt(character_name)
        
        # 尝试从角色设定中提取人物名称
        extracted_names = []
        if character_prompt:
            # 查找可能的人名（中文姓名模式）
            name_patterns = [
                r'([小]\w{1,2})',  # 小桃、小明等
                r'([李王张刘陈杨黄赵吴周徐孙朱马胡郭何高林罗郑梁谢宋唐许邓冯韩曹曾彭肖蔡潘田董袁于余叶蒋杜苏魏程吕丁沈任姚卢姜崔钟谭陆汪范金石廖贾夏韦傅方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤]\w{1,2})',  # 常见姓氏
                r'(\w{2,3}(?:同学|朋友|室友|同事))',  # XX同学、XX朋友等
            ]
            
            for pattern in name_patterns:
                matches = re.findall(pattern, character_prompt)
                extracted_names.extend(matches)
        

        # 合并名单，优先使用从角色设定中提取的名称
        all_names = list(set(extracted_names ))
        
        # 尝试读取用户配置的NPC设定
        npc_config = load_npc_config()
        configured_npcs = []
        
        if npc_config and 'selected_npcs' in npc_config:
            for npc_name in npc_config['selected_npcs']:
                if npc_name in npc_config.get('npc_settings', {}):
                    npc_settings = npc_config['npc_settings'][npc_name]
                    configured_npcs.append({
                        'name': npc_name,
                        'settings': npc_settings
                    })
        
        # 只使用用户配置的NPC生成智能回复
        if configured_npcs:
            app.logger.info(f"使用配置的NPC生成智能回复: {[npc['name'] for npc in configured_npcs]}")
            return generate_configured_npc_comments(configured_npcs, post_content, character_name)
        
        # 如果没有配置NPC，返回空列表
        app.logger.info("未配置NPC，不生成评论")
        return []
        
    except Exception as e:
        app.logger.error(f"生成NPC评论失败: {e}")
        return []

def generate_configured_npc_comments(configured_npcs, post_content, character_name):
    """基于用户配置生成智能NPC评论（逐条串行，每个NPC独立60%概率）"""
    try:
        comments = []
        reply_probability = 0.6  # 每个NPC独立回复概率

        # 逐条串行生成，避免并行导致的潜在超时/限速问题
        for npc in configured_npcs:
            # 概率判定：每个NPC独立60%概率回复
            if random.random() >= reply_probability:
                continue

            npc_name = npc['name']
            npc_settings = npc['settings']

            # 基于配置生成智能回复（串行调用）
            comment_text = generate_smart_npc_reply(
                npc_name,
                npc_settings,
                post_content,
                character_name
            )

            if comment_text:
                # 随机延迟时间
                base_time = datetime.now()
                delay_minutes = random.randint(2, 120)  # 2分钟到2小时
                comment_time = base_time + timedelta(minutes=delay_minutes)

                comments.append({
                    'npc_name': npc_name,
                    'content': comment_text,
                    'timestamp': comment_time.strftime("%Y-%m-%d %H:%M:%S")
                })

        return comments

    except Exception as e:
        app.logger.error(f"生成配置NPC评论失败: {e}")
        return []

def generate_smart_npc_reply(npc_name, npc_settings, post_content, character_name):
    """基于NPC配置生成智能回复"""
    try:
        # 构建AI提示词
        language_style = npc_settings.get('language_style', 'casual')
        relationship = npc_settings.get('relationship', 'friend')
        example_output = npc_settings.get('example_output', '')
        other_settings = npc_settings.get('other_settings', '')
        
        # 语言风格映射
        style_descriptions = {
            'formal': '正式、礼貌、得体',
            'casual': '随意、轻松、日常',
            'friendly': '友好、温暖、亲切',
            'professional': '专业、严谨、有见地',
            'humorous': '幽默、有趣、轻松',
            'serious': '严肃、认真、深思熟虑'
        }
        
        # 关系映射
        relationship_descriptions = {
            'friend': '朋友关系，可以开玩笑、分享感受',
            'colleague': '同事关系，保持专业但友好',
            'family': '家人关系，关心、温暖、支持',
            'stranger': '陌生人关系，礼貌、适度',
            'student': '学生关系，学习、请教、尊重'
        }
        
        prompt = f"""
你是一个名为"{npc_name}"的NPC，正在回复"{character_name}"的社交动态。

NPC设定：
- 语言风格：{style_descriptions.get(language_style, '随意')}
- 与{character_name}的关系：{relationship_descriptions.get(relationship, '朋友')}
- 示例回复风格：{example_output if example_output else '根据语言风格和关系自然回复'}
- 其他要求：{other_settings if other_settings else '无特殊要求'}

{character_name}的动态内容：
{post_content}

请根据你的NPC设定，生成一条符合你性格和关系的回复。要求：
1. 回复要自然、真实，符合你的语言风格
2. 体现你与{character_name}的关系
3. 回复长度适中,20字以内
4. 可以表达共鸣、关心、建议等情感
5. 不要过于复杂或冗长
6. 此次输出按一句或者一段话话，正常标点符号，不需要加反斜线
7. 如果人物比较活泼，可以适当跟据氛围加入emoji如😥🤣👂👍👎👊👏👌👍👎👊👏👌

直接输出回复内容，不需要引号或其他格式。
"""
        
        # 根据配置选择论坛自定义模型或主模型
        try:
            config = parse_config()
            use_forum_custom = bool(config.get('ENABLE_FORUM_CUSTOM_MODEL', False))

            if use_forum_custom:
                api_key = _resolve_forum_api_key(config)
                base_url = config.get('FORUM_BASE_URL', config.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com'))
                model = (config.get('FORUM_MODEL') or config.get('MODEL', 'deepseek-v3-0324'))
                temperature = config.get('FORUM_TEMPERATURE', config.get('TEMPERATURE', 1.1))
                max_tokens = config.get('FORUM_MAX_TOKEN', config.get('MAX_TOKEN', 2000))
            else:
                api_key = config.get('DEEPSEEK_API_KEY', '')
                base_url = config.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com')
                model = config.get('MODEL', 'deepseek-v3-0324')
                temperature = config.get('TEMPERATURE', 1.1)
                max_tokens = config.get('MAX_TOKEN', 2000)
        except Exception as e:
            app.logger.error(f"获取模型配置失败: {e}")
            return generate_fallback_reply(npc_name, language_style, relationship)
        
        if not api_key:
            app.logger.warning(f"API密钥未配置，使用默认回复")
            return generate_fallback_reply(npc_name, language_style, relationship)
        
        client = openai.OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=30  # 提高超时阈值，减少长尾超时
        )
        
        completion = client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": prompt
            }],
            temperature=temperature,
            max_tokens=min(max_tokens, 120),  # 按20字回复需求收紧，降低超时概率
            timeout=30
        )
        
        reply = completion.choices[0].message.content.strip()
        
        # 清理回复内容
        if reply and len(reply) > 3:
            # 移除可能的格式标记
            reply = reply.replace('"', '').replace('"', '').replace(''', '').replace(''', '')
            reply = reply.replace('回复：', '').replace('回复:', '').replace('NPC回复：', '').replace('NPC回复:', '')
            
            # 自动替换反斜线为换行符
            reply = reply.replace("\\", "\n")
            
            reply = reply.strip()
            
            if len(reply) > 3:
                return reply
        
        # 如果AI回复失败，使用备用回复
        return generate_fallback_reply(npc_name, language_style, relationship)
        
    except Exception as e:
        app.logger.error(f"生成智能NPC回复失败: {e}")
        return generate_fallback_reply(npc_name, 'casual', 'friend')

def generate_fallback_reply(npc_name, language_style, relationship):
    """生成备用回复（当AI调用失败时使用）"""
    try:
        # 基于语言风格和关系的备用回复模板
        fallback_templates = {
            'formal': {
                'friend': ['确实如此', '我理解你的感受', '说得很有道理'],
                'colleague': ['这个观点不错', '值得思考', '有见地'],
                'family': ['我支持你', '相信你能行', '为你感到骄傲'],
                'stranger': ['很有意思', '谢谢分享', '学到了'],
                'mentor': ['很好的想法', '继续努力', '保持这种状态'],
                'student': ['学到了', '谢谢指导', '我会努力的']
            },
            'casual': {
                'friend': ['哈哈哈哈哈', '这个我懂', '真实', '好可爱wwww'],
                'colleague': ['赞同', '同感+1', '确实如此', '支持'],
                'family': ['加油鸭', '支持！', '理解理解', '这就是你'],
                'stranger': ['有意思', '谢谢分享', '学到了', '不错'],
                'mentor': ['很好', '继续', '保持', '不错'],
                'student': ['谢谢', '学到了', '好的', '明白']
            },
            'friendly': {
                'friend': ['好可爱wwww', '理解理解', '加油鸭', '支持！'],
                'colleague': ['赞同', '同感+1', '确实如此', '支持'],
                'family': ['我支持你', '相信你能行', '为你感到骄傲'],
                'stranger': ['很有意思', '谢谢分享', '学到了'],
                'mentor': ['很好的想法', '继续努力', '保持这种状态'],
                'student': ['学到了', '谢谢指导', '我会努力的']
            }
        }
        
        # 获取对应的模板
        templates = fallback_templates.get(language_style, fallback_templates['casual'])
        relationship_templates = templates.get(relationship, templates['friend'])
        
        # 随机选择回复
        return random.choice(relationship_templates)
        
    except Exception as e:
        app.logger.error(f"生成备用回复失败: {e}")
        return "赞同"



def load_npc_config():
    """从配置文件加载NPC配置"""
    try:
        npc_config_file = _npc_config_file_path()
        
        if not os.path.exists(npc_config_file):
            app.logger.info("NPC配置文件不存在")
            return None
        
        with open(npc_config_file, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
        
        # 确保配置格式完整
        if 'selected_npcs' not in config_data:
            config_data['selected_npcs'] = []
        if 'npc_settings' not in config_data:
            config_data['npc_settings'] = {}
        
        # 为每个选中的NPC确保有默认设置
        for npc_name in config_data.get('selected_npcs', []):
            if npc_name not in config_data.get('npc_settings', {}):
                config_data['npc_settings'][npc_name] = {
                    'language_style': 'casual',
                    'relationship': 'friend',
                    'example_output': '',
                    'other_settings': ''
                }
                app.logger.info(f"为NPC {npc_name} 添加默认设置")
            else:
                app.logger.info(f"NPC {npc_name} 保持原有设置")
        
        app.logger.info(f"成功加载NPC配置: {config_data}")
        return config_data
        
    except Exception as e:
        app.logger.error(f"加载NPC配置失败: {e}")
        return None

def get_default_config():
    return {
        "LISTEN_LIST": [['微信名1', '默认角色']],
        "PRIVATE_CHAT_MODE": 'whitelist',
        "PRIVATE_CHAT_BLACKLIST": [],
        "DEFAULT_PROMPT_FILE": '默认角色',
        "DEEPSEEK_API_KEY": '',
        "DEEPSEEK_BASE_URL": 'https://ai.qiqiapi.top/v1',
        "MODEL": 'gpt-5.6-terra',
        "MODEL_REQUEST_TIMEOUT_SECONDS": 180,
        "MAX_GROUPS": 5,
        "MAX_TOKEN": 2000,
        "TEMPERATURE": 0.7,
        "MOONSHOT_API_KEY": '',
        "MOONSHOT_BASE_URL": 'https://ai.qiqiapi.top/v1',
        "MOONSHOT_MODEL": 'gpt-5.4-mini',
        "MOONSHOT_TEMPERATURE": 0.8,
        "ENABLE_IMAGE_RECOGNITION": True,
        "ENABLE_EMOJI_RECOGNITION": True,
        "ENABLE_REPLY_QUIET_TIME": True,
        "REPLY_QUIET_TIME_START": '23:00',
        "REPLY_QUIET_TIME_END": '8:00',
        "ENABLE_RANDOM_REPLY_DELAY": False,
        "REPLY_DELAY_FAST_MIN_SECONDS": 0,
        "REPLY_DELAY_FAST_MAX_SECONDS": 1,
        "REPLY_DELAY_SLOW_MIN_SECONDS": 1,
        "REPLY_DELAY_SLOW_MAX_SECONDS": 3,
        "REPLY_DELAY_SLOW_PROBABILITY": 10,
        "USER_INFO_PROFILE_QUESTION_INTERVAL_DAYS": 7,
        "DEFAULT_FAVORABILITY_SCORE": 30.0,
        "FAVORABILITY_POSITIVE_CHANGE": 0.5,
        "FAVORABILITY_VERY_POSITIVE_CHANGE": 1.0,
        "FAVORABILITY_MILD_NEGATIVE_CHANGE": -0.5,
        "FAVORABILITY_STRONG_NEGATIVE_CHANGE": -1.0,
        "FAVORABILITY_DECAY_GRACE_DAYS": 3,
        "FAVORABILITY_DECAY_PER_DAY": 0.5,
        "FAVORABILITY_MAX_OFFLINE_DECAY": 10.0,
        "FAVORABILITY_DUPLICATE_WINDOW_MINUTES": 10,
        "FAVORABILITY_STAGE_RULES": get_default_favorability_stage_rules(),
        "ENABLE_ADULT_INTIMACY": True,
        "ADULT_INTIMACY_MIN_SCORE": 80,
        "ADULT_INTIMACY_PROACTIVE_SCORE": 90,
        "ADULT_INTIMACY_MEDIUM_SIGNALS_REQUIRED": 2,
        "ADULT_INTIMACY_SESSION_TIMEOUT_HOURS": 12,
        "ENABLE_ADULT_MODEL": False,
        "ADULT_BASE_URL": 'https://ai.qiqiapi.top/v1',
        "ADULT_API_KEY": '',
        "ADULT_MODEL": 'grok-4.5',
        "ADULT_TEMPERATURE": 0.7,
        "ADULT_MAX_TOKEN": 2000,
        "ADULT_TIMEOUT_SECONDS": 180,
        "ADULT_FALLBACK_TO_MAIN": False,
        "ENABLE_CHAT_ACTIVE_TOPICS": False,
        "CHAT_ACTIVE_TOPIC_STRATEGY": 'ordered',
        "CHAT_ACTIVE_TOPIC_MIN_TURNS": 5,
        "EMOJI_DIR": 'emojis',
        "ENABLE_EMOJI_SENDING": True,
        "EMOJI_SENDING_PROBABILITY": 25,
        "AUTO_MESSAGE": '请你模拟系统设置的角色，在微信上找对方继续刚刚的话题或者询问对方在做什么',
        "ENABLE_AUTO_MESSAGE": True,
        "AUTO_MESSAGE_LIST": [],
        "MIN_COUNTDOWN_HOURS": 0.5,
        "MAX_COUNTDOWN_HOURS": 1.0,
        "QUIET_TIME_START": '22:00',
        "QUIET_TIME_END": '8:00',
        "AVERAGE_TYPING_SPEED": 0.03,
        "RANDOM_TYPING_SPEED_MIN": 0,
        "RANDOM_TYPING_SPEED_MAX": 0.02,
        "SEPARATE_ROW_SYMBOLS": True,
        "ENABLE_MEMORY": True,
        "MEMORY_TEMP_DIR": 'Memory_Temp',
        "MAX_MESSAGE_LOG_ENTRIES": 30,
        "MAX_MEMORY_NUMBER": 50,
        "UPLOAD_MEMORY_TO_AI": True,
        "ACCEPT_ALL_GROUP_CHAT_MESSAGES": False,
        "ENABLE_GROUP_AT_REPLY": True,
        "ENABLE_GROUP_KEYWORD_REPLY": False,
        "GROUP_KEYWORD_LIST": ['你好', '机器人', '在吗'],
        "GROUP_CHAT_RESPONSE_PROBABILITY": 100,
        "GROUP_KEYWORD_REPLY_IGNORE_PROBABILITY": True,
        "ENABLE_LOGIN_PASSWORD": False,
        "LOGIN_PASSWORD": '',
        "PORT": 5000,
        "ENABLE_REMINDERS": True,
        "ALLOW_REMINDERS_IN_QUIET_TIME": True,
        "USE_VOICE_CALL_FOR_REMINDERS": False,
        "ENABLE_AUTO_DECLINE_CALLS": True,
        "AUTO_DECLINE_CALL_CHECK_INTERVAL": 3.0,
        "AUTO_DECLINE_CALL_REPLY": True,
        "ENABLE_ONLINE_API": False,
        "ONLINE_BASE_URL": 'https://ai.qiqiapi.top/v1',
        "ONLINE_MODEL": 'grok-4.5',
        "ONLINE_API_KEY": '',
        "ONLINE_API_TEMPERATURE": 0.7,
        "ONLINE_API_MAX_TOKEN": 2000,
        "SEARCH_DETECTION_PROMPT": '是否需要查询今天的天气、最新的新闻事件、特定网站的内容、股票价格、特定人物的最新动态等',
        "ONLINE_FIXED_PROMPT": '',
        "ENABLE_URL_FETCHING": True,
        "REQUESTS_TIMEOUT": 10,
        "REQUESTS_USER_AGENT": 'Mozilla/5.0 (Linux; Android 10; SM-G975F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Mobile Safari/537.36',
        "MAX_WEB_CONTENT_LENGTH": 2000,
        "ENABLE_SCHEDULED_RESTART": False,
        "RESTART_INTERVAL_HOURS": 12.0,
        "RESTART_INACTIVITY_MINUTES": 10,
        "ENABLE_SELF_MESSAGE_CONTEXT": True,
        "ENABLE_RECALL_DETECTION": True,
        "REPLY_AFTER_RECALL": False,
        "MANUAL_SELF_REPLY_CANCELS_PENDING": True,
        "REMOVE_PARENTHESES": False,
        "ENABLE_ASSISTANT_MODEL": False,
        "ASSISTANT_BASE_URL": 'https://ai.qiqiapi.top/v1',
        "ASSISTANT_MODEL": 'gpt-5.4-mini',
        "ASSISTANT_API_KEY": '',
        "ASSISTANT_TEMPERATURE": 0.3,
        "ASSISTANT_MAX_TOKEN": 1000,
        "USE_ASSISTANT_FOR_MEMORY_SUMMARY": False,
        "IGNORE_GROUP_CHAT_FOR_AUTO_MESSAGE": False,
        "ENABLE_SENSITIVE_CONTENT_CLEARING": True,
        "SAVE_MEMORY_TO_SEPARATE_FILE": True,
        "CORE_MEMORY_DIR": 'CoreMemory',
        "ENABLE_TEXT_COMMANDS": True,
        "ENABLE_FORUM_CUSTOM_MODEL": False,
        "FORUM_BASE_URL": 'https://ai.qiqiapi.top/v1',
        "FORUM_MODEL": 'gpt-5.6-terra',
        "FORUM_API_KEY": '',
        "FORUM_TEMPERATURE": 1.0,
        "FORUM_MAX_TOKEN": 1200
    }

def validate_config():
    """验证config.py配置完整性，若有缺失项则自动补充默认值"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, 'config.py')
    
    try:
        # 如果配置文件不存在，直接创建完整配置
        if not os.path.exists(config_path):
            print(f"配置文件不存在，正在创建新配置文件: {config_path}")
            with open(config_path, 'w', encoding='utf-8') as f:
                f.write("# -*- coding: utf-8 -*-\n\n")
                f.write("# 自动生成的配置文件\n\n")
                
                for key, value in get_default_config().items():
                    f.write(f"{key} = {format_config_value(value)}\n")
            print("已创建新的配置文件")
            return True
        
        # 尝试解析当前配置
        current_config = parse_config()
        default_config = get_default_config()
        
        # 记录缺少的配置项
        missing_keys = []
        # 构建需要更新的配置字典
        updates_needed = {}
        
        # 检查每个默认配置项是否存在
        for key, default_value in default_config.items():
            if key not in current_config:
                missing_keys.append(key)
                updates_needed[key] = default_value
        
        # 如果存在缺失项，更新配置文件
        if missing_keys:
            print(f"检测到{len(missing_keys)}个缺失的配置项: {', '.join(missing_keys)}")
            print("正在自动补充默认值...")
            
            # 直接修改文件，添加缺失的配置项
            with open(config_path, 'a', encoding='utf-8') as f:
                f.write("\n# 自动补充的配置项\n")
                for key in missing_keys:
                    f.write(f"{key} = {format_config_value(default_config[key])}\n")
            
            print("配置文件已更新完成")
            return True  # 配置已更新
        
        print("配置文件验证完成，所有配置项齐全")
        return False  # 配置无需更新
        
    except Exception as e:
        print(f"验证配置文件时出错: {str(e)}")
        return False
def delete_forum_post_by_id(character_name, post_id):
    """根据ID删除角色的论坛帖子"""
    try:
        forum_file = _forum_file_path(character_name)
        
        if not os.path.exists(forum_file):
            app.logger.warning(f"论坛数据文件不存在: {forum_file}")
            return False
        
        # 读取现有数据
        with open(forum_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        posts = data.get('posts', [])
        
        # 查找并删除指定ID的帖子
        original_count = len(posts)
        posts = [post for post in posts if post.get('id') != post_id]
        
        if len(posts) == original_count:
            app.logger.warning(f"未找到要删除的帖子: {post_id}")
            return False
        
        # 更新数据
        data['posts'] = posts
        
        # 保存数据
        with open(forum_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        app.logger.info(f"成功删除帖子 {post_id}，剩余帖子数量: {len(posts)}")
        return True
        
    except Exception as e:
        app.logger.error(f"删除论坛帖子失败: {e}")
        return False

def kill_process_using_port(port):
    """
    检查指定端口是否被占用，如果被占用则结束占用的进程
    """
    # 遍历所有连接
    for conn in psutil.net_connections():
        # 由于 config 中 PORT 可能为字符串，转换为 int
        if conn.laddr and conn.laddr.port == port:
            # 根据不同平台，监听状态可能不同（Linux一般为 'LISTEN'，Windows为 'LISTENING'）
            if conn.status in ('LISTEN', 'LISTENING'):
                try:
                    proc = psutil.Process(conn.pid)
                    print(f"检测到端口 {port} 被进程 {conn.pid} 占用，尝试结束该进程……")
                    proc.kill()
                    proc.wait(timeout=3)
                    print(f"进程 {conn.pid} 已被成功结束。")
                except Exception as e:
                    print(f"结束进程 {conn.pid} 时出现异常：{e}")

if __name__ == '__main__':
    # 配置应用日志级别
    app.logger.setLevel(logging.INFO)
    
    # 添加控制台处理器确保论坛相关日志显示
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(formatter)
    app.logger.addHandler(console_handler)
    
    class BotStatusFilter(logging.Filter):
        def filter(self, record):
            msg = record.getMessage()
            # 允许论坛相关的日志通过
            if '/forum/' in msg or 'forum' in msg.lower():
                return True
            # 如果日志消息中包含以下日志，则返回 False（不记录）
            if '/bot_status' in msg or \
               '/api/log' in msg or \
               '/save_all_reminders' in msg or \
               '/get_all_reminders' in msg or \
               '/api/get_chat_context_users' in msg or \
               '/bot_heartbeat' in msg:
                return False
            return True

    # 获取 werkzeug 的日志记录器并添加过滤器
    werkzeug_logger = logging.getLogger('werkzeug')
    werkzeug_logger.addFilter(BotStatusFilter())

    # 验证配置文件完整性
    validate_config()

    # 配置文件存在检查
    config_path = os.path.join(os.path.dirname(__file__), 'config.py')
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"核心配置文件缺失: {config_path}")
    
    config = parse_config()
    PORT = config.get('PORT', '5000')

    # 在启动服务器前检查端口是否被占用，若占用则结束该进程
    kill_process_using_port(PORT)

    print("\033[32m========================================\033[0m")
    print(f"\033[32m{APP_DISPLAY_NAME} v{APP_VERSION} 配置编辑器启动成功！\033[0m")
    print("\033[32m========================================\033[0m")
    print("")
    print(f"\033[36m访问地址: http://localhost:{config.get('PORT', '5000')}/\033[0m")
    print("\033[90m   浏览器将自动打开，如未打开请手动访问上述地址\033[0m")
    if config.get('ENABLE_LOGIN_PASSWORD', False):
        print("")
        print(f"\033[33m登录密码: {config.get('LOGIN_PASSWORD', '未设置')}\033[0m")
        print("\033[90m   请妥善保管，勿泄露给他人\033[0m")
    print("")
    print("\033[33m常见问题：\033[0m")
    print("\033[90m   问题1: 点击Start Bot没反应，提示微信窗口找不到\033[0m")
    print("\033[90m        解决: 重启一次微信\033[0m")
    print("")
    print("\033[90m   问题2: 导入配置后出现404心跳报错日志\033[0m")
    print("\033[90m        解决: 重启一次Run.bat\033[0m")
    print("")
    print("\033[32m========================================\033[0m")
    
    # 在启动服务器前设置定时器打开浏览器
    def open_browser():
        webbrowser.open(f'http://localhost:{PORT}/')
    
    Timer(1, open_browser).start()  # 延迟1秒确保服务器已启动
    
    # 根据配置决定绑定地址
    #host = "0.0.0.0" if config.get('ENABLE_LOGIN_PASSWORD', False) else "127.0.0.1"
    app.run(host= "127.0.0.1", debug=False, port=PORT)
    
