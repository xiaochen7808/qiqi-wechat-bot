from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config_editor


class BarrierLock:
    """Make concurrent test callers reach the fetch lock before one proceeds."""

    def __init__(self, parties):
        self._barrier = threading.Barrier(parties)
        self._lock = threading.Lock()

    def __enter__(self):
        self._barrier.wait(timeout=5)
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._lock.release()

    def locked(self):
        return self._lock.locked()


class QiqiModelCatalogTests(unittest.TestCase):
    def setUp(self):
        with config_editor._qiqi_model_cache_lock:
            config_editor._qiqi_public_model_cache.clear()
            config_editor._qiqi_key_model_cache.clear()
            config_editor._qiqi_key_model_fetch_locks.clear()

    def test_public_catalog_requires_openai_chat_endpoint(self):
        payload = {
            "data": [
                {
                    "model_name": "chat-model",
                    "supported_endpoint_types": ["openai"],
                    "enable_groups": ["chat"],
                },
                {
                    "model_name": "vision-model",
                    "supported_endpoint_types": ["openai"],
                    "enable_groups": ["chat"],
                    "capabilities": {"vision": True},
                },
                {
                    "model_name": "image-generation-only",
                    "supported_endpoint_types": ["openai"],
                    "enable_groups": ["image"],
                },
                {
                    "model_name": "anthropic-only",
                    "supported_endpoint_types": ["anthropic"],
                },
            ]
        }

        catalog = config_editor._extract_qiqi_pricing_catalog(payload)

        self.assertEqual(set(catalog), {"chat-model", "vision-model"})
        self.assertIs(catalog["vision-model"]["image"], True)
        self.assertIs(catalog["chat-model"]["image"], False)

    def test_public_catalog_cache_coalesces_repeated_reads(self):
        payload = {
            "data": [{
                "model_name": "grok-4.5",
                "supported_endpoint_types": ["openai"],
                "enable_groups": ["chat"],
            }]
        }
        with mock.patch.object(config_editor, "_fetch_qiqi_json", return_value=payload) as fetch:
            first, first_stale, first_source = config_editor._get_qiqi_public_catalog()
            second, second_stale, second_source = config_editor._get_qiqi_public_catalog()

        self.assertEqual(first, second)
        self.assertFalse(first_stale)
        self.assertFalse(second_stale)
        self.assertEqual(first_source, "live")
        self.assertEqual(second_source, "cache")
        self.assertEqual(fetch.call_count, 1)

    def test_failed_public_catalog_uses_one_cooled_down_builtin_fallback(self):
        failure = RuntimeError("offline")
        with mock.patch.object(
            config_editor,
            "_fetch_qiqi_json",
            side_effect=failure,
        ) as fetch, mock.patch.object(
            config_editor.app.logger,
            "debug",
        ) as debug_log, mock.patch.object(
            config_editor.app.logger,
            "warning",
        ) as warning_log:
            results = [
                config_editor._get_qiqi_public_catalog()
                for _ in range(4)
            ]

        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(results[0][0], config_editor._qiqi_builtin_catalog())
        self.assertEqual(
            [(stale, source) for _catalog, stale, source in results],
            [(True, "built-in")] * 4,
        )
        debug_log.assert_called_once_with(
            "QIQI 公共模型目录更新失败（%s）",
            "RuntimeError",
        )
        warning_log.assert_not_called()

    def test_manual_refresh_bypasses_public_failure_cooldown(self):
        payload = {
            "data": [{
                "model_name": "future-model",
                "supported_endpoint_types": ["openai"],
                "enable_groups": ["chat"],
            }]
        }
        with mock.patch.object(
            config_editor,
            "_fetch_qiqi_json",
            side_effect=(RuntimeError("offline"), payload),
        ) as fetch:
            unavailable = config_editor._get_qiqi_public_catalog()
            cooled_down = config_editor._get_qiqi_public_catalog()
            refreshed = config_editor._get_qiqi_public_catalog(force_refresh=True)

        self.assertEqual(unavailable[1:], (True, "built-in"))
        self.assertEqual(cooled_down[1:], (True, "built-in"))
        self.assertEqual(refreshed, (
            {"future-model": {"image": False, "online": False}},
            False,
            "live",
        ))
        self.assertEqual(fetch.call_count, 2)

    def test_failed_public_refresh_prefers_stale_live_catalog(self):
        payload = {
            "data": [{
                "model_name": "last-known-model",
                "supported_endpoint_types": ["openai"],
                "enable_groups": ["chat"],
            }]
        }
        with mock.patch.object(config_editor, "_fetch_qiqi_json", return_value=payload):
            live = config_editor._get_qiqi_public_catalog()

        with mock.patch.object(
            config_editor,
            "_fetch_qiqi_json",
            side_effect=RuntimeError("offline"),
        ) as fetch:
            stale = config_editor._get_qiqi_public_catalog(force_refresh=True)
            cooled_down = config_editor._get_qiqi_public_catalog()

        self.assertEqual(live[1:], (False, "live"))
        self.assertEqual(stale, (live[0], True, "stale-cache"))
        self.assertEqual(cooled_down, stale)
        self.assertEqual(fetch.call_count, 1)

    def test_concurrent_forced_public_refreshes_share_one_generation(self):
        payload = {
            "data": [{
                "model_name": "grok-next",
                "supported_endpoint_types": ["openai"],
                "enable_groups": ["chat"],
            }]
        }
        gate = BarrierLock(4)
        with mock.patch.object(
            config_editor,
            "_qiqi_public_model_fetch_lock",
            gate,
        ), mock.patch.object(
            config_editor,
            "_fetch_qiqi_json",
            return_value=payload,
        ) as fetch, ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(config_editor._get_qiqi_public_catalog, True)
                for _ in range(4)
            ]
            results = [future.result(timeout=5) for future in futures]

        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(all(result[0] == results[0][0] for result in results))
        self.assertTrue(all(result[1] is False for result in results))
        self.assertEqual([result[2] for result in results].count("live"), 1)
        self.assertEqual([result[2] for result in results].count("cache"), 3)

    def test_key_cache_stores_only_fingerprint(self):
        payload = {"data": [{"id": "grok-4.5"}]}
        secret = "test-test-secret-value"
        with mock.patch.object(config_editor, "_fetch_qiqi_json", return_value=payload):
            models, stale, source = config_editor._get_qiqi_key_models(secret)

        self.assertEqual(models, {"grok-4.5"})
        self.assertFalse(stale)
        self.assertEqual(source, "live-key")
        cache_dump = json.dumps(config_editor._qiqi_key_model_cache, ensure_ascii=False)
        self.assertNotIn(secret, cache_dump)
        self.assertNotIn("test-test", cache_dump)

    def test_failed_key_catalog_is_cooled_down_without_repeated_warning(self):
        secret = "test-unavailable-secret"
        failure = RuntimeError("request failed for a redacted credential")
        with mock.patch.object(
            config_editor,
            "_fetch_qiqi_json",
            side_effect=failure,
        ) as fetch, mock.patch.object(config_editor.app.logger, "debug") as debug_log:
            results = [
                config_editor._get_qiqi_key_models(secret)
                for _ in range(4)
            ]

        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(results, [(None, True, "key-unavailable")] * 4)
        debug_log.assert_called_once_with(
            "QIQI Key 可用模型目录更新失败（%s）",
            "RuntimeError",
        )
        cache_dump = json.dumps(config_editor._qiqi_key_model_cache, ensure_ascii=False)
        self.assertNotIn(secret, cache_dump)
        self.assertNotIn("request failed", cache_dump)

    def test_page_categories_share_failed_key_cooldown_and_keep_fallback_models(self):
        secret = "test-shared-page-secret"
        config = {
            "ENABLE_LOGIN_PASSWORD": False,
            "DEEPSEEK_BASE_URL": config_editor.QIQI_API_BASE_URL,
            "DEEPSEEK_API_KEY": secret,
            "MODEL": "gpt-5.6-terra",
            "ASSISTANT_BASE_URL": config_editor.QIQI_API_BASE_URL,
            "ASSISTANT_API_KEY": secret,
            "ASSISTANT_MODEL": "gpt-5.4-mini",
            "MOONSHOT_BASE_URL": config_editor.QIQI_API_BASE_URL,
            "MOONSHOT_API_KEY": secret,
            "MOONSHOT_MODEL": "gpt-5.4-mini",
            "ONLINE_BASE_URL": config_editor.QIQI_API_BASE_URL,
            "ONLINE_API_KEY": secret,
            "ONLINE_MODEL": "grok-4.5",
        }
        catalog = config_editor._qiqi_builtin_catalog()
        with mock.patch.object(config_editor, "parse_config", return_value=config), \
                mock.patch.object(
                    config_editor,
                    "_get_qiqi_public_catalog",
                    return_value=(catalog, False, "cache"),
                ), \
                mock.patch.object(
                    config_editor,
                    "_fetch_qiqi_json",
                    side_effect=RuntimeError("offline"),
                ) as fetch, \
                mock.patch.object(config_editor.app.logger, "warning") as warning_log:
            responses = [
                config_editor.app.test_client().get(
                    f"/api/qiqi/models?category={category}"
                )
                for category in ("chat", "assistant", "image", "online")
            ]

        self.assertEqual(fetch.call_count, 1)
        warning_log.assert_not_called()
        for response in responses:
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertTrue(data["models"])
            self.assertTrue(data["stale"])
            self.assertEqual(data["key_source"], "key-unavailable")
            self.assertFalse(data["key_filtered"])
            self.assertNotIn(secret, response.get_data(as_text=True))

    def test_manual_refresh_bypasses_key_failure_cooldown(self):
        secret = "test-refresh-secret"
        payload = {"data": [{"id": "grok-4.5"}]}
        with mock.patch.object(
            config_editor,
            "_fetch_qiqi_json",
            side_effect=(RuntimeError("offline"), payload),
        ) as fetch:
            unavailable = config_editor._get_qiqi_key_models(secret)
            cooled_down = config_editor._get_qiqi_key_models(secret)
            refreshed = config_editor._get_qiqi_key_models(secret, force_refresh=True)

        self.assertEqual(unavailable, (None, True, "key-unavailable"))
        self.assertEqual(cooled_down, unavailable)
        self.assertEqual(refreshed, ({"grok-4.5"}, False, "live-key"))
        self.assertEqual(fetch.call_count, 2)

    def test_failed_manual_refresh_reuses_stale_key_models(self):
        secret = "test-stale-secret"
        payload = {"data": [{"id": "grok-4.5"}]}
        with mock.patch.object(config_editor, "_fetch_qiqi_json", return_value=payload):
            live = config_editor._get_qiqi_key_models(secret)

        with mock.patch.object(
            config_editor,
            "_fetch_qiqi_json",
            side_effect=RuntimeError("offline"),
        ) as fetch:
            stale = config_editor._get_qiqi_key_models(secret, force_refresh=True)
            cooled_down = config_editor._get_qiqi_key_models(secret)

        self.assertEqual(live, ({"grok-4.5"}, False, "live-key"))
        self.assertEqual(stale, ({"grok-4.5"}, True, "stale-key-cache"))
        self.assertEqual(cooled_down, stale)
        self.assertEqual(fetch.call_count, 1)

    def test_concurrent_forced_key_refreshes_share_one_generation(self):
        secret = "test-concurrent-refresh"
        fingerprint = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        gate = BarrierLock(4)
        with config_editor._qiqi_model_cache_lock:
            config_editor._qiqi_key_model_fetch_locks[fingerprint] = gate

        payload = {"data": [{"id": "grok-next"}]}
        with mock.patch.object(
            config_editor,
            "_fetch_qiqi_json",
            return_value=payload,
        ) as fetch, ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(config_editor._get_qiqi_key_models, secret, True)
                for _ in range(4)
            ]
            results = [future.result(timeout=5) for future in futures]

        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(all(result[0] == {"grok-next"} for result in results))
        self.assertTrue(all(result[1] is False for result in results))
        self.assertEqual([result[2] for result in results].count("live-key"), 1)
        self.assertEqual([result[2] for result in results].count("key-cache"), 3)

    def test_key_failure_cache_and_fetch_locks_are_pruned_together(self):
        secrets = [f"test-prune-{index}" for index in range(24)]
        with mock.patch.object(
            config_editor,
            "_fetch_qiqi_json",
            side_effect=RuntimeError("offline"),
        ):
            for secret in secrets:
                config_editor._get_qiqi_key_models(secret)

        with config_editor._qiqi_model_cache_lock:
            cache_fingerprints = set(config_editor._qiqi_key_model_cache)
            lock_fingerprints = set(config_editor._qiqi_key_model_fetch_locks)
            cache_dump = json.dumps(
                config_editor._qiqi_key_model_cache,
                ensure_ascii=False,
            )

        self.assertEqual(
            len(cache_fingerprints),
            config_editor.QIQI_KEY_MODEL_CACHE_MAX_ENTRIES,
        )
        self.assertLessEqual(
            len(lock_fingerprints),
            config_editor.QIQI_KEY_MODEL_CACHE_MAX_ENTRIES,
        )
        self.assertTrue(lock_fingerprints.issubset(cache_fingerprints))
        self.assertTrue(all(secret not in cache_dump for secret in secrets))

    def test_builtin_catalog_accepts_unknown_key_models_for_chat_categories(self):
        catalog = config_editor._qiqi_builtin_catalog()
        secret = "test-future-chat-model"
        for category in ("chat", "assistant", "forum", "adult"):
            with self.subTest(category=category):
                base_field, key_field, model_field = config_editor.QIQI_CONFIG_FIELDS[category]
                config = {
                    "ENABLE_LOGIN_PASSWORD": False,
                    base_field: config_editor.QIQI_API_BASE_URL,
                    key_field: secret,
                    model_field: "future-model",
                }
                with mock.patch.object(config_editor, "parse_config", return_value=config), \
                        mock.patch.object(
                            config_editor,
                            "_get_qiqi_public_catalog",
                            return_value=(catalog, True, "built-in"),
                        ), \
                        mock.patch.object(
                            config_editor,
                            "_get_qiqi_key_models",
                            return_value=({"future-model"}, False, "live-key"),
                        ):
                    response = config_editor.app.test_client().get(
                        f"/api/qiqi/models?category={category}"
                    )

                self.assertEqual(response.status_code, 200)
                data = response.get_json()
                self.assertEqual(data["models"], ["future-model"])
                self.assertEqual(data["catalog_source"], "built-in")
                self.assertEqual(data["key_source"], "live-key")
                self.assertTrue(data["catalog_stale"])
                self.assertFalse(data["key_stale"])
                self.assertTrue(data["stale"])

    def test_builtin_catalog_rejects_unknown_unverified_capability_models(self):
        catalog = config_editor._qiqi_builtin_catalog()
        cases = {
            "image": "gpt-5.4-mini",
            "online": "grok-4.5",
        }
        for category, verified_model in cases.items():
            with self.subTest(category=category):
                base_field, key_field, model_field = config_editor.QIQI_CONFIG_FIELDS[category]
                config = {
                    "ENABLE_LOGIN_PASSWORD": False,
                    base_field: config_editor.QIQI_API_BASE_URL,
                    key_field: "test-capability-model",
                    model_field: verified_model,
                }
                with mock.patch.object(config_editor, "parse_config", return_value=config), \
                        mock.patch.object(
                            config_editor,
                            "_get_qiqi_public_catalog",
                            return_value=(catalog, True, "built-in"),
                        ), \
                        mock.patch.object(
                            config_editor,
                            "_get_qiqi_key_models",
                            return_value=(
                                {verified_model, "future-unverified-model"},
                                False,
                                "live-key",
                            ),
                        ):
                    response = config_editor.app.test_client().get(
                        f"/api/qiqi/models?category={category}"
                    )

                self.assertEqual(response.status_code, 200)
                data = response.get_json()
                self.assertIn(verified_model, data["models"])
                self.assertNotIn("future-unverified-model", data["models"])

    def test_api_intersects_key_models_without_exposing_key(self):
        secret = "test-route-secret"
        config = {
            "ENABLE_LOGIN_PASSWORD": False,
            "DEEPSEEK_BASE_URL": config_editor.QIQI_API_BASE_URL,
            "DEEPSEEK_API_KEY": secret,
            "MODEL": "gpt-5.6-terra",
        }
        catalog = {
            "gpt-5.6-terra": {"image": False, "online": False},
            "grok-4.5": {"image": False, "online": False},
            "not-on-key": {"image": False, "online": False},
        }
        with mock.patch.object(config_editor, "parse_config", return_value=config), \
                mock.patch.object(
                    config_editor,
                    "_get_qiqi_public_catalog",
                    return_value=(catalog, False, "live"),
                ), \
                mock.patch.object(
                    config_editor,
                    "_get_qiqi_key_models",
                    return_value=({"gpt-5.6-terra", "grok-4.5"}, False, "live-key"),
                ):
            response = config_editor.app.test_client().get("/api/qiqi/models?category=chat")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["models"], ["gpt-5.6-terra", "grok-4.5"])
        self.assertTrue(data["key_filtered"])
        self.assertNotIn(secret, response.get_data(as_text=True))
        self.assertNotIn("api_key", data)

    def test_qiqi_base_url_with_trailing_slash_still_filters_by_key(self):
        secret = "test-trailing-slash"
        config = {
            "ENABLE_LOGIN_PASSWORD": False,
            "DEEPSEEK_BASE_URL": f"{config_editor.QIQI_API_BASE_URL}/",
            "DEEPSEEK_API_KEY": secret,
            "MODEL": "grok-4.5",
        }
        catalog = {
            "gpt-5.6-terra": {"image": False, "online": False},
            "grok-4.5": {"image": False, "online": False},
        }
        with mock.patch.object(config_editor, "parse_config", return_value=config), \
                mock.patch.object(
                    config_editor,
                    "_get_qiqi_public_catalog",
                    return_value=(catalog, False, "live"),
                ), \
                mock.patch.object(
                    config_editor,
                    "_get_qiqi_key_models",
                    return_value=({"grok-4.5"}, False, "live-key"),
                ) as get_key_models:
            response = config_editor.app.test_client().get("/api/qiqi/models?category=chat")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["models"], ["grok-4.5"])
        self.assertTrue(data["key_filtered"])
        get_key_models.assert_called_once_with(secret, False)

    def test_adult_catalog_uses_dedicated_qiqi_credentials(self):
        main_secret = "test-main-qiqi"
        adult_secret = "test-dedicated-adult"
        config = {
            "ENABLE_LOGIN_PASSWORD": False,
            "DEEPSEEK_BASE_URL": config_editor.QIQI_API_BASE_URL,
            "DEEPSEEK_API_KEY": main_secret,
            "ADULT_BASE_URL": config_editor.QIQI_API_BASE_URL,
            "ADULT_API_KEY": adult_secret,
            "ADULT_MODEL": "grok-4.5",
        }
        catalog = {
            "grok-4.5": {"image": False, "online": False},
            "gpt-5.6-terra": {"image": False, "online": False},
        }
        with mock.patch.object(config_editor, "parse_config", return_value=config), \
                mock.patch.object(
                    config_editor,
                    "_get_qiqi_public_catalog",
                    return_value=(catalog, False, "live"),
                ), \
                mock.patch.object(
                    config_editor,
                    "_get_qiqi_key_models",
                    return_value=({"grok-4.5"}, False, "live-key"),
                ) as get_key_models:
            response = config_editor.app.test_client().get(
                "/api/qiqi/models?category=adult"
            )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["category"], "adult")
        self.assertEqual(data["models"], ["grok-4.5"])
        self.assertTrue(data["key_filtered"])
        get_key_models.assert_called_once_with(adult_secret, False)

    def test_non_qiqi_endpoint_does_not_fetch_key_models(self):
        config = {
            "ENABLE_LOGIN_PASSWORD": False,
            "DEEPSEEK_BASE_URL": "https://api.example.com/v1",
            "DEEPSEEK_API_KEY": "test-custom-provider",
            "MODEL": "custom-model",
        }
        catalog = {
            "gpt-5.6-terra": {"image": False, "online": False},
            "grok-4.5": {"image": False, "online": False},
        }
        with mock.patch.object(config_editor, "parse_config", return_value=config), \
                mock.patch.object(
                    config_editor,
                    "_get_qiqi_public_catalog",
                    return_value=(catalog, False, "live"),
                ), \
                mock.patch.object(config_editor, "_get_qiqi_key_models") as get_key_models:
            response = config_editor.app.test_client().get("/api/qiqi/models?category=chat")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertCountEqual(data["models"], catalog)
        self.assertFalse(data["key_filtered"])
        self.assertEqual(data["key_source"], "not-used")
        get_key_models.assert_not_called()

    def test_inaccessible_configured_model_is_only_preserved(self):
        config = {
            "ENABLE_LOGIN_PASSWORD": False,
            "DEEPSEEK_BASE_URL": config_editor.QIQI_API_BASE_URL,
            "DEEPSEEK_API_KEY": "test-limited-models",
            "MODEL": "not-on-key",
        }
        catalog = {
            "grok-4.5": {"image": False, "online": False},
            "not-on-key": {"image": False, "online": False},
        }
        with mock.patch.object(config_editor, "parse_config", return_value=config), \
                mock.patch.object(
                    config_editor,
                    "_get_qiqi_public_catalog",
                    return_value=(catalog, False, "live"),
                ), \
                mock.patch.object(
                    config_editor,
                    "_get_qiqi_key_models",
                    return_value=({"grok-4.5"}, False, "live-key"),
                ):
            response = config_editor.app.test_client().get("/api/qiqi/models?category=chat")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["models"], ["grok-4.5"])
        self.assertEqual(data["preserved_models"], ["not-on-key"])
        self.assertNotIn("not-on-key", data["models"])


if __name__ == "__main__":
    unittest.main()
