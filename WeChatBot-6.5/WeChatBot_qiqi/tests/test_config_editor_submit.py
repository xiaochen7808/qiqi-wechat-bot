from pathlib import Path
import copy
import json
import sys
import tempfile
import unittest
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config_editor


MODEL_ENDPOINT_FIELDS = {
    "chat": ("DEEPSEEK_BASE_URL", "DEEPSEEK_API_KEY", "MODEL"),
    "assistant": ("ASSISTANT_BASE_URL", "ASSISTANT_API_KEY", "ASSISTANT_MODEL"),
    "image": ("MOONSHOT_BASE_URL", "MOONSHOT_API_KEY", "MOONSHOT_MODEL"),
    "online": ("ONLINE_BASE_URL", "ONLINE_API_KEY", "ONLINE_MODEL"),
    "forum": ("FORUM_BASE_URL", "FORUM_API_KEY", "FORUM_MODEL"),
    "adult": ("ADULT_BASE_URL", "ADULT_API_KEY", "ADULT_MODEL"),
}


class StartBotProcessTests(unittest.TestCase):
    def test_start_bot_uses_current_python_project_dir_and_integrity_environment(self):
        bot_path = str(PROJECT_ROOT / "bot.py")

        def fake_exists(path):
            return path == bot_path

        with mock.patch.object(config_editor, "bot_process", None), \
                mock.patch.object(config_editor.os.path, "exists", side_effect=fake_exists), \
                mock.patch.object(config_editor.subprocess, "Popen") as popen, \
                mock.patch.dict(config_editor.os.environ, {"QIQI_TEST_ENV": "preserved"}):
            response = config_editor.start_bot()

        self.assertEqual(response, ({"status": "started"}, 200))
        popen.assert_called_once()
        args, kwargs = popen.call_args
        self.assertEqual(args[0], [config_editor.sys.executable, bot_path])
        self.assertEqual(kwargs["cwd"], str(PROJECT_ROOT))
        self.assertEqual(
            kwargs["env"]["PROJECT_NAME"],
            "iwyxdxl/WeChatBot_WXAUTO_SE",
        )
        self.assertEqual(kwargs["env"]["QIQI_TEST_ENV"], "preserved")
        self.assertIsNot(kwargs["env"], config_editor.os.environ)


class SubmitConfigApiKeyTests(unittest.TestCase):
    def test_main_model_timeout_is_saved_as_integer_for_legacy_config(self):
        current_config = {
            "ENABLE_LOGIN_PASSWORD": False,
            "LISTEN_LIST": [],
            "MODEL": "gpt-5.6-terra",
        }
        with mock.patch.object(config_editor, "bot_process", None), \
                mock.patch.object(
                    config_editor, "parse_config", return_value=current_config
                ), \
                mock.patch.object(config_editor, "validate_config_types"), \
                mock.patch.object(config_editor, "update_config") as update_config:
            response = config_editor.app.test_client().post(
                "/submit_config",
                data={"MODEL_REQUEST_TIMEOUT_SECONDS": "240"},
            )

        self.assertEqual(response.status_code, 204)
        saved_timeout = update_config.call_args.args[0][
            "MODEL_REQUEST_TIMEOUT_SECONDS"
        ]
        self.assertEqual(saved_timeout, 240)
        self.assertIsInstance(saved_timeout, int)

    def test_keyword_list_save_uses_module_level_re(self):
        current_config = {
            "ENABLE_LOGIN_PASSWORD": False,
            "LISTEN_LIST": [],
            "GROUP_KEYWORD_LIST": [],
        }
        with mock.patch.object(config_editor, "bot_process", None), \
                mock.patch.object(
                    config_editor, "parse_config", return_value=current_config
                ), \
                mock.patch.object(config_editor, "validate_config_types"), \
                mock.patch.object(config_editor, "update_config") as update_config:
            response = config_editor.app.test_client().post(
                "/submit_config",
                data={"GROUP_KEYWORD_LIST": "关键词一， 关键词二\n关键词三"},
            )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            update_config.call_args.args[0]["GROUP_KEYWORD_LIST"],
            ["关键词一", "关键词二", "关键词三"],
        )

    def test_legacy_default_prompt_is_normalized_and_presets_have_stable_order(self):
        normalized = config_editor.normalize_prompt_config_values({
            "LISTEN_LIST": [["alice", "角色1"], ["bob", "自定义角色"]],
            "DEFAULT_PROMPT_FILE": "角色1",
        })

        self.assertEqual(normalized["LISTEN_LIST"][0], ["alice", "默认角色"])
        self.assertEqual(normalized["LISTEN_LIST"][1], ["bob", "自定义角色"])
        self.assertEqual(normalized["DEFAULT_PROMPT_FILE"], "默认角色")
        self.assertEqual(
            sorted(
                ["角色2", "男生02_温柔年上", "默认角色", "女生01_高冷御姐", "自定义角色"],
                key=config_editor.prompt_file_sort_key,
            ),
            ["默认角色", "女生01_高冷御姐", "男生02_温柔年上", "角色2", "自定义角色"],
        )

    def test_favorability_table_escapes_user_controlled_text(self):
        template = (PROJECT_ROOT / "templates" / "config_editor.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("${escapeHtml(record.username)}", template)
        self.assertIn("${escapeHtml(record.stage)}", template)
        self.assertIn("${escapeHtml(record.last_reason)}", template)
        self.assertIn('class="favor-save-button"', template)
        self.assertNotIn('onclick="updateFavorabilityScore(', template)

    def test_favorability_settings_preserve_half_point_values(self):
        current_config = {
            "ENABLE_LOGIN_PASSWORD": False,
            "LISTEN_LIST": [],
            "DEFAULT_FAVORABILITY_SCORE": 30.0,
            "FAVORABILITY_POSITIVE_CHANGE": 0.5,
            "FAVORABILITY_VERY_POSITIVE_CHANGE": 1.0,
            "FAVORABILITY_MILD_NEGATIVE_CHANGE": -0.5,
            "FAVORABILITY_STRONG_NEGATIVE_CHANGE": -1.0,
            "FAVORABILITY_DECAY_GRACE_DAYS": 3,
            "FAVORABILITY_DECAY_PER_DAY": 0.5,
            "FAVORABILITY_MAX_OFFLINE_DECAY": 10.0,
            "FAVORABILITY_DUPLICATE_WINDOW_MINUTES": 10,
        }
        submitted = {
            "DEFAULT_FAVORABILITY_SCORE": "30.5",
            "FAVORABILITY_POSITIVE_CHANGE": "0.5",
            "FAVORABILITY_VERY_POSITIVE_CHANGE": "1.0",
            "FAVORABILITY_MILD_NEGATIVE_CHANGE": "-0.5",
            "FAVORABILITY_STRONG_NEGATIVE_CHANGE": "-1.0",
            "FAVORABILITY_DECAY_GRACE_DAYS": "4",
            "FAVORABILITY_DECAY_PER_DAY": "0.5",
            "FAVORABILITY_MAX_OFFLINE_DECAY": "8.5",
            "FAVORABILITY_DUPLICATE_WINDOW_MINUTES": "15",
        }
        with mock.patch.object(config_editor, "bot_process", None), \
                mock.patch.object(config_editor, "parse_config", return_value=current_config), \
                mock.patch.object(config_editor, "validate_config_types"), \
                mock.patch.object(config_editor, "update_config") as update_config:
            response = config_editor.app.test_client().post(
                "/submit_config", data=submitted
            )

        self.assertEqual(response.status_code, 204)
        saved = update_config.call_args.args[0]
        self.assertEqual(saved["DEFAULT_FAVORABILITY_SCORE"], 30.5)
        self.assertEqual(saved["FAVORABILITY_MILD_NEGATIVE_CHANGE"], -0.5)
        self.assertEqual(saved["FAVORABILITY_DECAY_PER_DAY"], 0.5)
        self.assertEqual(saved["FAVORABILITY_MAX_OFFLINE_DECAY"], 8.5)
        self.assertEqual(saved["FAVORABILITY_DECAY_GRACE_DAYS"], 4)
        self.assertEqual(saved["FAVORABILITY_DUPLICATE_WINDOW_MINUTES"], 15)

    def test_favorability_api_round_trips_float_score(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            favorability_file = Path(temp_dir) / "favorability.json"
            favorability_file.write_text(
                json.dumps({"alice": {"score": 59, "message_count": 2}}),
                encoding="utf-8",
            )
            lock = config_editor.FileLock(str(favorability_file) + ".lock")
            with mock.patch.object(config_editor, "FAVORABILITY_FILE", str(favorability_file)), \
                    mock.patch.object(config_editor, "favorability_file_lock", lock), \
                    mock.patch.object(
                        config_editor,
                        "parse_config",
                        return_value={"ENABLE_LOGIN_PASSWORD": False},
                    ):
                response = config_editor.app.test_client().post(
                    "/api/favorability/update",
                    json={"username": "alice", "score": 59.5},
                )

            self.assertEqual(response.status_code, 200)
            record = response.get_json()["record"]
            self.assertEqual(record["score"], 59.5)
            self.assertEqual(record["last_change"], 0.5)
            stored = json.loads(favorability_file.read_text(encoding="utf-8"))
            self.assertEqual(stored["alice"]["score"], 59.5)

    def test_favorability_api_does_not_overwrite_corrupt_storage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            favorability_file = Path(temp_dir) / "favorability.json"
            favorability_file.write_text("{invalid", encoding="utf-8")
            lock = config_editor.FileLock(str(favorability_file) + ".lock")
            with mock.patch.object(config_editor, "FAVORABILITY_FILE", str(favorability_file)), \
                    mock.patch.object(config_editor, "favorability_file_lock", lock), \
                    mock.patch.object(
                        config_editor,
                        "parse_config",
                        return_value={"ENABLE_LOGIN_PASSWORD": False},
                    ):
                response = config_editor.app.test_client().post(
                    "/api/favorability/update",
                    json={"username": "alice", "score": 59.5},
                )

            self.assertEqual(response.status_code, 500)
            self.assertEqual(favorability_file.read_text(encoding="utf-8"), "{invalid")

    def test_adult_intimacy_settings_keep_boolean_and_integer_types(self):
        current_config = {
            "ENABLE_LOGIN_PASSWORD": False,
            "LISTEN_LIST": [],
            "ENABLE_ADULT_INTIMACY": False,
            "ADULT_INTIMACY_MIN_SCORE": 80,
            "ADULT_INTIMACY_PROACTIVE_SCORE": 90,
            "ADULT_INTIMACY_MEDIUM_SIGNALS_REQUIRED": 2,
            "ADULT_INTIMACY_SESSION_TIMEOUT_HOURS": 12,
            "ENABLE_ADULT_MODEL": False,
            "ADULT_BASE_URL": config_editor.QIQI_API_BASE_URL,
            "ADULT_API_KEY": "test-dedicated-adult",
            "ADULT_FALLBACK_TO_MAIN": True,
            "ADULT_TEMPERATURE": 0.7,
            "ADULT_MAX_TOKEN": 2000,
            "ADULT_TIMEOUT_SECONDS": 45,
        }
        with mock.patch.object(config_editor, "bot_process", None), \
                mock.patch.object(
                    config_editor, "parse_config", return_value=current_config
                ), \
                mock.patch.object(config_editor, "validate_config_types"), \
                mock.patch.object(config_editor, "update_config") as update_config:
            response = config_editor.app.test_client().post(
                "/submit_config",
                data={
                    "ENABLE_ADULT_INTIMACY": "on",
                    "ADULT_INTIMACY_MIN_SCORE": "80",
                    "ADULT_INTIMACY_PROACTIVE_SCORE": "90",
                    "ADULT_INTIMACY_MEDIUM_SIGNALS_REQUIRED": "2",
                    "ADULT_INTIMACY_SESSION_TIMEOUT_HOURS": "12",
                    "ENABLE_ADULT_MODEL": "on",
                    "ADULT_FALLBACK_TO_MAIN": "on",
                    "ADULT_TEMPERATURE": "0.8",
                    "ADULT_MAX_TOKEN": "2400",
                    "ADULT_TIMEOUT_SECONDS": "50",
                },
            )

        self.assertEqual(response.status_code, 204)
        saved = update_config.call_args.args[0]
        self.assertIs(saved["ENABLE_ADULT_INTIMACY"], True)
        self.assertEqual(saved["ADULT_INTIMACY_MIN_SCORE"], 80)
        self.assertEqual(saved["ADULT_INTIMACY_PROACTIVE_SCORE"], 90)
        self.assertEqual(saved["ADULT_INTIMACY_MEDIUM_SIGNALS_REQUIRED"], 2)
        self.assertEqual(saved["ADULT_INTIMACY_SESSION_TIMEOUT_HOURS"], 12)
        self.assertIs(saved["ENABLE_ADULT_MODEL"], True)
        self.assertIs(saved["ADULT_FALLBACK_TO_MAIN"], True)
        self.assertEqual(saved["ADULT_TEMPERATURE"], 0.8)
        self.assertEqual(saved["ADULT_MAX_TOKEN"], 2400)
        self.assertEqual(saved["ADULT_TIMEOUT_SECONDS"], 50)

    def test_enabling_adult_model_requires_dedicated_api_key(self):
        current_config = {
            "ENABLE_LOGIN_PASSWORD": False,
            "LISTEN_LIST": [],
            "ENABLE_ADULT_MODEL": False,
            "ADULT_BASE_URL": config_editor.QIQI_API_BASE_URL,
            "ADULT_API_KEY": "",
            "ADULT_MODEL": "grok-4.5",
        }
        with mock.patch.object(config_editor, "bot_process", None), \
                mock.patch.object(
                    config_editor, "parse_config", return_value=current_config
                ), \
                mock.patch.object(config_editor, "update_config") as update_config:
            response = config_editor.app.test_client().post(
                "/submit_config",
                data={
                    "ENABLE_ADULT_MODEL": "on",
                    "ADULT_BASE_URL": config_editor.QIQI_API_BASE_URL,
                    "ADULT_API_KEY": "",
                    "ADULT_MODEL": "grok-4.5",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("成人模型专用 API Key", response.get_json()["error"])
        update_config.assert_not_called()

    def test_masked_key_cannot_move_to_qiqi_for_any_model_endpoint(self):
        for api_key_field, base_url_field in config_editor.API_KEY_BASE_URL_FIELDS.items():
            with self.subTest(api_key_field=api_key_field):
                current_config = {
                    "ENABLE_LOGIN_PASSWORD": False,
                    api_key_field: "test-old-provider-secret",
                    base_url_field: "https://old-provider.example/v1",
                }
                submitted_form = {
                    api_key_field: config_editor.hide_api_key(
                        current_config[api_key_field]
                    ),
                    base_url_field: config_editor.QIQI_API_BASE_URL,
                }

                with mock.patch.object(config_editor, "bot_process", None), \
                        mock.patch.object(
                            config_editor, "parse_config", return_value=current_config
                        ), \
                        mock.patch.object(config_editor, "update_config") as update_config:
                    response = config_editor.app.test_client().post(
                        "/submit_config",
                        data=submitted_form,
                    )

                self.assertEqual(response.status_code, 400)
                self.assertIn("API Key", response.get_json()["error"])
                update_config.assert_not_called()

    def test_masked_key_is_reused_when_its_base_url_is_unchanged(self):
        secret = "test-qiqi-existing-secret"
        current_config = {
            "ENABLE_LOGIN_PASSWORD": False,
            "DEEPSEEK_API_KEY": secret,
            "DEEPSEEK_BASE_URL": config_editor.QIQI_API_BASE_URL,
        }
        submitted_form = {
            "DEEPSEEK_API_KEY": config_editor.hide_api_key(secret),
            "DEEPSEEK_BASE_URL": config_editor.QIQI_API_BASE_URL + "/",
        }

        with mock.patch.object(config_editor, "bot_process", None), \
                mock.patch.object(
                    config_editor, "parse_config", return_value=current_config
                ), \
                mock.patch.object(config_editor, "validate_config_types"), \
                mock.patch.object(config_editor, "update_config") as update_config:
            response = config_editor.app.test_client().post(
                "/submit_config",
                data=submitted_form,
            )

        self.assertEqual(response.status_code, 204)
        saved_values = update_config.call_args.args[0]
        self.assertEqual(saved_values["DEEPSEEK_API_KEY"], secret)
        self.assertEqual(
            saved_values["DEEPSEEK_BASE_URL"],
            config_editor.QIQI_API_BASE_URL,
        )

    def test_endpoint_change_cannot_omit_new_key(self):
        current_config = {
            "ENABLE_LOGIN_PASSWORD": False,
            "DEEPSEEK_API_KEY": "test-old-provider-secret",
            "DEEPSEEK_BASE_URL": "https://old-provider.example/v1",
        }
        with mock.patch.object(config_editor, "bot_process", None), \
                mock.patch.object(
                    config_editor, "parse_config", return_value=current_config
                ), \
                mock.patch.object(config_editor, "update_config") as update_config:
            response = config_editor.app.test_client().post(
                "/submit_config",
                data={"DEEPSEEK_BASE_URL": config_editor.QIQI_API_BASE_URL},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("API Key", response.get_json()["error"])
        update_config.assert_not_called()

    def test_each_model_endpoint_accepts_custom_https_url_with_new_key(self):
        for category, (base_url_field, api_key_field, model_field) in (
                MODEL_ENDPOINT_FIELDS.items()):
            with self.subTest(category=category):
                current_config = {
                    "ENABLE_LOGIN_PASSWORD": False,
                    "LISTEN_LIST": [],
                    base_url_field: config_editor.QIQI_API_BASE_URL,
                    api_key_field: "test-old-qiqi-key",
                    model_field: "old-model",
                }
                submitted_url = f"https://custom-{category}.example/openai/v1/"
                submitted_key = f"test-new-{category}-key"
                submitted_model = f"custom-{category}-model"

                with mock.patch.object(config_editor, "bot_process", None), \
                        mock.patch.object(
                            config_editor, "parse_config", return_value=current_config
                        ), \
                        mock.patch.object(config_editor, "validate_config_types"), \
                        mock.patch.object(config_editor, "update_config") as update_config:
                    response = config_editor.app.test_client().post(
                        "/submit_config",
                        data={
                            base_url_field: submitted_url,
                            api_key_field: submitted_key,
                            model_field: submitted_model,
                        },
                    )

                self.assertEqual(response.status_code, 204)
                saved_values = update_config.call_args.args[0]
                self.assertEqual(saved_values[base_url_field], submitted_url.rstrip("/"))
                self.assertEqual(saved_values[api_key_field], submitted_key)
                self.assertEqual(saved_values[model_field], submitted_model)

    def test_main_chat_accepts_siliconflow_with_new_key(self):
        current_config = {
            "ENABLE_LOGIN_PASSWORD": False,
            "LISTEN_LIST": [],
            "DEEPSEEK_BASE_URL": config_editor.QIQI_API_BASE_URL,
            "DEEPSEEK_API_KEY": "test-old-qiqi-key",
            "MODEL": "gpt-5.6-terra",
        }
        submitted_key = "test-new-siliconflow-key"
        submitted_model = "deepseek-ai/DeepSeek-V3.2"

        with mock.patch.object(config_editor, "bot_process", None), \
                mock.patch.object(
                    config_editor, "parse_config", return_value=current_config
                ), \
                mock.patch.object(config_editor, "validate_config_types"), \
                mock.patch.object(config_editor, "update_config") as update_config:
            response = config_editor.app.test_client().post(
                "/submit_config",
                data={
                    "DEEPSEEK_BASE_URL": config_editor.SILICONFLOW_API_BASE_URL,
                    "DEEPSEEK_API_KEY": submitted_key,
                    "MODEL": submitted_model,
                },
            )

        self.assertEqual(response.status_code, 204)
        saved_values = update_config.call_args.args[0]
        self.assertEqual(
            saved_values["DEEPSEEK_BASE_URL"],
            config_editor.SILICONFLOW_API_BASE_URL,
        )
        self.assertEqual(saved_values["DEEPSEEK_API_KEY"], submitted_key)
        self.assertEqual(saved_values["MODEL"], submitted_model)

    def test_invalid_api_base_urls_are_rejected(self):
        invalid_urls = {
            "scheme": "ftp://api.example.com/v1",
            "missing-hostname": "https:///v1",
            "credentials": "https://user:REDACTED@api.example.com/v1",
            "query": "https://api.example.com/v1?tenant=test",
            "fragment": "https://api.example.com/v1#models",
        }
        current_config = {
            "ENABLE_LOGIN_PASSWORD": False,
            "DEEPSEEK_BASE_URL": config_editor.QIQI_API_BASE_URL,
            "DEEPSEEK_API_KEY": "test-current-key",
        }

        for case, invalid_url in invalid_urls.items():
            with self.subTest(case=case):
                with mock.patch.object(config_editor, "bot_process", None), \
                        mock.patch.object(
                            config_editor, "parse_config", return_value=current_config
                        ), \
                        mock.patch.object(config_editor, "update_config") as update_config:
                    response = config_editor.app.test_client().post(
                        "/submit_config",
                        data={
                            "DEEPSEEK_BASE_URL": invalid_url,
                            "DEEPSEEK_API_KEY": "test-new-key",
                        },
                    )

                self.assertEqual(response.status_code, 400)
                self.assertIn("DEEPSEEK_BASE_URL", response.get_json()["error"])
                update_config.assert_not_called()

    def test_quick_start_rejects_non_qiqi_provider(self):
        current_config = {
            "ENABLE_LOGIN_PASSWORD": False,
            "DEEPSEEK_API_KEY": "",
            "DEEPSEEK_BASE_URL": config_editor.QIQI_API_BASE_URL,
        }
        with mock.patch.object(
                config_editor, "parse_config", return_value=current_config
            ), mock.patch.object(config_editor, "update_config") as update_config:
            response = config_editor.app.test_client().post(
                "/quick_start",
                data={
                    "quick_start_api_provider": "siliconflow",
                    "quick_start_api_key": "test-siliconflow-key",
                },
            )

        self.assertEqual(response.status_code, 400)
        update_config.assert_not_called()


class UserInfoAdultModeTests(unittest.TestCase):
    def test_legacy_record_defaults_closed_and_normalizes_switches(self):
        legacy = config_editor.normalize_user_info_record({"age": "17"})
        self.assertIs(legacy["is_minor"], False)
        self.assertIs(legacy["adult_mode_enabled"], False)

        enabled = config_editor.normalize_user_info_record({
            "is_minor": False,
            "adult_mode_enabled": "true",
        })
        self.assertIs(enabled["adult_mode_enabled"], True)

        minor = config_editor.normalize_user_info_record({
            "is_minor": True,
            "adult_mode_enabled": True,
        })
        self.assertIs(minor["is_minor"], True)
        self.assertIs(minor["adult_mode_enabled"], False)

    def test_user_info_api_persists_switch_and_minor_forces_it_off(self):
        store = {
            "alice": {
                "name": "Alice",
                "age": "17",
                "is_minor": False,
                "adult_mode_enabled": False,
            }
        }

        def load_store(*_args, **_kwargs):
            return copy.deepcopy(store)

        def save_store(data):
            store.clear()
            store.update(copy.deepcopy(data))

        with mock.patch.object(
            config_editor, "parse_config", return_value={"ENABLE_LOGIN_PASSWORD": False}
        ), mock.patch.object(
            config_editor, "load_user_info_file", side_effect=load_store
        ), mock.patch.object(
            config_editor, "save_user_info_file", side_effect=save_store
        ), mock.patch.object(
            config_editor, "user_info_file_lock", mock.MagicMock()
        ):
            client = config_editor.app.test_client()

            enabled = client.post(
                "/api/user_info/update",
                json={"username": "alice", "adult_mode_enabled": True},
            )
            self.assertEqual(enabled.status_code, 200)
            self.assertIs(store["alice"]["adult_mode_enabled"], True)

            marked = client.post(
                "/api/user_info/update",
                json={"username": "alice", "is_minor": True},
            )
            self.assertEqual(marked.status_code, 200)
            self.assertIs(store["alice"]["is_minor"], True)
            self.assertIs(store["alice"]["adult_mode_enabled"], False)

            rejected = client.post(
                "/api/user_info/update",
                json={"username": "alice", "adult_mode_enabled": True},
            )
            self.assertEqual(rejected.status_code, 400)
            self.assertIn("未成年人不能开启成人模式", rejected.get_json()["message"])
            self.assertIs(store["alice"]["adult_mode_enabled"], False)

            preserved = client.post(
                "/api/user_info/update",
                json={"username": "alice", "name": "Alice 2"},
            )
            self.assertEqual(preserved.status_code, 200)
            self.assertIs(store["alice"]["is_minor"], True)

            cleared = client.post(
                "/api/user_info/update",
                json={"username": "alice", "is_minor": False},
            )
            self.assertEqual(cleared.status_code, 200)
            self.assertIs(store["alice"]["is_minor"], False)

            invalid = client.post(
                "/api/user_info/update",
                json={"username": "alice", "is_minor": "true"},
            )
            self.assertEqual(invalid.status_code, 400)
            self.assertIs(store["alice"]["is_minor"], False)

            invalid_adult_mode = client.post(
                "/api/user_info/update",
                json={"username": "alice", "adult_mode_enabled": "true"},
            )
            self.assertEqual(invalid_adult_mode.status_code, 400)
            self.assertIs(store["alice"]["adult_mode_enabled"], False)

    def test_user_info_records_include_listen_users_with_closed_default(self):
        stored = {
            "alice": {
                "name": "Alice",
                "adult_mode_enabled": True,
                "is_minor": False,
            }
        }
        with mock.patch.object(
            config_editor, "load_user_info_file", return_value=copy.deepcopy(stored)
        ), mock.patch.object(
            config_editor,
            "parse_config",
            return_value={"LISTEN_LIST": [["alice", "默认角色"], ["bob", "默认角色"]]},
        ):
            records = {
                record["username"]: record
                for record in config_editor.get_user_info_records()
            }

        self.assertIs(records["alice"]["adult_mode_enabled"], True)
        self.assertIs(records["bob"]["adult_mode_enabled"], False)

    def test_user_info_update_does_not_overwrite_an_unreadable_store(self):
        with mock.patch.object(
            config_editor, "parse_config", return_value={"ENABLE_LOGIN_PASSWORD": False}
        ), mock.patch.object(
            config_editor, "load_user_info_file", side_effect=ValueError("broken json")
        ), mock.patch.object(
            config_editor, "save_user_info_file"
        ) as save_store, mock.patch.object(
            config_editor, "user_info_file_lock", mock.MagicMock()
        ):
            response = config_editor.app.test_client().post(
                "/api/user_info/update",
                json={"username": "alice", "is_minor": True},
            )

        self.assertEqual(response.status_code, 500)
        save_store.assert_not_called()

    def test_user_info_file_uses_atomic_replace_and_strict_reads(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            user_info_path = Path(temp_dir) / "user_info.json"
            file_lock = config_editor.FileLock(str(user_info_path) + ".lock")
            with mock.patch.object(
                config_editor, "USER_INFO_FILE", str(user_info_path)
            ), mock.patch.object(
                config_editor, "user_info_file_lock", file_lock
            ):
                config_editor.save_user_info_file({
                    "alice": {"name": "Alice", "is_minor": True}
                })
                loaded = config_editor.load_user_info_file(raise_on_error=True)
                self.assertIs(loaded["alice"]["is_minor"], True)
                self.assertEqual(list(Path(temp_dir).glob("*.tmp")), [])

                user_info_path.write_text("{broken", encoding="utf-8")
                with self.assertRaises(ValueError):
                    config_editor.load_user_info_file(raise_on_error=True)


class AdultIntimacyToggleTests(unittest.TestCase):
    def test_toggle_endpoint_updates_only_the_runtime_adult_switch(self):
        with mock.patch.object(
            config_editor, "parse_config", return_value={"ENABLE_LOGIN_PASSWORD": False}
        ), mock.patch.object(config_editor, "update_config") as update_config:
            client = config_editor.app.test_client()
            enabled = client.post(
                "/api/adult_intimacy/toggle",
                json={"enabled": True},
            )
            invalid = client.post(
                "/api/adult_intimacy/toggle",
                json={"enabled": "true"},
            )

        self.assertEqual(enabled.status_code, 200)
        self.assertIs(enabled.get_json()["enabled"], True)
        self.assertEqual(invalid.status_code, 400)
        update_config.assert_called_once_with({"ENABLE_ADULT_INTIMACY": True})


class AdultModelConnectionTests(unittest.TestCase):
    def test_connection_test_ignores_reuse_request_and_uses_adult_credentials(self):
        adult_base_url = "https://adult.example/v1"
        config = {
            "ENABLE_LOGIN_PASSWORD": False,
            "DEEPSEEK_BASE_URL": config_editor.QIQI_API_BASE_URL,
            "DEEPSEEK_API_KEY": "test-main-secret",
            "ADULT_BASE_URL": adult_base_url,
            "ADULT_API_KEY": "test-adult-secret",
            "ADULT_MODEL": "grok-4.5",
            "ADULT_TIMEOUT_SECONDS": 45,
        }
        choice = mock.Mock()
        choice.message.content = "OK"
        openai_client = mock.Mock()
        openai_client.chat.completions.create.return_value = mock.Mock(
            choices=[choice]
        )

        with mock.patch.object(config_editor, "parse_config", return_value=config), \
                mock.patch.object(
                    config_editor.openai, "OpenAI", return_value=openai_client
                ) as openai_factory:
            response = config_editor.app.test_client().post(
                "/api/adult_model/test",
                json={
                    "reuse_main": True,
                    "model": "grok-4.5",
                    "timeout_seconds": 45,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "success")
        openai_factory.assert_called_once_with(
            api_key="test-adult-secret",
            base_url=adult_base_url,
            timeout=180.0,
            max_retries=0,
        )
        openai_client.chat.completions.create.assert_called_once()


class ConfigEditorPageTests(unittest.TestCase):
    def test_low_overhead_background_defaults_are_consistent(self):
        defaults = config_editor.get_default_config()
        config_source = (PROJECT_ROOT / "config.py").read_text(encoding="utf-8")
        template = (PROJECT_ROOT / "templates" / "config_editor.html").read_text(
            encoding="utf-8"
        )

        self.assertFalse(defaults["ENABLE_SCHEDULED_RESTART"])
        self.assertEqual(defaults["RESTART_INTERVAL_HOURS"], 12.0)
        self.assertEqual(defaults["RESTART_INACTIVITY_MINUTES"], 10)
        self.assertEqual(defaults["AUTO_DECLINE_CALL_CHECK_INTERVAL"], 3.0)
        self.assertIn("ENABLE_SCHEDULED_RESTART = False", config_source)
        self.assertIn("RESTART_INTERVAL_HOURS = 12.0", config_source)
        self.assertIn("RESTART_INACTIVITY_MINUTES = 10", config_source)
        self.assertIn("AUTO_DECLINE_CALL_CHECK_INTERVAL = 3.0", config_source)
        self.assertIn("AUTO_DECLINE_CALL_CHECK_INTERVAL|default(3.0)", template)
        self.assertIn("RESTART_INTERVAL_HOURS|default(12.0)", template)
        self.assertIn("RESTART_INACTIVITY_MINUTES|default(10)", template)
        self.assertIn("建议保持 3 秒", template)

    def test_unused_queue_waiting_time_setting_is_not_exposed_or_defaulted(self):
        template = (PROJECT_ROOT / "templates" / "config_editor.html").read_text(
            encoding="utf-8"
        )
        config_source = (PROJECT_ROOT / "config.py").read_text(encoding="utf-8")
        editor_source = (PROJECT_ROOT / "config_editor.py").read_text(encoding="utf-8")

        self.assertNotIn("QUEUE_WAITING_TIME", config_editor.get_default_config())
        self.assertNotIn('name="QUEUE_WAITING_TIME"', template)
        self.assertNotIn("消息队列等待时间", template)
        self.assertNotIn("QUEUE_WAITING_TIME", config_source)
        self.assertNotIn("QUEUE_WAITING_TIME", editor_source)

    @staticmethod
    def _select_html(html, select_id):
        marker = f'<select id="{select_id}"'
        return html.split(marker, 1)[1].split("</select>", 1)[0]

    def _render_editor(self):
        config = {
            "ENABLE_LOGIN_PASSWORD": False,
            "LISTEN_LIST": [],
            "DEEPSEEK_API_KEY": "test-test-key",
            "DEEPSEEK_BASE_URL": config_editor.QIQI_API_BASE_URL,
            "MODEL": "gpt-5.6-terra",
            "MODEL_REQUEST_TIMEOUT_SECONDS": 180,
            "ASSISTANT_BASE_URL": config_editor.QIQI_API_BASE_URL,
            "ASSISTANT_API_KEY": "",
            "ASSISTANT_MODEL": "gpt-5.4-mini",
            "MOONSHOT_BASE_URL": config_editor.QIQI_API_BASE_URL,
            "MOONSHOT_API_KEY": "",
            "MOONSHOT_MODEL": "gpt-5.4-mini",
            "ONLINE_BASE_URL": config_editor.QIQI_API_BASE_URL,
            "ONLINE_API_KEY": "",
            "ONLINE_MODEL": "grok-4.5",
            "FORUM_BASE_URL": config_editor.QIQI_API_BASE_URL,
            "FORUM_API_KEY": "",
            "FORUM_MODEL": "gpt-5.6-terra",
            "ENABLE_ADULT_INTIMACY": True,
            "ADULT_INTIMACY_MIN_SCORE": 80,
            "ADULT_INTIMACY_PROACTIVE_SCORE": 90,
            "ADULT_INTIMACY_MEDIUM_SIGNALS_REQUIRED": 2,
            "ADULT_INTIMACY_SESSION_TIMEOUT_HOURS": 12,
            "ENABLE_ADULT_MODEL": False,
            "ADULT_BASE_URL": config_editor.QIQI_API_BASE_URL,
            "ADULT_API_KEY": "",
            "ADULT_MODEL": "grok-4.5",
            "ADULT_TEMPERATURE": 0.7,
            "ADULT_MAX_TOKEN": 2000,
            "ADULT_TIMEOUT_SECONDS": 180,
            "ADULT_FALLBACK_TO_MAIN": True,
        }
        with mock.patch.object(config_editor, "parse_config", return_value=config), \
                mock.patch.object(
                    config_editor, "get_chat_context_users", return_value=[]
                ):
            return config_editor.app.test_client().get("/?skipped=true")

    def test_provider_selects_offer_expected_channels(self):
        response = self._render_editor()

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        main_select = self._select_html(html, "apiProvider")
        self.assertIn(config_editor.QIQI_API_BASE_URL, main_select)
        self.assertIn(config_editor.SILICONFLOW_API_BASE_URL, main_select)
        self.assertIn('value="other"', main_select)

        for select_id in (
            "assistantApiProvider",
            "imageApiProvider",
            "onlineApiProvider",
            "forumApiProvider",
            "adultApiProvider",
        ):
            with self.subTest(select_id=select_id):
                select_html = self._select_html(html, select_id)
                self.assertIn(config_editor.QIQI_API_BASE_URL, select_html)
                self.assertIn('value="other"', select_html)
                self.assertNotIn(config_editor.SILICONFLOW_API_BASE_URL, select_html)

    def test_page_contains_qq_contact_and_all_model_recommendations(self):
        response = self._render_editor()

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("QQ交流群：QQ_GROUP_ID_REMOVED", html)
        self.assertIn("群主QQ：GROUP_OWNER_ID_REMOVED", html)

        recommendations = {
            "chat": ("GPT-5.6 Sol", "GPT-5.6 Terra"),
            "assistant": ("GPT-5.6 Luna", "GPT-5.4 Mini"),
            "image": ("GPT-5.4", "GPT-5.4 Mini"),
            "online": ("Grok 4.5", "GPT-5.4 Mini"),
            "forum": ("GPT-5.6 Terra", "DeepSeek V4 Flash"),
            "adult": ("Grok 4.5", "GPT-5.6 Terra"),
        }
        for category, (effect_model, value_model) in recommendations.items():
            with self.subTest(category=category):
                marker = f'data-model-recommendation="{category}"'
                self.assertIn(marker, html)
                recommendation_html = html.split(marker, 1)[1].split("</span>", 1)[0]
                self.assertIn("QIQI 推荐", recommendation_html)
                self.assertIn("效果", recommendation_html)
                self.assertIn(effect_model, recommendation_html)
                self.assertIn("性价比", recommendation_html)
                self.assertIn(value_model, recommendation_html)

    def test_page_contains_adult_intimacy_controls_and_private_chat_notice(self):
        response = self._render_editor()

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        for field_name in (
            "ENABLE_ADULT_INTIMACY",
            "ENABLE_ADULT_MODEL",
            "ADULT_BASE_URL",
            "ADULT_API_KEY",
            "ADULT_MODEL",
            "ADULT_TEMPERATURE",
            "ADULT_MAX_TOKEN",
            "ADULT_TIMEOUT_SECONDS",
            "ADULT_FALLBACK_TO_MAIN",
            "MODEL_REQUEST_TIMEOUT_SECONDS",
        ):
            self.assertIn(f'name="{field_name}"', html)
        self.assertNotIn('name="ADULT_REUSE_MAIN_CREDENTIALS"', html)
        self.assertIn("启用成人聊天模式", html)
        self.assertIn('onchange="updateAdultChatMode(this)"', html)
        self.assertIn("/api/adult_intimacy/toggle", html)
        self.assertIn('id="userInfoIsMinor"', html)
        self.assertIn("payload.is_minor = nextMinor", html)
        self.assertIn("minorCheckbox.dataset.originalValue", html)
        self.assertIn("nextMinor !== originalMinor", html)
        self.assertNotIn("is_minor: document.getElementById('userInfoIsMinor').checked", html)
        self.assertIn("未成年标记", html)
        self.assertIn("成人模式", html)
        self.assertIn('id="userInfoAdultModeEnabled"', html)
        self.assertIn("updateUserAdultModeStatus", html)
        self.assertIn("adult_mode_enabled: nextValue", html)
        self.assertIn('colspan="11"', html)
        self.assertNotIn('colspan="10"', html)
        self.assertIn("这是总开关", html)
        self.assertIn("用户信息", html)
        self.assertIn("成人模式不再依赖好感度、关键词或连续信号", html)
        self.assertIn("每轮私聊都会直接使用成人模式", html)
        self.assertIn("默认关闭并按成年用户处理", html)
        self.assertIn("群聊、未成年用户、服务号和聊天类型未知时不会启用", html)
        self.assertIn('data-target="section-adult-mode"', html)
        self.assertIn('id="section-adult-mode"', html)
        self.assertLess(
            html.index('id="section-user-info"'),
            html.index('id="section-adult-mode"'),
        )
        self.assertLess(
            html.index('id="section-adult-mode"'),
            html.index('id="section-favorability"'),
        )
        self.assertIn("使用独立成人话题模型", html)
        self.assertIn("所有聊天轮次都使用独立成人模型", html)
        self.assertIn("Grok 4.5", html)
        self.assertIn("/api/adult_model/test", html)
        self.assertIn("timeout_seconds: adultTimeoutSeconds", html)
        self.assertIn('id="adultTimeoutSeconds" min="5" max="600"', html)

    def test_header_contains_five_actions_and_tutorial_link(self):
        response = self._render_editor()

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        actions_html = html.split('<div class="buttons">', 1)[1].split(
            '<!-- <button id="oneKeyDiagnosticButton"', 1
        )[0]
        labels = ("Start Bot", "保存配置", "导入配置", "快速上手", "使用教程")
        positions = [actions_html.index(label) for label in labels]
        self.assertEqual(positions, sorted(positions))
        self.assertIn(
            'href="https://my.feishu.cn/wiki/NE7KwqzO1inv2Wky3vVcvfdQnFg?from=from_copylink"',
            actions_html,
        )
        self.assertIn('class="nav-button tutorial-button"', actions_html)
        self.assertIn('target="_blank"', actions_html)
        self.assertIn('rel="noopener noreferrer"', actions_html)

        template_html = (PROJECT_ROOT / "templates" / "config_editor.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("display: inline-flex;", template_html)
        self.assertIn("align-items: center;", template_html)
        self.assertIn("height: 38px;", template_html)
        self.assertIn("line-height: 1;", template_html)

    def test_brand_wallpaper_and_legal_source_display(self):
        quick_config = {
            "ENABLE_LOGIN_PASSWORD": False,
            "DEEPSEEK_API_KEY": "",
            "DEEPSEEK_BASE_URL": config_editor.QIQI_API_BASE_URL,
            "LISTEN_LIST": [],
            "ENABLE_ONLINE_API": False,
            "ENABLE_AUTO_MESSAGE": False,
        }
        with mock.patch.object(
            config_editor, "parse_config", return_value=quick_config
        ), mock.patch.object(
            config_editor,
            "_get_current_wallpaper_url",
            return_value="/wallpapers/current.jpg",
        ):
            response = config_editor.app.test_client().get("/quick_start")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("QIQI 微信 Bot", html)
        self.assertIn("6.9", html)
        self.assertIn('url("/wallpapers/current.jpg")', html)
        self.assertNotIn("github.com/iwyxdxl/WeChatBot_WXAUTO_SE", html)
        self.assertIn("github.com/KouriChat/KouriChat", html)

        for template_name in ("login.html", "quick_start.html", "character_forum.html"):
            template = (PROJECT_ROOT / "templates" / template_name).read_text(
                encoding="utf-8"
            )
            self.assertNotIn("github.com/iwyxdxl/WeChatBot_WXAUTO_SE", template)

    def test_root_post_is_rejected(self):
        response = config_editor.app.test_client().post("/", data={"MODEL": "x"})

        self.assertEqual(response.status_code, 405)


class ImportedConfigSafetyTests(unittest.TestCase):
    def test_endpoint_change_without_imported_key_clears_old_key(self):
        current_config = {
            "DEEPSEEK_BASE_URL": config_editor.QIQI_API_BASE_URL,
            "DEEPSEEK_API_KEY": "test-old-secret",
            "MODEL": "gpt-5.6-terra",
        }

        merged = config_editor._merge_imported_config(
            current_config,
            {"DEEPSEEK_BASE_URL": "https://new-provider.example/v1/"},
        )

        self.assertEqual(
            merged["DEEPSEEK_BASE_URL"],
            "https://new-provider.example/v1",
        )
        self.assertEqual(merged["DEEPSEEK_API_KEY"], "")
        self.assertEqual(merged["MODEL"], "gpt-5.6-terra")

    def test_forum_does_not_reuse_main_key_across_different_endpoints(self):
        config = {
            "DEEPSEEK_BASE_URL": config_editor.QIQI_API_BASE_URL,
            "DEEPSEEK_API_KEY": "test-main-secret",
            "FORUM_BASE_URL": "https://forum-provider.example/v1",
            "FORUM_API_KEY": "",
        }

        self.assertEqual(config_editor._resolve_forum_api_key(config), "")

    def test_forum_can_reuse_main_key_on_the_same_endpoint(self):
        config = {
            "DEEPSEEK_BASE_URL": config_editor.QIQI_API_BASE_URL,
            "DEEPSEEK_API_KEY": "test-main-secret",
            "FORUM_BASE_URL": config_editor.QIQI_API_BASE_URL + "/",
            "FORUM_API_KEY": "",
        }

        self.assertEqual(
            config_editor._resolve_forum_api_key(config),
            "test-main-secret",
        )


if __name__ == "__main__":
    unittest.main()
