import ast
import builtins
import copy
from datetime import datetime
import inspect
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from filelock import FileLock
from model_timeout import resolve_model_timeout_seconds


BOT_PATH = Path(__file__).resolve().parents[1] / "bot.py"
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8")
BOT_TREE = ast.parse(BOT_SOURCE)


def load_functions(*names, namespace=None):
    selected = [
        node for node in BOT_TREE.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    module = ast.Module(body=selected, type_ignores=[])
    scope = dict(namespace or {})
    exec(compile(module, str(BOT_PATH), "exec"), scope)
    return scope


def find_openai_client_kwargs(target_name):
    matches = []
    for node in ast.walk(BOT_TREE):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == target_name for target in node.targets):
            continue
        call = node.value
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "OpenAI"
        ):
            continue
        kwargs = {}
        for keyword in call.keywords:
            try:
                kwargs[keyword.arg] = ast.literal_eval(keyword.value)
            except (ValueError, TypeError):
                # Only timeout/retry literals are asserted below. Keep other
                # expressions inspectable without trying to execute them.
                kwargs[keyword.arg] = ast.dump(keyword.value)
        matches.append(kwargs)
    if len(matches) != 1:
        raise AssertionError(f"expected one OpenAI initializer for {target_name}, got {len(matches)}")
    return matches[0]


class BotConsistencyTests(unittest.TestCase):
    def _adult_intimacy_scope(
        self, *, score=90, age="20", is_minor=False, is_group_chat=False,
        enabled=True, user_enabled=True, proactive_score=90
    ):
        state_data = {}
        context = {
            "score": score,
            "age": age,
            "is_minor": is_minor,
            "is_group_chat": is_group_chat,
            "enabled": enabled,
            "user_enabled": user_enabled,
            "proactive_score": proactive_score,
        }

        def load_state_data():
            return copy.deepcopy(state_data)

        def save_state_data(data):
            state_data.clear()
            state_data.update(copy.deepcopy(data))

        # Keep private parsing helpers with the public behavior under test, but
        # replace the file-backed store so these tests remain deterministic.
        function_names = {
            "create_default_adult_intimacy_state",
            "normalize_adult_intimacy_state",
            "classify_adult_intimacy_signal",
            "is_user_adult_mode_enabled",
            "update_adult_intimacy_state",
            "build_adult_intimacy_prompt",
        }
        function_names.update(
            node.name for node in BOT_TREE.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("_")
            and ("adult" in node.name or "intimacy" in node.name)
        )
        scope = load_functions(
            *function_names,
            namespace={
                "datetime": __import__("datetime").datetime,
                "logger": logging.getLogger("bot-consistency-test"),
                "re": __import__("re"),
                "threading": threading,
                "adult_intimacy_state_lock": threading.RLock(),
                "get_dynamic_config": lambda key, default: (
                    context["enabled"]
                    if key == "ENABLE_ADULT_INTIMACY"
                    else context["proactive_score"]
                    if key == "ADULT_INTIMACY_PROACTIVE_SCORE"
                    else default
                ),
                "load_adult_intimacy_state_data": load_state_data,
                "save_adult_intimacy_state_data": save_state_data,
                "get_user_favorability": lambda _user_id: {"score": context["score"]},
                "get_user_info_record": lambda _user_id: {
                    "age": context["age"],
                    "is_minor": context["is_minor"],
                    "adult_mode_enabled": context["user_enabled"],
                },
                "is_group_speaker_id": lambda _user_id: context["is_group_chat"],
                "is_user_group_chat": lambda _user_id: context["is_group_chat"],
            },
        )
        scope["get_user_adult_intimacy_state"] = lambda user_id: scope[
            "normalize_adult_intimacy_state"
        ](state_data.get(str(user_id), {}))
        return scope, state_data, context

    def _call_adult_intimacy_function(
        self,
        function,
        *,
        user_id="alice",
        message=None,
        score=90,
        age="20",
        is_group_chat=False,
    ):
        values = {
            "user_id": user_id,
            "speaker_id": user_id,
            "message": message,
            "current_message": message,
            "user_message": message,
            "merged_message": message,
            "text": message,
            "score": score,
            "favorability_score": score,
            "age": age,
            "user_age": age,
            "is_group": is_group_chat,
            "is_group_chat": is_group_chat,
        }
        kwargs = {
            name: values[name]
            for name in inspect.signature(function).parameters
            if name in values and values[name] is not None
        }
        return function(**kwargs)

    def test_adult_intimacy_signal_classifier_distinguishes_conversation_cues(self):
        scope, _state_data, _context = self._adult_intimacy_scope()
        classify = scope["classify_adult_intimacy_signal"]

        # The classifier is retained only for legacy state-file compatibility;
        # manual adult-mode routing no longer depends on nuanced signal scores.
        self.assertEqual(classify("今晚想聊点更亲密的，你主动一点"), "strong")
        self.assertEqual(classify("今天午饭吃了面"), "neutral")
        self.assertEqual(classify("聊骚是什么意思？"), "neutral")
        self.assertEqual(classify("我拒绝聊骚"), "stop")
        self.assertEqual(
            classify("我想聊点更亲密的，但还是别聊这个了，换个话题"),
            "stop",
        )

    def test_adult_intimacy_state_normalization_preserves_required_fields(self):
        scope, _state_data, _context = self._adult_intimacy_scope()
        normalize = scope["normalize_adult_intimacy_state"]

        default_state = normalize(None)
        self.assertFalse(default_state["active"])
        self.assertEqual(default_state["medium_signal_streak"], 0)
        self.assertEqual(default_state["last_signal"], "neutral")
        self.assertEqual(default_state["last_updated"], "")

        normalized = normalize({
            "active": "true",
            "medium_signal_streak": 999,
            "last_signal": "unexpected",
            "last_updated": None,
        })
        self.assertTrue(normalized["active"])
        self.assertLessEqual(normalized["medium_signal_streak"], 10)
        self.assertEqual(normalized["last_signal"], "neutral")
        self.assertEqual(normalized["last_updated"], "")
        self.assertFalse(normalize({"active": "false"})["active"])
        self.assertEqual(
            normalize({"medium_signal_streak": "invalid"})["medium_signal_streak"],
            0,
        )
        self.assertEqual(normalize({"last_signal": "manual"})["last_signal"], "manual")

    def test_reply_hot_path_does_not_write_legacy_adult_state(self):
        process_node = next(
            node for node in BOT_TREE.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_process_user_messages_locked"
        )
        called_names = {
            child.func.id
            for child in ast.walk(process_node)
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
        }
        self.assertNotIn("update_adult_intimacy_state", called_names)

    def test_worker_rechecks_aggregation_deadline_before_queue_pop(self):
        process_node = next(
            node for node in BOT_TREE.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_process_user_messages_locked"
        )
        ready_line = min(
            child.lineno
            for child in ast.walk(process_node)
            if isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "is_user_queue_ready_for_processing"
        )
        pop_line = min(
            child.lineno
            for child in ast.walk(process_node)
            if isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and isinstance(child.func.value, ast.Name)
            and child.func.value.id == "user_queues"
            and child.func.attr == "pop"
        )
        self.assertLess(ready_line, pop_line)

    def test_adult_intimacy_state_follows_manual_user_switch_for_every_message(self):
        scope, state_data, context = self._adult_intimacy_scope()
        update = scope["update_adult_intimacy_state"]
        normalize = scope["normalize_adult_intimacy_state"]

        def update_message(user_id, message, score):
            result = self._call_adult_intimacy_function(
                update,
                user_id=user_id,
                message=message,
                score=score,
                age=context["age"],
                is_group_chat=context["is_group_chat"],
            )
            return normalize(result if isinstance(result, dict) else state_data.get(user_id, {}))

        state_data["alice"] = {
            "active": False,
            "medium_signal_streak": 7,
            "last_signal": "stop",
            "last_positive_at": "2000-01-01 00:00:00",
        }
        for score, message in (
            (0, "今天午饭吃了面"),
            (100, "今晚想聊点更亲密的"),
            (5, "别聊这个了，换个话题"),
        ):
            with self.subTest(score=score, message=message):
                record = update_message("alice", message, score)
                self.assertTrue(record["active"])
                self.assertEqual(record["medium_signal_streak"], 0)
                self.assertEqual(record["last_signal"], "manual")

        context["user_enabled"] = False
        disabled = update_message("alice", "今晚想聊点更亲密的", 100)
        self.assertFalse(disabled["active"])
        self.assertEqual(disabled["last_signal"], "manual")

    def test_adult_intimacy_manual_switch_still_blocks_minor_and_group_chat(self):
        scope, _state_data, context = self._adult_intimacy_scope(score=95)
        update = scope["update_adult_intimacy_state"]
        build_prompt = scope["build_adult_intimacy_prompt"]
        normalize = scope["normalize_adult_intimacy_state"]

        enabled_private = self._call_adult_intimacy_function(
            update,
            user_id="alice",
            message="普通聊天",
            score=0,
        )
        self.assertTrue(normalize(enabled_private)["active"])
        self.assertIn("成人亲密互动已激活", build_prompt("alice", current_message="普通聊天"))

        context["is_minor"] = True
        marked_minor = self._call_adult_intimacy_function(
            update,
            user_id="alice",
            message="普通聊天",
            score=95,
        )
        self.assertFalse(normalize(marked_minor)["active"])
        self.assertEqual(build_prompt("alice", current_message="普通聊天"), "")

        context["is_minor"] = False
        context["is_group_chat"] = True
        group_result = self._call_adult_intimacy_function(
            update,
            user_id="alice",
            message="普通聊天",
            score=95,
            is_group_chat=True,
        )
        self.assertFalse(normalize(group_result)["active"])
        self.assertEqual(
            build_prompt("alice", is_group_chat=True, current_message="普通聊天"),
            "",
        )

    def test_global_and_per_user_switches_are_both_required(self):
        scope, _state_data, context = self._adult_intimacy_scope(
            score=95,
            is_minor=False,
            enabled=False,
        )
        update = scope["update_adult_intimacy_state"]
        build_prompt = scope["build_adult_intimacy_prompt"]
        normalize = scope["normalize_adult_intimacy_state"]

        result = self._call_adult_intimacy_function(
            update,
            user_id="alice",
            message="今晚想聊点更亲密的，你主动一点",
            score=95,
        )
        self.assertFalse(normalize(result)["active"])
        self.assertEqual(build_prompt("alice", current_message="普通聊天"), "")

        context["enabled"] = True
        context["user_enabled"] = False
        self.assertFalse(normalize(self._call_adult_intimacy_function(
            update, user_id="alice", message="成人关键词", score=100
        ))["active"])
        self.assertEqual(build_prompt("alice", score=100, current_message="成人关键词"), "")

    def test_user_info_prompt_hides_minor_age_text_unless_manually_marked(self):
        profile = {
            "name": "",
            "age": "17",
            "is_minor": False,
            "gender": "",
            "location": "",
            "job_or_school": "",
            "likes": [],
            "dislikes": [],
            "important_info": [],
        }
        scope = load_functions(
            "_parse_adult_intimacy_chinese_age_number",
            "_parse_adult_intimacy_age",
            "build_user_info_prompt",
            namespace={
                "re": __import__("re"),
                "get_user_info_record": lambda _user_id: dict(profile),
                "should_allow_profile_question": lambda _record: False,
            },
        )

        adult_prompt = scope["build_user_info_prompt"]("alice")
        self.assertIn("年龄状态：成年", adult_prompt)
        self.assertNotIn("年龄：17", adult_prompt)

        profile["is_minor"] = True
        minor_prompt = scope["build_user_info_prompt"]("alice")
        self.assertIn("年龄状态：未成年", minor_prompt)
        self.assertIn("成人聊天模式对该用户永久禁用", minor_prompt)

    def test_adult_intimacy_prompt_ignores_favorability_state_and_keywords(self):
        scope, state_data, context = self._adult_intimacy_scope(score=0)
        build_prompt = scope["build_adult_intimacy_prompt"]
        state_data["alice"] = {
            "active": False,
            "last_signal": "stop",
            "last_positive_at": "2000-01-01 00:00:00",
        }

        for score, message in (
            (0, "今天午饭吃了面"),
            (79, "别聊这个了，换个话题"),
            (100, "今晚想聊点更亲密的，你主动一点"),
        ):
            with self.subTest(score=score, message=message):
                context["score"] = score
                prompt = self._call_adult_intimacy_function(
                    build_prompt,
                    message=message,
                    score=score,
                )
                self.assertIn("成人亲密互动已激活", prompt)

    def test_default_favorability_rules_define_60_80_90_behavior_boundaries(self):
        scope = load_functions("get_default_favorability_stage_rules")
        rules = {
            rule["min"]: rule
            for rule in scope["get_default_favorability_stage_rules"]()
        }

        flirt = rules[60]
        self.assertEqual((flirt["min"], flirt["max"]), (60, 79))
        self.assertEqual(flirt["name"], "暧昧阶段")
        self.assertIn("自然的暧昧张力", flirt["prompt"])
        self.assertIn("不主动转入成人话题", flirt["prompt"])

        intimate = rules[80]
        self.assertEqual((intimate["min"], intimate["max"]), (80, 89))
        self.assertEqual(intimate["name"], "亲密阶段")
        self.assertIn("成人亲密互动已激活", intimate["prompt"])
        self.assertIn("更直白的挑逗表达", intimate["prompt"])
        self.assertIn("先回应用户当前内容", intimate["prompt"])

        fully_intimate = rules[90]
        self.assertEqual((fully_intimate["min"], fully_intimate["max"]), (90, 100))
        self.assertEqual(fully_intimate["name"], "完全亲密阶段")
        self.assertIn("主动提出成人亲密话题", fully_intimate["prompt"])
        self.assertIn("不能施压、威胁、羞辱或伤害", fully_intimate["prompt"])
        self.assertIn("普通问题仍然先完整回答", fully_intimate["prompt"])

    def _favorability_classifier_scope(self):
        names = (
            "normalize_favorability_score",
            "normalize_favorability_change",
            "_favorability_config_number",
            "_compact_relationship_message",
            "_message_targets_bot_negatively",
            "_message_is_self_distress",
            "_message_is_reconciliation",
            "_message_is_polite_closure",
            "_message_is_direct_rejection",
            "infer_emotion_from_message",
            "infer_favorability_change",
        )
        return load_functions(
            *names,
            namespace={
                "re": __import__("re"),
                "DEFAULT_FAVORABILITY_SCORE": 30.0,
                "get_dynamic_config": lambda _key, default: default,
            },
        )

    def test_emotion_driven_favorability_classifies_positive_negative_and_neutral(self):
        scope = self._favorability_classifier_scope()
        infer_emotion = scope["infer_emotion_from_message"]
        classify = scope["infer_favorability_change"]

        cases = (
            ("谢谢你，晚安", 0.5, "positive"),
            ("有你真好，我最喜欢你", 1.0, "very_positive"),
            ("今天午饭吃了面", 0.0, "neutral"),
            ("我不喜欢你了", -0.5, "mild_negative"),
            ("你真恶心，滚", -1.0, "strong_negative"),
            ("我今天很难受，陪陪我", 0.0, "neutral"),
            ("我不是不喜欢你，别生气", 0.5, "positive"),
            ("我不讨厌你，也不是不想理你", 0.5, "positive"),
            ("对不起，我不想聊了", -0.5, "mild_negative"),
            ("不用了，谢谢", 0.0, "neutral"),
            ("老板说这个东西恶心死了", 0.0, "neutral"),
            ("你不是废物，别这样想", 0.0, "neutral"),
            ("我不是不想和你聊", 0.5, "positive"),
        )
        for message, expected_change, expected_category in cases:
            with self.subTest(message=message):
                emotion = infer_emotion(message, {"score": 30.0})
                change, _reason, category = classify(message, emotion)
                self.assertEqual(change, expected_change)
                self.assertEqual(category, expected_category)

    def test_half_point_stage_boundaries_do_not_round_up_early(self):
        scope = load_functions(
            "normalize_favorability_score",
            "get_default_favorability_stage_rules",
            "normalize_favorability_stage_rules",
            "get_favorability_rule_for_score",
            "get_favorability_stage",
            namespace={"get_dynamic_config": lambda _key, default: default},
        )
        stage = scope["get_favorability_stage"]
        self.assertEqual(stage(19.5), "陌生阶段")
        self.assertEqual(stage(20.0), "熟悉阶段")
        self.assertEqual(stage(59.5), "亲近阶段")
        self.assertEqual(stage(60.0), "暧昧阶段")
        self.assertEqual(stage(79.5), "暧昧阶段")
        self.assertEqual(stage(80.0), "亲密阶段")

    def test_repeated_strong_positive_and_system_turns_do_not_double_score(self):
        records = {
            "alice": {
                "score": 30.0,
                "message_count": 0,
                "voice_video_request_streak": 0,
            },
            "bob": {
                "score": 30.0,
                "message_count": 0,
                "voice_video_request_streak": 0,
            },
        }

        def load_records(**_kwargs):
            return copy.deepcopy(records)

        def save_records(data):
            records.clear()
            records.update(copy.deepcopy(data))

        scope = self._favorability_classifier_scope()
        extra = load_functions(
            "parse_favorability_datetime",
            "contains_voice_video_request",
            "update_user_favorability",
            namespace={
                **scope,
                "datetime": datetime,
                "favorability_lock": threading.RLock(),
                "favorability_file_lock": threading.RLock(),
                "load_favorability_data": load_records,
                "save_favorability_data": save_records,
                "create_default_favorability_record": lambda: {"score": 30.0},
                "apply_favorability_decay": lambda user, data: data[user]["score"],
                "get_favorability_stage": lambda _score: "测试阶段",
                "logger": logging.getLogger("favorability-repeat-test"),
            },
        )
        update = extra["update_user_favorability"]
        first = update("alice", "有你真好，我最喜欢你")
        second = update("alice", "有你真好，我最喜欢你")
        before_system = copy.deepcopy(records)
        system_result = update("alice", "触发主动发消息：问问用户在做什么")

        self.assertEqual(first["last_change"], 1.0)
        self.assertEqual(second["last_change"], 0.0)
        self.assertIn("不重复加分", second["last_reason"])
        self.assertEqual(second["score"], 31.0)
        self.assertIsNone(system_result)
        self.assertEqual(records, before_system)

        bob_first = update("bob", "谢谢你，晚安")
        bob_second = update("bob", "谢谢你，晚安")
        self.assertEqual(bob_first["last_change"], 0.5)
        self.assertEqual(bob_second["last_change"], 0.0)
        self.assertEqual(bob_second["score"], 30.5)

        update("alice", "你真恶心，滚", relationship_event_id="event-1")
        after_event = copy.deepcopy(records)
        replayed = update(
            "alice", "你真恶心，滚", relationship_event_id="event-1"
        )
        self.assertEqual(replayed["score"], after_event["alice"]["score"])
        self.assertEqual(records, after_event)

        first_batch = update(
            "alice",
            "有你真好\n谢谢你",
            relationship_events=[("event-a", "有你真好"), ("event-b", "谢谢你")],
        )
        merged_after_restart = update(
            "alice",
            "有你真好\n谢谢你\n今天午饭吃了面",
            relationship_events=[
                ("event-a", "有你真好"),
                ("event-b", "谢谢你"),
                ("event-c", "今天午饭吃了面"),
            ],
        )
        self.assertEqual(first_batch["last_change"], 1.0)
        self.assertEqual(merged_after_restart["last_change"], 0.0)
        self.assertEqual(merged_after_restart["score"], first_batch["score"])
        self.assertIn(
            "event-c", merged_after_restart["applied_relationship_event_ids"]
        )

    def test_favorability_decay_uses_half_points_is_capped_and_not_reapplied(self):
        saved = []
        scope = load_functions(
            "normalize_favorability_score",
            "normalize_favorability_change",
            "parse_favorability_date",
            "_favorability_config_number",
            "apply_favorability_decay",
            namespace={
                "datetime": datetime,
                "get_dynamic_config": lambda _key, default: default,
                "get_favorability_stage": lambda score: f"stage-{score}",
                "save_favorability_data": lambda data: saved.append(copy.deepcopy(data)),
                "logger": logging.getLogger("favorability-decay-test"),
            },
        )
        today = datetime.now().date()
        data = {
            "alice": {
                "score": 30,
                "last_interaction_date": (today - __import__("datetime").timedelta(days=5)).strftime("%Y-%m-%d"),
                "offline_decay_total": 0.0,
                "decay_schema_version": 1,
            }
        }
        apply_decay = scope["apply_favorability_decay"]
        self.assertEqual(apply_decay("alice", data), 29.0)
        self.assertEqual(data["alice"]["last_change"], -1.0)
        self.assertEqual(apply_decay("alice", data), 29.0)
        self.assertEqual(len(saved), 1)

        capped = {
            "bob": {
                "score": 30.0,
                "last_interaction_date": (today - __import__("datetime").timedelta(days=60)).strftime("%Y-%m-%d"),
                "offline_decay_total": 0.0,
                "decay_schema_version": 1,
            }
        }
        self.assertEqual(apply_decay("bob", capped), 20.0)
        self.assertEqual(capped["bob"]["offline_decay_total"], 10.0)

        legacy = {
            "carol": {
                "score": 30.0,
                "last_interaction_date": (today - __import__("datetime").timedelta(days=5)).strftime("%Y-%m-%d"),
            }
        }
        self.assertEqual(apply_decay("carol", legacy), 30.0)
        self.assertEqual(legacy["carol"]["offline_decay_total"], 1.0)
        self.assertEqual(legacy["carol"]["decay_schema_version"], 1)
        self.assertEqual(apply_decay("carol", legacy), 30.0)

    def test_favorability_read_error_can_abort_before_overwriting_data(self):
        with tempfile.TemporaryDirectory() as directory:
            favorability_file = Path(directory) / "favorability.json"
            favorability_file.write_text("{invalid", encoding="utf-8")
            scope = load_functions(
                "load_favorability_data",
                namespace={
                    "os": os,
                    "json": json,
                    "FAVORABILITY_FILE": str(favorability_file),
                    "favorability_lock": threading.RLock(),
                    "favorability_file_lock": FileLock(str(favorability_file) + ".lock"),
                    "logger": logging.getLogger("favorability-read-test"),
                },
            )

            with self.assertRaises(json.JSONDecodeError):
                scope["load_favorability_data"](raise_on_error=True)
            self.assertEqual(scope["load_favorability_data"](), {})
            self.assertEqual(favorability_file.read_text(encoding="utf-8"), "{invalid")

    def test_emotion_state_concurrent_updates_keep_all_users_and_valid_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = os.path.join(temp_dir, "emotion_state.json")
            scope = load_functions(
                "create_default_emotion_state",
                "normalize_emotion_state",
                "load_emotion_state_data",
                "save_emotion_state_data",
                "update_user_emotion_state",
                namespace={
                    "datetime": datetime,
                    "json": json,
                    "os": os,
                    "threading": threading,
                    "EMOTION_STATE_FILE": state_file,
                    "emotion_state_lock": threading.RLock(),
                    "logger": logging.getLogger("emotion-concurrency-test"),
                    "get_user_favorability": lambda _user: {"score": 30.0},
                    "infer_emotion_from_message": lambda _message, _record: None,
                },
            )
            update = scope["update_user_emotion_state"]
            threads = [
                threading.Thread(target=update, args=(f"user-{index}", f"消息 {index}"))
                for index in range(12)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            data = json.loads(Path(state_file).read_text(encoding="utf-8"))
            self.assertEqual(set(data), {f"user-{index}" for index in range(12)})
            self.assertFalse(list(Path(temp_dir).glob("*.tmp")))

    def _group_identity_scope(self, **namespace):
        identity_namespace = {
            "base64": __import__("base64"),
            "binascii": __import__("binascii"),
            "GROUP_SPEAKER_ID_SEPARATOR": "::",
            "GROUP_SPEAKER_ID_PREFIX": "group-v1.",
        }
        identity_namespace.update(namespace)
        return load_functions(
            "_encode_group_speaker_component",
            "_decode_group_speaker_id",
            "build_speaker_id",
            "conversation_id_from_speaker_id",
            "is_group_speaker_id",
            namespace=identity_namespace,
        )

    def _dynamic_config_scope(self, open_calls=None):
        def tracked_open(*args, **kwargs):
            if open_calls is not None:
                open_calls.append(args[0])
            return builtins.open(*args, **kwargs)

        return load_functions(
            "_load_dynamic_config_values",
            namespace={
                "ast": ast,
                "os": os,
                "open": tracked_open,
                "threading": threading,
                "_dynamic_config_cache_lock": threading.Lock(),
                "_dynamic_config_cache_signature": None,
                "_dynamic_config_cache_values": {},
            },
        )

    def test_sensitive_chat_scope_requires_exact_friend_type(self):
        scope = load_functions(
            "remember_chat_scope",
            "get_user_chat_scope",
            namespace={
                "threading": threading,
                "group_cache_lock": threading.Lock(),
                "group_chat_cache": {},
                "chat_scope_cache": {},
                "logger": logging.getLogger("bot-consistency-test"),
            },
        )

        class FakeChat:
            def __init__(self, chat_type):
                self.chat_type = chat_type

            def ChatInfo(self):
                return {"chat_type": self.chat_type}

        self.assertEqual(scope["remember_chat_scope"]("friend", FakeChat("friend")), "private")
        self.assertEqual(scope["get_user_chat_scope"]("friend"), "private")
        self.assertEqual(scope["remember_chat_scope"]("group", FakeChat("group")), "group")
        self.assertEqual(scope["get_user_chat_scope"]("group"), "group")
        self.assertEqual(scope["remember_chat_scope"]("service", FakeChat("service")), "unknown")
        self.assertEqual(scope["get_user_chat_scope"]("service"), "unknown")

    def _send_reply_scope(self, parts, typing_speed=0.03, random_typing_delay=0.0):
        class FakeTime:
            def __init__(self):
                self.sleep_calls = []

            def sleep(self, seconds):
                self.sleep_calls.append(seconds)

        class UpperBoundRandom:
            def __init__(self):
                self.uniform_calls = []

            def uniform(self, lower, upper):
                self.uniform_calls.append((lower, upper))
                return upper

        fake_time = FakeTime()
        fake_random = UpperBoundRandom()
        scope = load_functions(
            "send_reply",
            namespace={
                "logger": logging.getLogger("bot-consistency-test"),
                "time": fake_time,
                "random": fake_random,
                "wx_ui_lock": threading.RLock(),
                "user_info_lock": threading.RLock(),
                "user_info_file_lock": threading.RLock(),
                "is_sending_message": False,
                "ENABLE_EMOJI_SENDING": False,
                "remove_timestamps": lambda text: text,
                "clean_stage_directions": lambda text: text,
                "REMOVE_PARENTHESES": False,
                "split_message_with_context": lambda _text: list(parts),
                "replace_stored_assistant_reply_with_sent_parts": lambda *args: None,
                "wx_send_text_to_chat": lambda _user, _content: True,
                "register_ai_sent_message": lambda _user, _content: None,
                "ENABLE_MEMORY": False,
                "AVERAGE_TYPING_SPEED": typing_speed,
                "RANDOM_TYPING_SPEED_MIN": random_typing_delay,
                "RANDOM_TYPING_SPEED_MAX": random_typing_delay,
            },
        )
        return scope, fake_time, fake_random

    def _response_scope(self):
        captured = []
        scope = {
            "logger": logging.getLogger("bot-consistency-test"),
            "MAX_GROUPS": 10,
            "queue_lock": threading.RLock(),
            "chat_contexts": {},
            "save_chat_contexts": lambda: None,
            "get_user_prompt": lambda _user: "role",
            "build_favorability_prompt": lambda _user: "",
            "build_emotion_prompt": lambda _user: "",
            "build_user_info_prompt": lambda _user: "",
            "build_adult_intimacy_prompt": (
                lambda _user, current_message=None: "adult-intimacy-marker"
            ),
            "build_optional_profile_question_prompt": lambda _user, _message: "",
            "build_chat_active_topic_prompt": lambda _user, _message: "",
            "build_bot_identity_prompt": lambda _user, _prompt: "",
            "should_route_to_adult_model": lambda *_args, **_kwargs: False,
            "call_adult_chat_api_with_fallback": lambda *_args, **_kwargs: "adult-answer",
            "call_chat_api_with_retry": lambda messages, _user, is_summary=False: captured.append(messages) or "answer",
        }
        scope = load_functions("get_deepseek_response", namespace=scope)
        return scope, captured

    def test_main_response_only_injects_adult_prompt_when_turn_allows_it(self):
        scope, captured = self._response_scope()

        scope["get_deepseek_response"]("hello", "alice")
        self.assertNotIn("adult-intimacy-marker", captured[-1][0]["content"])

        scope["get_deepseek_response"](
            "hello", "alice", allow_adult_intimacy=True
        )
        self.assertIn("adult-intimacy-marker", captured[-1][0]["content"])

    def test_adult_turn_skips_profile_questions_and_active_topic_injection(self):
        scope, captured = self._response_scope()
        profile_calls = []
        topic_calls = []
        scope["build_optional_profile_question_prompt"] = (
            lambda user, message: profile_calls.append((user, message))
            or "profile-question-marker"
        )
        scope["build_chat_active_topic_prompt"] = (
            lambda user, message: topic_calls.append((user, message))
            or "active-topic-marker"
        )

        scope["get_deepseek_response"](
            "继续",
            "alice",
            allow_adult_intimacy=True,
        )

        system_prompt = captured[-1][0]["content"]
        self.assertIn("adult-intimacy-marker", system_prompt)
        self.assertNotIn("profile-question-marker", system_prompt)
        self.assertNotIn("active-topic-marker", system_prompt)
        self.assertEqual(profile_calls, [])
        self.assertEqual(topic_calls, [])

    def test_adult_backend_reuses_complete_request_and_stores_one_turn(self):
        scope, main_requests = self._response_scope()
        adult_requests = []
        scope["should_route_to_adult_model"] = lambda *_args, **_kwargs: True
        scope["call_adult_chat_api_with_fallback"] = (
            lambda messages, _user, is_summary=False: (
                adult_requests.append(messages) or "adult-answer"
            )
        )

        reply = scope["get_deepseek_response"](
            "继续",
            "alice",
            allow_adult_intimacy=True,
        )

        self.assertEqual(reply, "adult-answer")
        self.assertEqual(main_requests, [])
        self.assertEqual(len(adult_requests), 1)
        self.assertIn("adult-intimacy-marker", adult_requests[0][0]["content"])
        self.assertEqual(adult_requests[0][-1], {"role": "user", "content": "继续"})
        self.assertEqual(
            scope["chat_contexts"]["alice"],
            [
                {"role": "user", "content": "继续"},
                {"role": "assistant", "content": "adult-answer"},
            ],
        )

    def test_adult_model_route_uses_only_manual_switch_and_safety_gates(self):
        context = {
            "global_enabled": True,
            "user_enabled": True,
            "model_enabled": True,
            "client_available": True,
            "minor": False,
            "group": False,
        }

        scope = load_functions(
            "should_route_to_adult_model",
            namespace={
                "logger": logging.getLogger("bot-consistency-test"),
                "_adult_intimacy_enabled": lambda: context["global_enabled"],
                "is_user_adult_mode_enabled": lambda _user: context["user_enabled"],
                "_adult_model_enabled": lambda: context["model_enabled"],
                "get_runtime_adult_client": (
                    lambda: object() if context["client_available"] else None
                ),
                "_is_adult_intimacy_group_identity": (
                    lambda _user, _is_group=False: context["group"]
                ),
                "_adult_intimacy_user_is_marked_minor": (
                    lambda _user: context["minor"]
                ),
            },
        )
        route = scope["should_route_to_adult_model"]

        self.assertFalse(route("alice", "adult-topic", allow_adult_intimacy=False))
        for message in (
            "adult-topic",
            "今天北京天气怎么样？",
            "stop",
            "完全没有关键词的普通聊天",
        ):
            with self.subTest(message=message):
                self.assertTrue(route("alice", message, allow_adult_intimacy=True))

        context["minor"] = True
        self.assertFalse(route("alice", "adult-topic", allow_adult_intimacy=True))
        context["minor"] = False
        context["group"] = True
        self.assertFalse(route("alice", "adult-topic", allow_adult_intimacy=True))
        context["group"] = False

        context["user_enabled"] = False
        self.assertFalse(route("alice", "adult-topic", allow_adult_intimacy=True))
        context["user_enabled"] = True

        context["global_enabled"] = False
        self.assertFalse(route("alice", "adult-topic", allow_adult_intimacy=True))
        context["global_enabled"] = True

        context["model_enabled"] = False
        self.assertFalse(route("alice", "adult-topic", allow_adult_intimacy=True))
        context["model_enabled"] = True

        context["client_available"] = False
        self.assertFalse(route("alice", "adult-topic", allow_adult_intimacy=True))

    def test_adult_model_failure_falls_back_with_the_same_messages_object(self):
        adult_requests = []
        main_requests = []

        def fail_adult(messages, _user):
            adult_requests.append(messages)
            raise RuntimeError("adult backend unavailable")

        scope = load_functions(
            "_is_enabled_config_value",
            "_adult_model_failure_is_policy_rejection",
            "call_adult_chat_api_with_fallback",
            namespace={
                "logger": logging.getLogger("bot-consistency-test"),
                "ADULT_MODEL": "grok-4.5",
                "ADULT_FALLBACK_TO_MAIN": True,
                "_adult_model_config": lambda _key, default="": default,
                "call_adult_chat_api": fail_adult,
                "call_chat_api_with_retry": (
                    lambda messages, _user, is_summary=False: (
                        main_requests.append(messages) or "main-answer"
                    )
                ),
            },
        )
        messages = [
            {"role": "system", "content": "role and history"},
            {"role": "user", "content": "continue"},
        ]

        reply = scope["call_adult_chat_api_with_fallback"](messages, "alice")

        self.assertEqual(reply, "main-answer")
        self.assertIs(adult_requests[0], messages)
        self.assertIs(main_requests[0], messages)

    def test_adult_policy_rejection_never_falls_back_to_the_main_model(self):
        main_requests = []

        class FilteredCompletions:
            def create(self, **_kwargs):
                class Message:
                    content = None

                class Choice:
                    finish_reason = "content_filter"
                    message = Message()

                class Response:
                    choices = [Choice()]

                return Response()

        class FilteredAdultClient:
            class Chat:
                completions = FilteredCompletions()

            chat = Chat()

        scope = load_functions(
            "_is_enabled_config_value",
            "call_adult_chat_api",
            "_adult_model_failure_is_policy_rejection",
            "call_adult_chat_api_with_fallback",
            namespace={
                "logger": logging.getLogger("bot-consistency-test"),
                "adult_client": FilteredAdultClient(),
                "ADULT_BASE_URL": "https://adult.example/v1",
                "ADULT_MODEL": "grok-4.5",
                "ADULT_TEMPERATURE": 0.7,
                "ADULT_MAX_TOKEN": 2000,
                "ADULT_TIMEOUT_SECONDS": 45,
                "ADULT_FALLBACK_TO_MAIN": True,
                "_adult_model_config": lambda _key, default="": default,
                "resolve_model_timeout_seconds": resolve_model_timeout_seconds,
                "_is_base_url_untrusted": lambda _url: False,
                "strip_before_thought_tags": lambda text: text,
                "call_chat_api_with_retry": (
                    lambda messages, _user, is_summary=False: (
                        main_requests.append(messages) or "main-answer"
                    )
                ),
            },
        )
        messages = [{"role": "user", "content": "continue"}]

        with self.assertRaisesRegex(RuntimeError, "内容策略拒绝"):
            scope["call_adult_chat_api_with_fallback"](messages, "alice")

        self.assertEqual(main_requests, [])

    def test_adult_api_uses_its_own_client_and_configured_model(self):
        calls = []
        checked_base_urls = []

        class FakeCompletions:
            def create(self, **kwargs):
                calls.append(kwargs)

                class Message:
                    content = "adult-answer"

                class Choice:
                    message = Message()

                class Response:
                    choices = [Choice()]

                return Response()

        class FakeChat:
            completions = FakeCompletions()

        class FakeAdultClient:
            chat = FakeChat()

        scope = load_functions(
            "_is_enabled_config_value",
            "call_adult_chat_api",
            namespace={
                "adult_client": FakeAdultClient(),
                "ADULT_BASE_URL": "https://adult.example/v1",
                "ADULT_MODEL": "grok-4.5",
                "ADULT_TEMPERATURE": 0.7,
                "ADULT_MAX_TOKEN": 2000,
                "ADULT_TIMEOUT_SECONDS": 45,
                "resolve_model_timeout_seconds": resolve_model_timeout_seconds,
                "_is_base_url_untrusted": (
                    lambda url: checked_base_urls.append(url) or False
                ),
                "strip_before_thought_tags": lambda text: text,
                "_adult_model_config": lambda key, default="": {
                    "ADULT_BASE_URL": "https://adult.example/v1",
                    "ADULT_MODEL": "grok-4.5",
                    "ADULT_TEMPERATURE": 0.7,
                    "ADULT_MAX_TOKEN": 2000,
                    "ADULT_TIMEOUT_SECONDS": 45,
                }.get(key, default),
            },
        )
        messages = [{"role": "user", "content": "continue"}]

        reply = scope["call_adult_chat_api"](messages, "alice")

        self.assertEqual(reply, "adult-answer")
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0]["messages"], messages)
        self.assertEqual(calls[0]["model"], "grok-4.5")
        self.assertEqual(calls[0]["temperature"], 0.7)
        self.assertEqual(calls[0]["max_tokens"], 2000)
        self.assertEqual(calls[0]["timeout"], 180.0)
        self.assertEqual(checked_base_urls, ["https://adult.example/v1"])

    def test_missing_persona_file_still_keeps_minor_system_prompt(self):
        scope, captured = self._response_scope()

        def missing_prompt(_user):
            raise FileNotFoundError("missing persona")

        scope["get_user_prompt"] = missing_prompt
        scope["build_user_info_prompt"] = lambda _user: "\nminor-hard-marker"
        reply = scope["get_deepseek_response"]("hello", "alice")

        self.assertEqual(reply, "answer")
        self.assertIn("你是一个乐于助人的助手。", captured[-1][0]["content"])
        self.assertIn("minor-hard-marker", captured[-1][0]["content"])

    def test_minor_marker_added_during_generation_forces_safe_regeneration(self):
        marker_states = iter((False, True))
        model_calls = []
        rollbacks = []

        def fake_model(message, user_id, **kwargs):
            model_calls.append((message, user_id, dict(kwargs)))
            return "unsafe-reply" if len(model_calls) == 1 else "safe-reply"

        scope = load_functions(
            "_minor_safe_transient_system_message",
            "_regenerate_minor_safe_chat_response",
            "_get_guarded_user_chat_response",
            namespace={
                "_adult_intimacy_user_is_marked_minor": lambda _user: next(marker_states),
                "get_deepseek_response": fake_model,
                "rollback_stored_chat_turn": lambda *args: rollbacks.append(args),
                "logger": logging.getLogger("bot-consistency-test"),
            },
        )

        reply, minor_safe = scope["_get_guarded_user_chat_response"](
            "hello",
            "alice",
            context_message="hello",
            transient_system_message="search-context",
            context_user_id="conversation",
            allow_adult_intimacy=True,
        )

        self.assertEqual(reply, "safe-reply")
        self.assertTrue(minor_safe)
        self.assertEqual(len(model_calls), 2)
        self.assertTrue(model_calls[0][2]["allow_adult_intimacy"])
        self.assertFalse(model_calls[1][2]["allow_adult_intimacy"])
        self.assertIn(
            "后台明确标记为未成年",
            model_calls[1][2]["transient_system_message"],
        )
        self.assertIn(
            "search-context",
            model_calls[1][2]["transient_system_message"],
        )
        self.assertEqual(
            rollbacks,
            [("conversation", "hello", "unsafe-reply")],
        )

    def _concurrent_user_info_store(self, worker_count):
        barrier = threading.Barrier(worker_count)
        store = {"data": {}}

        class TrackingRLock:
            def __init__(self):
                self._lock = threading.RLock()
                self._local = threading.local()

            def acquire(self):
                acquired = self._lock.acquire()
                if acquired:
                    self._local.depth = getattr(self._local, "depth", 0) + 1
                return acquired

            def release(self):
                self._local.depth -= 1
                self._lock.release()

            def held_by_current_thread(self):
                return getattr(self._local, "depth", 0) > 0

            def __enter__(self):
                self.acquire()
                return self

            def __exit__(self, _exc_type, _exc_value, _traceback):
                self.release()

        lock = TrackingRLock()

        def load_data(*_args, **_kwargs):
            # An unlocked read is synchronized onto the same stale snapshot,
            # making a lost-update bug deterministic instead of timing-based.
            if not lock.held_by_current_thread():
                barrier.wait(timeout=5)
            return copy.deepcopy(store["data"])

        def save_data(data):
            with lock:
                store["data"] = copy.deepcopy(data)

        return store, lock, load_data, save_data

    def test_tool_call_does_not_read_or_write_chat_history(self):
        scope, captured = self._response_scope()

        class ForbiddenHistory(dict):
            def __contains__(self, key):
                raise AssertionError(f"tool call touched history: {key}")

            def get(self, key, default=None):
                raise AssertionError(f"tool call touched history: {key}")

        scope["chat_contexts"] = ForbiddenHistory()
        reply = scope["get_deepseek_response"]("classify this", "tool", store_context=False)

        self.assertEqual(reply, "answer")
        self.assertEqual(captured, [[{"role": "user", "content": "classify this"}]])

    def test_transient_search_context_is_not_persisted(self):
        scope, captured = self._response_scope()
        reply = scope["get_deepseek_response"](
            "enhanced request",
            "alice",
            context_message="real user text",
            transient_system_message="temporary search result",
        )

        self.assertEqual(reply, "answer")
        self.assertIn({"role": "system", "content": "temporary search result"}, captured[0])
        self.assertEqual(captured[0][-1], {"role": "user", "content": "enhanced request"})
        self.assertEqual(
            scope["chat_contexts"]["alice"],
            [
                {"role": "user", "content": "real user text"},
                {"role": "assistant", "content": "answer"},
            ],
        )

    def test_group_members_share_conversation_but_keep_personal_prompt_state(self):
        captured_requests = []
        personal_state_calls = []
        active_topic_calls = []
        identity_scope = self._group_identity_scope()
        member_a = identity_scope["build_speaker_id"]("测试群", "小林", is_group_chat=True)
        member_b = identity_scope["build_speaker_id"]("测试群", "小周", is_group_chat=True)

        def record_personal_state(kind):
            def builder(user_id, *args):
                personal_state_calls.append((kind, user_id))
                return ""
            return builder

        scope = load_functions(
            "get_deepseek_response",
            namespace={
                "logger": logging.getLogger("bot-consistency-test"),
                "MAX_GROUPS": 10,
                "queue_lock": threading.RLock(),
                "chat_contexts": {},
                "save_chat_contexts": lambda: None,
                "get_user_prompt": record_personal_state("prompt"),
                "build_favorability_prompt": record_personal_state("favorability"),
                "build_emotion_prompt": record_personal_state("emotion"),
                "build_user_info_prompt": record_personal_state("profile"),
                "build_optional_profile_question_prompt": record_personal_state("profile_question"),
                "build_chat_active_topic_prompt": lambda conversation_id, _message: (
                    active_topic_calls.append(conversation_id) or ""
                ),
                "build_bot_identity_prompt": record_personal_state("identity"),
                "call_chat_api_with_retry": lambda messages, _user, is_summary=False: (
                    captured_requests.append(list(messages)) or f"answer-{len(captured_requests)}"
                ),
            },
        )

        scope["get_deepseek_response"](
            "[发送者: 小林] 第一条",
            member_a,
            context_user_id="测试群",
        )
        scope["get_deepseek_response"](
            "[发送者: 小周] 第二条",
            member_b,
            context_user_id="测试群",
        )

        self.assertEqual(set(scope["chat_contexts"]), {"测试群"})
        self.assertEqual(
            scope["chat_contexts"]["测试群"],
            [
                {"role": "user", "content": "[发送者: 小林] 第一条"},
                {"role": "assistant", "content": "answer-1"},
                {"role": "user", "content": "[发送者: 小周] 第二条"},
                {"role": "assistant", "content": "answer-2"},
            ],
        )
        self.assertIn({"role": "user", "content": "[发送者: 小林] 第一条"}, captured_requests[1])
        self.assertIn(("profile", member_a), personal_state_calls)
        self.assertIn(("profile", member_b), personal_state_calls)
        self.assertEqual(active_topic_calls, ["测试群", "测试群"])

    def test_group_queue_records_do_not_merge_member_identity(self):
        identity_scope = self._group_identity_scope()
        member_a = identity_scope["build_speaker_id"]("测试群", "小林", is_group_chat=True)
        member_b = identity_scope["build_speaker_id"]("测试群", "小周", is_group_chat=True)
        scope = load_functions(
            "_normalize_message_text",
            "_message_fingerprint",
            "_build_relationship_event_id",
            "_normalize_chat_scope_value",
            "_default_relationship_content",
            "_set_queue_message_records",
            "_normalize_queue_message_records",
            "format_queued_user_messages",
            namespace={"hashlib": __import__("hashlib"), "re": __import__("re")},
        )
        records = [
            {"content": "我喜欢咖啡", "speaker_id": member_a, "sender_name": "小林"},
            {"content": "我不喝咖啡", "speaker_id": member_b, "sender_name": "小周"},
        ]
        queue_entry = {}

        scope["_set_queue_message_records"](queue_entry, records, "测试群")
        normalized = scope["_normalize_queue_message_records"](queue_entry, "测试群")
        formatted = scope["format_queued_user_messages"](normalized)

        self.assertEqual(
            [record["speaker_id"] for record in normalized],
            [member_a, member_b],
        )
        self.assertEqual(
            len({record["relationship_event_id"] for record in normalized}),
            2,
        )
        self.assertIn("[发送者: 小林] 我喜欢咖啡", formatted)
        self.assertIn("[发送者: 小周] 我不喝咖啡", formatted)
        self.assertIn("这些人不是同一个用户", formatted)

    def test_reply_queue_waits_only_when_random_delay_is_enabled(self):
        settings = {"enabled": False, "quiet": False}
        scope = load_functions(
            "is_random_reply_delay_enabled",
            "is_user_queue_ready_for_processing",
            namespace={
                "time": time,
                "datetime": datetime,
                "ENABLE_RANDOM_REPLY_DELAY": False,
                "get_dynamic_config": lambda key, default: (
                    settings["enabled"]
                    if key == "ENABLE_RANDOM_REPLY_DELAY" else default
                ),
                "is_reply_quiet_time": lambda _now: settings["quiet"],
            },
        )
        queue_entry = {"messages": ["hello"], "scheduled_reply_time": 110.0}

        self.assertTrue(scope["is_user_queue_ready_for_processing"](queue_entry, 100.0))
        settings["enabled"] = True
        self.assertFalse(scope["is_user_queue_ready_for_processing"](queue_entry, 109.0))
        self.assertTrue(scope["is_user_queue_ready_for_processing"](queue_entry, 110.0))
        settings["enabled"] = False
        settings["quiet"] = True
        self.assertFalse(scope["is_user_queue_ready_for_processing"](queue_entry, 120.0))

    def test_scheduled_reply_timestamp_only_applies_random_delay_when_enabled(self):
        settings = {"enabled": False, "quiet": False}
        random_delay_calls = []
        scope = load_functions(
            "is_random_reply_delay_enabled",
            "calculate_scheduled_reply_timestamp",
            namespace={
                "datetime": datetime,
                "dt": __import__("datetime"),
                "ENABLE_RANDOM_REPLY_DELAY": False,
                "get_dynamic_config": lambda key, default: (
                    settings["enabled"]
                    if key == "ENABLE_RANDOM_REPLY_DELAY" else default
                ),
                "is_reply_quiet_time": lambda _now: settings["quiet"],
                "get_next_reply_window_start": lambda now: now + __import__(
                    "datetime"
                ).timedelta(hours=1),
                "calculate_random_reply_delay_seconds": lambda: (
                    random_delay_calls.append(True) or 11
                ),
            },
        )
        now = datetime(2026, 1, 1, 12, 0, 0)
        quiet_base = now + __import__("datetime").timedelta(hours=1)

        self.assertEqual(
            scope["calculate_scheduled_reply_timestamp"](now),
            now.timestamp(),
        )
        self.assertEqual(random_delay_calls, [])

        settings["enabled"] = True
        self.assertEqual(
            scope["calculate_scheduled_reply_timestamp"](now),
            (now + __import__("datetime").timedelta(seconds=11)).timestamp(),
        )
        self.assertEqual(random_delay_calls, [True])

        settings["quiet"] = True
        self.assertEqual(
            scope["calculate_scheduled_reply_timestamp"](now),
            (quiet_base + __import__("datetime").timedelta(seconds=11)).timestamp(),
        )
        self.assertEqual(len(random_delay_calls), 2)

        settings["enabled"] = False
        self.assertEqual(
            scope["calculate_scheduled_reply_timestamp"](now),
            quiet_base.timestamp(),
        )
        self.assertEqual(len(random_delay_calls), 2)

    def test_cross_midnight_quiet_window_waits_until_next_morning_before_delay(self):
        scope = load_functions(
            "parse_config_time_string",
            "is_reply_quiet_time",
            "get_next_reply_window_start",
            "is_random_reply_delay_enabled",
            "calculate_scheduled_reply_timestamp",
            namespace={
                "datetime": datetime,
                "dt": __import__("datetime"),
                "logger": logging.getLogger("bot-consistency-test"),
                "ENABLE_REPLY_QUIET_TIME": True,
                "REPLY_QUIET_TIME_START": "23:00",
                "REPLY_QUIET_TIME_END": "08:00",
                "ENABLE_RANDOM_REPLY_DELAY": True,
                "get_dynamic_config": lambda key, default: (
                    True if key == "ENABLE_RANDOM_REPLY_DELAY" else default
                ),
                "calculate_random_reply_delay_seconds": lambda: 11,
            },
        )
        now = datetime(2026, 1, 1, 23, 30, 0)
        expected_base = datetime(2026, 1, 2, 8, 0, 0)

        self.assertTrue(scope["is_reply_quiet_time"](now))
        self.assertEqual(scope["get_next_reply_window_start"](now), expected_base)
        self.assertEqual(
            scope["calculate_scheduled_reply_timestamp"](now),
            (expected_base + __import__("datetime").timedelta(seconds=11)).timestamp(),
        )

    def test_scheduler_caps_future_random_deadline_for_dynamic_config_refresh(self):
        settings = {"enabled": True}
        scope = load_functions(
            "is_random_reply_delay_enabled",
            "_next_message_scheduler_wait_seconds",
            namespace={
                "time": time,
                "datetime": datetime,
                "queue_lock": threading.RLock(),
                "user_queues": {
                    "alice": {"messages": ["hello"], "scheduled_reply_time": 700.0}
                },
                "can_send_messages": True,
                "is_sending_message": False,
                "ENABLE_RANDOM_REPLY_DELAY": True,
                "MESSAGE_SCHEDULER_CONFIG_REFRESH_SECONDS": 5.0,
                "MESSAGE_SCHEDULER_QUIET_REFRESH_SECONDS": 30.0,
                "get_dynamic_config": lambda key, default: (
                    settings["enabled"] if key == "ENABLE_RANDOM_REPLY_DELAY" else default
                ),
                "is_reply_quiet_time": lambda _now: False,
                "get_next_reply_window_start": lambda now: now,
            },
        )

        self.assertEqual(
            scope["_next_message_scheduler_wait_seconds"](100.0),
            5.0,
        )

    def test_scheduler_rechecks_refreshed_deadline_before_starting_worker(self):
        scope = load_functions(
            "_schedule_user_message_processing",
            namespace={
                "time": time,
                "threading": threading,
                "logger": logging.getLogger("bot-consistency-test"),
                "queue_lock": threading.RLock(),
                "user_queues": {
                    "alice": {"messages": ["late"], "scheduled_reply_time": time.time() + 60}
                },
                "is_user_queue_ready_for_processing": lambda _entry, _now: False,
                "sanitize_user_id_for_filename": lambda value: str(value),
                "message_processing_slots": threading.BoundedSemaphore(1),
                "message_processing_schedule_lock": threading.Lock(),
                "scheduled_processing_users": set(),
            },
        )

        self.assertFalse(scope["_schedule_user_message_processing"]("alice"))
        self.assertEqual(scope["scheduled_processing_users"], set())
        self.assertTrue(scope["message_processing_slots"].acquire(blocking=False))

    def test_inactive_scanner_schedules_immediately_without_random_delay(self):
        scheduled = []
        waits = []

        def schedule(user_id):
            scheduled.append(user_id)
            return True

        class ScanComplete(Exception):
            pass

        class SingleScanClock:
            @staticmethod
            def time():
                return 1_700_000_000.0

        class SingleScanEvent:
            @staticmethod
            def clear():
                return None

            @staticmethod
            def wait(timeout=None):
                waits.append(timeout)
                raise ScanComplete

        scope = load_functions(
            "is_random_reply_delay_enabled",
            "is_user_queue_ready_for_processing",
            "_next_message_scheduler_wait_seconds",
            "check_inactive_users",
            namespace={
                "time": SingleScanClock,
                "datetime": datetime,
                "queue_lock": threading.RLock(),
                "message_processing_schedule_lock": threading.Lock(),
                "scheduled_processing_users": set(),
                "message_scheduler_event": SingleScanEvent,
                "MESSAGE_SCHEDULER_CONFIG_REFRESH_SECONDS": 1.0,
                "user_queues": {
                    "alice": {
                        "messages": ["hello"],
                        "last_message_time": 1_700_000_000.0,
                        "scheduled_reply_time": 1_700_000_000.0,
                    }
                },
                "can_send_messages": True,
                "is_sending_message": False,
                "ENABLE_RANDOM_REPLY_DELAY": False,
                "get_dynamic_config": lambda key, default: (
                    False if key == "ENABLE_RANDOM_REPLY_DELAY" else default
                ),
                "is_reply_quiet_time": lambda _now: False,
                "get_next_reply_window_start": lambda now: now,
                "_schedule_user_message_processing": schedule,
            },
        )

        with self.assertRaises(ScanComplete):
            scope["check_inactive_users"]()
        self.assertEqual(scheduled, ["alice"])
        self.assertEqual(waits, [0.0])

        scope["_schedule_user_message_processing"] = lambda _user_id: False
        with self.assertRaises(ScanComplete):
            scope["check_inactive_users"]()
        self.assertEqual(waits[-1], 1.0)

    def test_new_message_refreshes_random_reply_aggregation_deadline(self):
        scheduled_inputs = []

        def calculate_deadline(now):
            scheduled_inputs.append(now.timestamp())
            return now.timestamp() + 7

        def set_records(entry, records, _user_id):
            entry["message_records"] = list(records)
            entry["messages"] = [record["content"] for record in records]

        def normalize_records(entry, _user_id):
            return list(entry.get("message_records", []))

        scope = load_functions(
            "create_user_queue_entry",
            "append_message_to_user_queue",
            namespace={
                "time": time,
                "datetime": datetime,
                "queue_lock": threading.RLock(),
                "user_queues": {},
                "active_speaker_ids": set(),
                "logger": logging.getLogger("bot-consistency-test"),
                "_is_valid_chat_target_name": lambda value: bool(value),
                "_normalize_chat_scope_value": lambda value: value,
                "_default_relationship_content": lambda value: value,
                "_build_relationship_event_id": lambda *_args, **_kwargs: "event",
                "_set_queue_message_records": set_records,
                "_normalize_queue_message_records": normalize_records,
                "calculate_scheduled_reply_timestamp": calculate_deadline,
                "save_pending_replies_locked": lambda: None,
                "wake_message_scheduler": lambda: None,
            },
        )

        first_message_time = 1_700_000_000.0
        second_message_time = first_message_time + 4
        self.assertTrue(scope["append_message_to_user_queue"](
            "alice", "one", now_ts=first_message_time
        ))
        self.assertEqual(
            scope["user_queues"]["alice"]["scheduled_reply_time"],
            first_message_time + 7,
        )
        self.assertTrue(scope["append_message_to_user_queue"](
            "alice", "two", now_ts=second_message_time
        ))
        self.assertEqual(
            scope["user_queues"]["alice"]["scheduled_reply_time"],
            second_message_time + 7,
        )
        self.assertEqual(scheduled_inputs, [first_message_time, second_message_time])

    def test_requeued_messages_preserve_relationship_update_marker(self):
        scope = load_functions(
            "_normalize_message_text",
            "_message_fingerprint",
            "_build_relationship_event_id",
            "_normalize_chat_scope_value",
            "_default_relationship_content",
            "_normalize_queue_message_records",
            "_set_queue_message_records",
            "_merge_queue_entry",
            namespace={
                "time": time,
                "hashlib": __import__("hashlib"),
                "re": __import__("re"),
            },
        )
        processed = {
            "messages": ["old"],
            "message_records": [{
                "content": "old",
                "speaker_id": "alice",
                "sender_name": "alice",
                "chat_scope": "private",
                "relationship_state_applied": True,
            }],
            "last_message_time": 1,
            "queued_at": 1,
            "scheduled_reply_time": 1,
        }
        incoming = {
            "messages": ["new"],
            "message_records": [{
                "content": "new",
                "speaker_id": "alice",
                "sender_name": "alice",
                "chat_scope": "private",
                "relationship_state_applied": False,
            }],
            "last_message_time": 2,
            "queued_at": 2,
            "scheduled_reply_time": 2,
        }

        merged = scope["_merge_queue_entry"](incoming, processed, "alice")

        self.assertEqual(
            [record["relationship_state_applied"] for record in merged["message_records"]],
            [False, True],
        )
        self.assertTrue(all(
            record.get("relationship_event_id")
            for record in merged["message_records"]
        ))

    def test_relationship_classifier_uses_authored_text_not_derived_content(self):
        scope = load_functions(
            "_default_relationship_content",
            "_is_generated_relationship_message",
            "_relationship_update_content",
            "_normalize_chat_scope_value",
            "_relationship_record_scope",
        )

        self.assertEqual(scope["_default_relationship_content"]("今晚一起喝奶茶"), "今晚一起喝奶茶")
        self.assertEqual(scope["_default_relationship_content"]("[图片识别结果]: 成人聊天"), "")
        self.assertEqual(
            scope["_default_relationship_content"](
                "用户发送了消息：链接\n其中包含的链接的主要内容摘要如下：成人话题"
            ),
            "",
        )
        self.assertTrue(scope["_is_generated_relationship_message"]("提醒触发：喝水"))
        self.assertFalse(scope["_is_generated_relationship_message"]("我想继续聊"))
        self.assertEqual(
            scope["_relationship_update_content"]({
                "content": "[图片识别结果]: 一张照片",
                "relationship_content": "",
            }),
            "[非文本用户消息]",
        )
        self.assertIsNone(scope["_relationship_update_content"]({
            "content": "提醒触发：喝水",
            "relationship_content": "",
        }))
        self.assertEqual(scope["_relationship_record_scope"]({
            "content": "你好",
            "relationship_content": "你好",
            "chat_scope": "private",
        }), "private")
        self.assertIsNone(scope["_relationship_record_scope"]({
            "content": "提醒触发：喝水",
            "relationship_content": "",
            "chat_scope": "unknown",
        }))

    def test_group_member_memory_paths_are_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            scope = load_functions(
                "_encode_group_speaker_component",
                "_decode_group_speaker_id",
                "build_speaker_id",
                "conversation_id_from_speaker_id",
                "get_prompt_name_for_identity",
                "sanitize_user_id_for_filename",
                "_legacy_sanitize_user_id_for_filename",
                "_copy_legacy_state_file_if_needed",
                "_legacy_identity_candidates",
                "get_core_memory_file_path",
                namespace={
                    "base64": __import__("base64"),
                    "binascii": __import__("binascii"),
                    "hashlib": __import__("hashlib"),
                    "re": __import__("re"),
                    "GROUP_SPEAKER_ID_SEPARATOR": "::",
                    "GROUP_SPEAKER_ID_PREFIX": "group-v1.",
                    "prompt_mapping": {
                        "测试群": "角色1",
                        "A": "Role1",
                        "a": "Role1",
                    },
                    "root_dir": directory,
                    "CORE_MEMORY_DIR": "CoreMemory",
                    "os": os,
                    "shutil": __import__("shutil"),
                    "logger": logging.getLogger("bot-consistency-test"),
                    "legacy_state_migration_lock": threading.Lock(),
                },
            )
            member_a = scope["build_speaker_id"]("测试群", "小林", is_group_chat=True)
            member_b = scope["build_speaker_id"]("测试群", "小周", is_group_chat=True)

            path_a = scope["get_core_memory_file_path"](member_a)
            path_b = scope["get_core_memory_file_path"](member_b)
            uppercase_path = scope["get_core_memory_file_path"]("A")
            lowercase_path = scope["get_core_memory_file_path"]("a")

        self.assertEqual(scope["conversation_id_from_speaker_id"](member_a), "测试群")
        self.assertNotEqual(path_a, path_b)
        self.assertTrue(path_a.endswith("_角色1_core_memory.json"))
        self.assertTrue(path_b.endswith("_角色1_core_memory.json"))
        self.assertNotEqual(uppercase_path.casefold(), lowercase_path.casefold())
        self.assertTrue(uppercase_path.endswith("_Role1_core_memory.json"))
        self.assertTrue(lowercase_path.endswith("_Role1_core_memory.json"))

    def test_group_identity_round_trip_handles_legacy_separator_in_names(self):
        scope = self._group_identity_scope()
        member_id = scope["build_speaker_id"](
            "研发::二组",
            "小林::值班",
            is_group_chat=True,
        )

        self.assertTrue(member_id.startswith("group-v1."))
        self.assertEqual(scope["conversation_id_from_speaker_id"](member_id), "研发::二组")
        self.assertTrue(scope["is_group_speaker_id"](member_id))
        self.assertEqual(scope["conversation_id_from_speaker_id"]("旧群::小林"), "旧群")
        self.assertTrue(scope["is_group_speaker_id"]("旧群::小林"))

    def test_sanitized_filename_hash_prevents_replacement_collisions(self):
        scope = load_functions(
            "sanitize_user_id_for_filename",
            namespace={
                "re": __import__("re"),
                "hashlib": __import__("hashlib"),
            },
        )

        slash_path = scope["sanitize_user_id_for_filename"]("a/b", collision_resistant=True)
        colon_path = scope["sanitize_user_id_for_filename"]("a:b", collision_resistant=True)
        uppercase_path = scope["sanitize_user_id_for_filename"]("A", collision_resistant=True)
        lowercase_path = scope["sanitize_user_id_for_filename"]("a", collision_resistant=True)

        self.assertNotEqual(slash_path, colon_path)
        self.assertNotEqual(uppercase_path.casefold(), lowercase_path.casefold())
        self.assertTrue(slash_path.startswith("a_b_"))
        self.assertTrue(colon_path.startswith("a_b_"))
        self.assertTrue(uppercase_path.startswith("A_"))
        self.assertEqual(lowercase_path, "a")
        self.assertEqual(scope["sanitize_user_id_for_filename"]("ordinary_user"), "ordinary_user")
        self.assertEqual(scope["sanitize_user_id_for_filename"]("Role1"), "Role1")
        self.assertTrue(scope["sanitize_user_id_for_filename"]("CON.txt").startswith("user_CON.txt_"))
        self.assertTrue(scope["sanitize_user_id_for_filename"]("COM1.log").startswith("user_COM1.log_"))

    def test_legacy_identity_memory_files_are_copied_and_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            core_dir = Path(directory) / "CoreMemory"
            temp_dir = Path(directory) / "MemoryTemp"
            core_dir.mkdir()
            temp_dir.mkdir()

            legacy_alice_core = core_dir / "Alice_Role1_core_memory.json"
            legacy_alice_log = temp_dir / "Alice_Role1_log.txt"
            legacy_colon_core = core_dir / "a_b_Role1_core_memory.json"
            legacy_colon_log = temp_dir / "a_b_Role1_log.txt"
            legacy_alice_core.write_text('[{"content":"alice core"}]', encoding="utf-8")
            legacy_alice_log.write_text("alice log\n", encoding="utf-8")
            legacy_colon_core.write_text('[{"content":"colon core"}]', encoding="utf-8")
            legacy_colon_log.write_text("colon log\n", encoding="utf-8")

            scope = load_functions(
                "sanitize_user_id_for_filename",
                "_legacy_sanitize_user_id_for_filename",
                "_copy_legacy_state_file_if_needed",
                "_legacy_identity_candidates",
                "get_memory_temp_log_file_path",
                "get_core_memory_file_path",
                namespace={
                    "hashlib": __import__("hashlib"),
                    "re": __import__("re"),
                    "os": os,
                    "shutil": __import__("shutil"),
                    "logger": logging.getLogger("bot-consistency-test"),
                    "legacy_state_migration_lock": threading.Lock(),
                    "root_dir": directory,
                    "CORE_MEMORY_DIR": "CoreMemory",
                    "MEMORY_TEMP_DIR": "MemoryTemp",
                    "get_prompt_name_for_identity": lambda _user_id: "Role1",
                },
            )

            migrated_alice_core = Path(scope["get_core_memory_file_path"]("Alice"))
            migrated_alice_log = Path(scope["get_memory_temp_log_file_path"]("Alice", "Role1"))
            migrated_colon_core = Path(scope["get_core_memory_file_path"]("a:b"))
            migrated_colon_log = Path(scope["get_memory_temp_log_file_path"]("a:b", "Role1"))

            migrations = (
                (migrated_alice_core, legacy_alice_core),
                (migrated_alice_log, legacy_alice_log),
                (migrated_colon_core, legacy_colon_core),
                (migrated_colon_log, legacy_colon_log),
            )
            for migrated_path, legacy_path in migrations:
                self.assertNotEqual(migrated_path.name.casefold(), legacy_path.name.casefold())
                self.assertEqual(
                    migrated_path.read_text(encoding="utf-8"),
                    legacy_path.read_text(encoding="utf-8"),
                )
                self.assertTrue(legacy_path.exists())

    def test_legacy_separator_group_memory_is_copied_to_encoded_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            core_dir = Path(directory) / "CoreMemory"
            temp_dir = Path(directory) / "MemoryTemp"
            core_dir.mkdir()
            temp_dir.mkdir()
            legacy_core = core_dir / "测试群__小林_角色1_core_memory.json"
            legacy_log = temp_dir / "测试群__小林_角色1_log.txt"
            legacy_core.write_text('[{"content":"group core"}]', encoding="utf-8")
            legacy_log.write_text("group log\n", encoding="utf-8")

            scope = load_functions(
                "_encode_group_speaker_component",
                "_decode_group_speaker_id",
                "build_speaker_id",
                "sanitize_user_id_for_filename",
                "_legacy_sanitize_user_id_for_filename",
                "_copy_legacy_state_file_if_needed",
                "_legacy_identity_candidates",
                "get_memory_temp_log_file_path",
                "get_core_memory_file_path",
                namespace={
                    "base64": __import__("base64"),
                    "binascii": __import__("binascii"),
                    "hashlib": __import__("hashlib"),
                    "re": __import__("re"),
                    "os": os,
                    "shutil": __import__("shutil"),
                    "logger": logging.getLogger("bot-consistency-test"),
                    "legacy_state_migration_lock": threading.Lock(),
                    "root_dir": directory,
                    "CORE_MEMORY_DIR": "CoreMemory",
                    "MEMORY_TEMP_DIR": "MemoryTemp",
                    "GROUP_SPEAKER_ID_SEPARATOR": "::",
                    "GROUP_SPEAKER_ID_PREFIX": "group-v1.",
                    "get_prompt_name_for_identity": lambda _user_id: "角色1",
                },
            )
            member_id = scope["build_speaker_id"]("测试群", "小林", is_group_chat=True)
            migrated_core = Path(scope["get_core_memory_file_path"](member_id))
            migrated_log = Path(scope["get_memory_temp_log_file_path"](member_id, "角色1"))

            self.assertEqual(migrated_core.read_text(encoding="utf-8"), legacy_core.read_text(encoding="utf-8"))
            self.assertEqual(migrated_log.read_text(encoding="utf-8"), legacy_log.read_text(encoding="utf-8"))
            self.assertTrue(legacy_core.exists())
            self.assertTrue(legacy_log.exists())

    def test_renamed_default_prompt_copies_role1_memory_forward(self):
        with tempfile.TemporaryDirectory() as directory:
            core_dir = Path(directory) / "CoreMemory"
            temp_dir = Path(directory) / "MemoryTemp"
            core_dir.mkdir()
            temp_dir.mkdir()
            legacy_core = core_dir / "Alice_角色1_core_memory.json"
            legacy_log = temp_dir / "Alice_角色1_log.txt"
            legacy_core.write_text('[{"content":"legacy core"}]', encoding="utf-8")
            legacy_log.write_text("legacy log\n", encoding="utf-8")

            normalizer = load_functions(
                "normalize_configured_prompt_name",
                namespace={"LEGACY_PROMPT_NAME_ALIASES": {"角色1": "默认角色"}},
            )["normalize_configured_prompt_name"]
            scope = load_functions(
                "sanitize_user_id_for_filename",
                "_legacy_sanitize_user_id_for_filename",
                "_copy_legacy_state_file_if_needed",
                "_legacy_identity_candidates",
                "get_memory_temp_log_file_path",
                "get_core_memory_file_path",
                namespace={
                    "hashlib": __import__("hashlib"),
                    "re": __import__("re"),
                    "os": os,
                    "shutil": __import__("shutil"),
                    "logger": logging.getLogger("bot-consistency-test"),
                    "legacy_state_migration_lock": threading.Lock(),
                    "root_dir": directory,
                    "CORE_MEMORY_DIR": "CoreMemory",
                    "MEMORY_TEMP_DIR": "MemoryTemp",
                    "LEGACY_PROMPT_NAME_ALIASES": {"角色1": "默认角色"},
                    "normalize_configured_prompt_name": normalizer,
                    "get_prompt_name_for_identity": lambda _user_id: "默认角色",
                },
            )

            migrated_core = Path(scope["get_core_memory_file_path"]("Alice"))
            migrated_log = Path(scope["get_memory_temp_log_file_path"]("Alice"))

            self.assertIn("_默认角色_", migrated_core.name)
            self.assertIn("_默认角色_", migrated_log.name)
            self.assertEqual(migrated_core.read_text(encoding="utf-8"), legacy_core.read_text(encoding="utf-8"))
            self.assertEqual(migrated_log.read_text(encoding="utf-8"), legacy_log.read_text(encoding="utf-8"))
            self.assertTrue(legacy_core.exists())
            self.assertTrue(legacy_log.exists())

    def test_group_reminder_setting_keeps_target_and_member_identity_separate(self):
        member_id = self._group_identity_scope()["build_speaker_id"](
            "测试提醒群",
            "小林",
            is_group_chat=True,
        )
        timer_instances = []
        memory_calls = []
        confirmation_calls = []

        class FakeTimer:
            def __init__(self, interval, function, args):
                self.interval = interval
                self.function = function
                self.args = args
                self.started = False
                timer_instances.append(self)

            def start(self):
                self.started = True

        scope = load_functions(
            "try_parse_and_set_reminder",
            "_acquire_message_processing_slot",
            "_release_message_processing_slot",
            namespace={
                "logger": logging.getLogger("bot-consistency-test"),
                "dt": __import__("datetime"),
                "datetime": __import__("datetime").datetime,
                "re": __import__("re"),
                "json": __import__("json"),
                "ENABLE_ASSISTANT_MODEL": True,
                "get_assistant_response": lambda *_args: '{"type":"one-off-short","delay_seconds":120,"message":"喝水"}',
                "get_deepseek_response": lambda *_args, **_kwargs: None,
                "extract_last_json_or_null": lambda value: value,
                "format_delay_approx": lambda *_args: "大约 2 分钟后",
                "send_error_reply": lambda *_args, **_kwargs: self.fail("unexpected reminder error"),
                "Timer": FakeTimer,
                "message_processing_slots": threading.BoundedSemaphore(2),
                "message_processing_slot_state": threading.local(),
                "trigger_reminder": object(),
                "timer_lock": threading.RLock(),
                "next_timer_id": 11,
                "active_timers": {},
                "log_original_message_to_memory": lambda *args: memory_calls.append(args),
                "send_confirmation_reply": lambda *args, **kwargs: confirmation_calls.append((args, kwargs)),
            },
        )

        result = scope["try_parse_and_set_reminder"](
            "2分钟后提醒我喝水",
            "测试提醒群",
            speaker_id=member_id,
            sender_name="小林",
        )

        self.assertTrue(result)
        self.assertEqual(memory_calls, [(member_id, "2分钟后提醒我喝水")])
        self.assertEqual(len(timer_instances), 1)
        self.assertTrue(timer_instances[0].started)
        self.assertEqual(timer_instances[0].args[:3], ["测试提醒群", 11, "喝水"])
        self.assertEqual(timer_instances[0].args[3:], [member_id, "小林"])
        confirmation_args, confirmation_kwargs = confirmation_calls[0]
        self.assertEqual(confirmation_args[0], "测试提醒群")
        self.assertEqual(confirmation_kwargs["speaker_id"], member_id)
        self.assertEqual(confirmation_kwargs["sender_name"], "小林")

    def test_group_persistent_reminder_stores_group_target_and_member_identity(self):
        member_id = self._group_identity_scope()["build_speaker_id"](
            "测试提醒群",
            "小林",
            is_group_chat=True,
        )
        reminders = []
        save_calls = []
        scope = load_functions(
            "try_parse_and_set_reminder",
            "_acquire_message_processing_slot",
            "_release_message_processing_slot",
            namespace={
                "logger": logging.getLogger("bot-consistency-test"),
                "dt": __import__("datetime"),
                "datetime": __import__("datetime").datetime,
                "re": __import__("re"),
                "json": __import__("json"),
                "ENABLE_ASSISTANT_MODEL": True,
                "message_processing_slots": threading.BoundedSemaphore(2),
                "message_processing_slot_state": threading.local(),
                "get_assistant_response": lambda *_args: '{"type":"recurring","time_str":"23:59","message":"休息"}',
                "get_deepseek_response": lambda *_args, **_kwargs: None,
                "extract_last_json_or_null": lambda value: value,
                "send_error_reply": lambda *_args, **_kwargs: self.fail("unexpected reminder error"),
                "recurring_reminder_lock": threading.RLock(),
                "recurring_reminders": reminders,
                "save_recurring_reminders": lambda: save_calls.append(True),
                "log_original_message_to_memory": lambda *_args: None,
                "send_confirmation_reply": lambda *_args, **_kwargs: None,
            },
        )

        result = scope["try_parse_and_set_reminder"](
            "每天23:59提醒我休息",
            "测试提醒群",
            speaker_id=member_id,
            sender_name="小林",
        )

        self.assertTrue(result)
        self.assertEqual(save_calls, [True])
        self.assertEqual(reminders[0]["user_id"], "测试提醒群")
        self.assertEqual(reminders[0]["speaker_id"], member_id)
        self.assertEqual(reminders[0]["sender_name"], "小林")

    def test_group_reminder_confirmation_uses_member_prompt_and_group_context(self):
        member_id = self._group_identity_scope()["build_speaker_id"](
            "测试提醒群",
            "小林",
            is_group_chat=True,
        )
        model_calls = []
        send_calls = []

        def fake_model(message, **kwargs):
            model_calls.append((message, kwargs))
            return "设置好了"

        scope = load_functions(
            "send_confirmation_reply",
            "_send_confirmation_reply_now",
            "get_conversation_turn_lock",
            "_conversation_work_state",
            "_acquire_message_processing_slot",
            "_release_message_processing_slot",
            "_is_current_processing_turn",
            namespace={
                "logger": logging.getLogger("bot-consistency-test"),
                "get_deepseek_response": fake_model,
                "send_reply": lambda *args, **kwargs: send_calls.append((args, kwargs)) or True,
                "threading": threading,
                "message_processing_slots": threading.BoundedSemaphore(2),
                "message_processing_slot_state": threading.local(),
                "message_processing_turn_state": threading.local(),
                "conversation_turn_locks_guard": threading.Lock(),
                "conversation_turn_locks": {},
                "queue_lock": threading.RLock(),
                "user_queues": {},
                "active_processing_users": set(),
            },
        )

        scope["send_confirmation_reply"](
            "测试提醒群",
            "生成确认",
            "[提醒已设置]",
            "备用确认",
            context_message="2分钟后提醒我",
            speaker_id=member_id,
            sender_name="小林",
        )

        self.assertEqual(model_calls[0][1]["user_id"], member_id)
        self.assertEqual(model_calls[0][1]["context_user_id"], "测试提醒群")
        send_args, send_kwargs = send_calls[0]
        self.assertEqual(send_args[:3], ("测试提醒群", "小林", "测试提醒群"))
        self.assertEqual(send_kwargs["memory_user_id"], member_id)

    def test_reminder_confirmation_drains_already_queued_conversation_first(self):
        group_id = "测试提醒群"
        call_order = []
        pending_entry = {"messages": ["userA"]}
        scope = load_functions(
            "get_conversation_turn_lock",
            "_conversation_work_state",
            "process_user_messages",
            "send_confirmation_reply",
            "_send_confirmation_reply_now",
            "_acquire_message_processing_slot",
            "_release_message_processing_slot",
            "_is_current_processing_turn",
            namespace={
                "logger": logging.getLogger("bot-consistency-test"),
                "threading": threading,
                "message_processing_slots": threading.BoundedSemaphore(2),
                "message_processing_slot_state": threading.local(),
                "message_processing_turn_state": threading.local(),
                "conversation_turn_locks_guard": threading.Lock(),
                "conversation_turn_locks": {},
                "queue_lock": threading.RLock(),
                "user_queues": {group_id: pending_entry},
                "active_processing_users": set(),
                "get_deepseek_response": lambda *_args, **_kwargs: call_order.append("confirm-model") or "assistantB",
                "send_reply": lambda *_args, **_kwargs: call_order.append("confirm-send") or True,
            },
        )

        def fake_process_locked(conversation_id):
            self.assertEqual(conversation_id, group_id)
            call_order.extend(("ordinary-model", "ordinary-send"))
            scope["user_queues"].pop(conversation_id, None)

        scope["_process_user_messages_locked"] = fake_process_locked
        scope["send_confirmation_reply"](
            group_id,
            "confirmation prompt B",
            "[reminder B]",
            "fallback B",
            context_message="userB",
            speaker_id="memberB",
            sender_name="memberB",
        )

        self.assertEqual(
            call_order,
            ["ordinary-model", "ordinary-send", "confirm-model", "confirm-send"],
        )

    def test_active_reply_blocks_reminder_confirmation_and_preserves_history_order(self):
        group_id = "测试提醒群"
        api_a_started = threading.Event()
        release_api_a = threading.Event()
        confirmation_lock_attempted = threading.Event()
        confirmation_model_started = threading.Event()
        send_order = []
        thread_errors = []

        class ObservedRLock:
            def __init__(self):
                self._lock = threading.RLock()

            def acquire(self, *args, **kwargs):
                if threading.current_thread().name == "confirmation-B":
                    confirmation_lock_attempted.set()
                return self._lock.acquire(*args, **kwargs)

            def release(self):
                self._lock.release()

            def __enter__(self):
                self.acquire()
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                self.release()
                return False

        observed_lock = ObservedRLock()

        def fake_chat_api(messages, _user_id, is_summary=False):
            current_prompt = messages[-1]["content"]
            if current_prompt == "userA":
                api_a_started.set()
                if not release_api_a.wait(timeout=5):
                    raise AssertionError("test did not release ordinary API call")
                return "assistantA"
            confirmation_model_started.set()
            return "assistantB"

        scope = load_functions(
            "get_conversation_turn_lock",
            "_conversation_work_state",
            "process_user_messages",
            "get_deepseek_response",
            "send_confirmation_reply",
            "_send_confirmation_reply_now",
            "_acquire_message_processing_slot",
            "_release_message_processing_slot",
            "_is_current_processing_turn",
            namespace={
                "logger": logging.getLogger("bot-consistency-test"),
                "threading": threading,
                "message_processing_slots": threading.BoundedSemaphore(2),
                "message_processing_slot_state": threading.local(),
                "message_processing_turn_state": threading.local(),
                "conversation_turn_locks_guard": threading.Lock(),
                "conversation_turn_locks": {group_id: observed_lock},
                "queue_lock": threading.RLock(),
                "user_queues": {},
                "active_processing_users": set(),
                "chat_contexts": {},
                "MAX_GROUPS": 10,
                "save_chat_contexts": lambda: None,
                "get_user_prompt": lambda _user: "role",
                "build_favorability_prompt": lambda _user: "",
                "build_emotion_prompt": lambda _user: "",
                "build_user_info_prompt": lambda _user: "",
                "build_optional_profile_question_prompt": lambda _user, _message: "",
                "build_chat_active_topic_prompt": lambda _user, _message: "",
                "build_bot_identity_prompt": lambda _user, _prompt: "",
                "call_chat_api_with_retry": fake_chat_api,
                "send_reply": lambda _target, _sender, _username, _original, reply, **_kwargs: send_order.append(reply) or True,
            },
        )

        def fake_process_locked(conversation_id):
            scope["active_processing_users"].add(conversation_id)
            try:
                reply = scope["get_deepseek_response"](
                    "userA",
                    user_id="memberA",
                    context_user_id=conversation_id,
                )
                scope["send_reply"](
                    conversation_id,
                    "memberA",
                    conversation_id,
                    "userA",
                    reply,
                    memory_user_id="memberA",
                )
            finally:
                scope["active_processing_users"].discard(conversation_id)

        scope["_process_user_messages_locked"] = fake_process_locked

        def run_and_capture(target):
            try:
                target()
            except Exception as exc:
                thread_errors.append(exc)

        ordinary_thread = threading.Thread(
            target=lambda: run_and_capture(lambda: scope["process_user_messages"](group_id)),
            name="ordinary-A",
        )
        confirmation_thread = threading.Thread(
            target=lambda: run_and_capture(
                lambda: scope["send_confirmation_reply"](
                    group_id,
                    "confirmation prompt B",
                    "[reminder B]",
                    "fallback B",
                    context_message="userB",
                    speaker_id="memberB",
                    sender_name="memberB",
                )
            ),
            name="confirmation-B",
        )

        ordinary_thread.start()
        self.assertTrue(api_a_started.wait(timeout=5))
        confirmation_thread.start()
        self.assertTrue(confirmation_lock_attempted.wait(timeout=5))

        # B has reached the same session lock while A is deliberately paused in
        # its API call. Therefore neither B's model call nor send can have run.
        self.assertFalse(confirmation_model_started.is_set())
        self.assertEqual(send_order, [])

        release_api_a.set()
        ordinary_thread.join(timeout=5)
        confirmation_thread.join(timeout=5)

        self.assertFalse(ordinary_thread.is_alive())
        self.assertFalse(confirmation_thread.is_alive())
        self.assertEqual(thread_errors, [])
        self.assertEqual(send_order, ["assistantA", "assistantB"])
        self.assertEqual(
            scope["chat_contexts"][group_id],
            [
                {"role": "user", "content": "userA"},
                {"role": "assistant", "content": "assistantA"},
                {"role": "user", "content": "userB"},
                {"role": "assistant", "content": "assistantB"},
            ],
        )

    def test_triggered_group_reminder_requeues_for_original_member(self):
        member_id = self._group_identity_scope()["build_speaker_id"](
            "测试提醒群",
            "小林",
            is_group_chat=True,
        )
        queued = []
        scope = load_functions(
            "trigger_reminder",
            namespace={
                "logger": logging.getLogger("bot-consistency-test"),
                "is_sending_message": False,
                "timer_lock": threading.RLock(),
                "active_timers": {("测试提醒群", 7): object()},
                "is_quiet_time": lambda: False,
                "ALLOW_REMINDERS_IN_QUIET_TIME": False,
                "datetime": __import__("datetime").datetime,
                "queue_lock": threading.RLock(),
                "append_message_to_user_queue": lambda *args, **kwargs: queued.append((args, kwargs)),
                "get_dynamic_config": lambda _key, default: default,
                "USE_VOICE_CALL_FOR_REMINDERS": False,
            },
        )

        scope["trigger_reminder"](
            "测试提醒群",
            7,
            "喝水",
            speaker_id=member_id,
            sender_name="小林",
        )

        queued_args, queued_kwargs = queued[0]
        self.assertEqual(queued_args[0], "测试提醒群")
        self.assertEqual(queued_kwargs["username"], "测试提醒群")
        self.assertEqual(queued_kwargs["speaker_id"], member_id)
        self.assertEqual(queued_kwargs["sender_name"], "小林")

    def test_active_topic_requires_low_content_without_question(self):
        scope = load_functions(
            "has_explicit_user_question",
            "should_inject_chat_active_topic",
            namespace={
                "re": __import__("re"),
                "get_dynamic_config": lambda _key, default: default,
                "ENABLE_CHAT_ACTIVE_TOPICS": True,
                "CHAT_ACTIVE_TOPIC_MIN_TURNS": 2,
                "load_chat_active_topic_state": lambda: {"alice": {"turns_since_topic": 99}},
                "normalize_chat_topic_state": lambda record: {
                    "turns_since_topic": int(record.get("turns_since_topic", 0))
                },
                "is_low_content_message": lambda message: message in {"嗯", "你觉得呢？"},
            },
        )

        self.assertEqual(scope["should_inject_chat_active_topic"]("alice", "嗯")[0], True)
        self.assertEqual(scope["should_inject_chat_active_topic"]("alice", "你觉得呢？")[0], False)
        self.assertEqual(scope["should_inject_chat_active_topic"]("alice", "继续讲刚才的方案")[0], False)

    def test_no_sent_text_removes_unsent_assistant_reply(self):
        saved = []
        context = {
            "alice": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "full reply"},
            ]
        }
        scope = load_functions(
            "replace_stored_assistant_reply_with_sent_parts",
            namespace={
                "queue_lock": threading.RLock(),
                "chat_contexts": context,
                "save_chat_contexts": lambda: saved.append(True),
                "logger": logging.getLogger("bot-consistency-test"),
            },
        )

        changed = scope["replace_stored_assistant_reply_with_sent_parts"](
            "alice", "full reply", []
        )
        self.assertTrue(changed)
        self.assertEqual(context["alice"], [{"role": "user", "content": "hello"}])
        self.assertEqual(saved, [True])

    def test_memory_snapshot_restore_preserves_new_appends(self):
        scope = load_functions(
            "_claim_memory_log_snapshot",
            "_restore_memory_log_snapshot",
            namespace={
                "os": os,
                "threading": threading,
                "time": time,
                "memory_log_lock": threading.RLock(),
                "logger": logging.getLogger("bot-consistency-test"),
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            log_file = Path(directory) / "memory.log"
            log_file.write_text("old\n", encoding="utf-8")
            snapshot = scope["_claim_memory_log_snapshot"](str(log_file))
            with log_file.open("a", encoding="utf-8") as stream:
                stream.write("new\n")

            scope["_restore_memory_log_snapshot"](str(log_file), snapshot)
            self.assertEqual(log_file.read_text(encoding="utf-8"), "old\nnew\n")
            self.assertFalse(Path(snapshot).exists())

    def test_dynamic_config_cache_hit_does_not_reopen_file(self):
        open_calls = []
        scope = self._dynamic_config_scope(open_calls)
        with tempfile.TemporaryDirectory() as directory:
            config_file = Path(directory) / "config.py"
            config_file.write_text("VALUE = 1\n", encoding="utf-8")

            first = scope["_load_dynamic_config_values"](str(config_file))
            second = scope["_load_dynamic_config_values"](str(config_file))

        self.assertIs(first, second)
        self.assertEqual(first, {"VALUE": 1})
        self.assertEqual(len(open_calls), 1)

    def test_dynamic_config_cache_invalidates_after_file_change(self):
        open_calls = []
        scope = self._dynamic_config_scope(open_calls)
        with tempfile.TemporaryDirectory() as directory:
            config_file = Path(directory) / "config.py"
            config_file.write_text("VALUE = 1\n", encoding="utf-8")
            first = scope["_load_dynamic_config_values"](str(config_file))

            # A different size guarantees a new cache signature even on coarse filesystems.
            config_file.write_text("VALUE = 'updated value'\n", encoding="utf-8")
            updated = scope["_load_dynamic_config_values"](str(config_file))

        self.assertEqual(first["VALUE"], 1)
        self.assertEqual(updated["VALUE"], "updated value")
        self.assertIsNot(first, updated)
        self.assertEqual(len(open_calls), 2)

    def test_dynamic_config_ignores_non_literals_without_executing_them(self):
        scope = self._dynamic_config_scope()
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "non_literal_was_executed"
            config_file = Path(directory) / "config.py"
            config_file.write_text(
                "SAFE = {'enabled': True}\n"
                f"DANGEROUS = __import__('pathlib').Path({str(marker)!r}).touch()\n"
                "COMPUTED = 1 + 2\n",
                encoding="utf-8",
            )

            values = scope["_load_dynamic_config_values"](str(config_file))

        self.assertEqual(values, {"SAFE": {"enabled": True}})
        self.assertFalse(marker.exists())

    def test_local_online_search_decision_has_three_states(self):
        scope = load_functions(
            "_local_online_search_decision",
            namespace={"re": __import__("re")},
        )
        decide = scope["_local_online_search_decision"]

        self.assertIs(decide("给我讲讲 Python 装饰器"), False)
        self.assertEqual(decide("帮我查一下今天北京天气"), "帮我查一下今天北京天气")
        self.assertIsNone(decide("最近有什么值得看的新电影吗"))

    def test_prompt_memory_injection_is_capped_at_twelve_items(self):
        scope = load_functions("format_json_memories_for_prompt")
        memories = [
            {
                "timestamp": f"2026-07-{index + 1:02d} 12:00",
                "summary": f"memory-{index}",
                "importance": (index % 5) + 1,
            }
            for index in range(20)
        ]

        prompt = scope["format_json_memories_for_prompt"](memories)

        self.assertEqual(prompt.count("## 记忆片段 ["), 12)
        self.assertEqual(prompt.count("**摘要**:"), 12)

    def test_send_reply_first_text_has_only_short_fixed_stabilization_wait(self):
        scope, fake_time, fake_random = self._send_reply_scope(["first part"])

        sent = scope["send_reply"]("alice", "Alice", "alice", "hello", "reply")

        self.assertTrue(sent)
        self.assertEqual(fake_time.sleep_calls, [0.05])
        self.assertEqual(fake_random.uniform_calls, [])

    def test_send_reply_text_segment_delay_is_capped_at_one_point_five_seconds(self):
        scope, fake_time, _ = self._send_reply_scope(
            ["first part", "second part"],
            typing_speed=100.0,
            random_typing_delay=100.0,
        )

        sent = scope["send_reply"]("alice", "Alice", "alice", "hello", "reply")

        self.assertTrue(sent)
        self.assertEqual(fake_time.sleep_calls[0], 0.05)
        self.assertEqual(fake_time.sleep_calls[1], 1.5)
        self.assertTrue(all(delay <= 1.5 for delay in fake_time.sleep_calls))

    def test_send_reply_stops_remaining_parts_after_minor_marker_appears(self):
        scope, _fake_time, _fake_random = self._send_reply_scope(
            ["first part", "second part"]
        )
        marker_states = iter((False, False, True))
        sent_parts = []
        reconciled = []
        scope["_adult_intimacy_user_is_marked_minor"] = (
            lambda _user: next(marker_states)
        )
        scope["wx_send_text_to_chat"] = (
            lambda _user, content: sent_parts.append(content) or True
        )
        scope["replace_stored_assistant_reply_with_sent_parts"] = (
            lambda *args: reconciled.append(args)
        )

        sent = scope["send_reply"](
            "alice",
            "Alice",
            "alice",
            "hello",
            "first part$second part",
            raw_reply_for_rollback="raw reply",
            adult_guard_user_id="alice",
        )

        self.assertFalse(sent)
        self.assertEqual(sent_parts, ["first part"])
        self.assertEqual(reconciled[-1], ("alice", "raw reply", ["first part"]))

    def test_minor_marker_save_is_serialized_with_each_outbound_action(self):
        scope, _fake_time, _fake_random = self._send_reply_scope(["only part"])
        marker = {"is_minor": False}
        send_entered = threading.Event()
        release_send = threading.Event()
        marker_saved = threading.Event()
        send_results = []

        scope["_adult_intimacy_user_is_marked_minor"] = (
            lambda _user: marker["is_minor"]
        )

        def fake_send(_user, _content):
            send_entered.set()
            self.assertTrue(release_send.wait(timeout=3))
            return True

        scope["wx_send_text_to_chat"] = fake_send

        def send_worker():
            send_results.append(scope["send_reply"](
                "alice",
                "Alice",
                "alice",
                "hello",
                "only part",
                raw_reply_for_rollback="raw reply",
                adult_guard_user_id="alice",
            ))

        def marker_worker():
            self.assertTrue(send_entered.wait(timeout=3))
            with scope["user_info_file_lock"]:
                marker["is_minor"] = True
            marker_saved.set()

        sender_thread = threading.Thread(target=send_worker)
        marker_thread = threading.Thread(target=marker_worker)
        sender_thread.start()
        marker_thread.start()

        self.assertTrue(send_entered.wait(timeout=3))
        self.assertFalse(marker_saved.wait(timeout=0.1))
        release_send.set()
        sender_thread.join(timeout=3)
        marker_thread.join(timeout=3)

        self.assertFalse(sender_thread.is_alive())
        self.assertFalse(marker_thread.is_alive())
        self.assertEqual(send_results, [True])
        self.assertTrue(marker_saved.is_set())

    def test_openai_clients_use_bounded_timeouts_and_retries(self):
        expected = {
            "client": {
                "timeout": "Name(id='main_model_timeout_seconds', ctx=Load())",
                "max_retries": 0,
            },
            "adult_client": {
                "timeout": "Name(id='timeout', ctx=Load())",
                "max_retries": 0,
            },
            "assistant_client": {"timeout": 30.0, "max_retries": 0},
            "online_client": {"timeout": 45.0, "max_retries": 1},
        }

        for client_name, expected_limits in expected.items():
            with self.subTest(client=client_name):
                kwargs = find_openai_client_kwargs(client_name)
                self.assertEqual(
                    {key: kwargs[key] for key in expected_limits},
                    expected_limits,
                )

    def test_model_timeout_policy_preserves_normal_and_extends_grok(self):
        self.assertEqual(resolve_model_timeout_seconds("gpt-5.4", 45), 45.0)
        self.assertEqual(resolve_model_timeout_seconds("GROK-4.5", 45), 180.0)
        self.assertEqual(resolve_model_timeout_seconds("grok-4.5", 240), 240.0)

    def test_main_chat_request_uses_resolved_client_timeout(self):
        function = next(
            node for node in BOT_TREE.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "call_chat_api_with_retry"
        )
        create_call = next(
            node for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create"
        )
        timeout_keyword = next(
            keyword for keyword in create_call.keywords
            if keyword.arg == "timeout"
        )
        self.assertEqual(
            ast.dump(timeout_keyword.value),
            "Name(id='main_model_timeout_seconds', ctx=Load())",
        )

    def test_adult_client_cache_signature_tracks_model_and_timeout(self):
        settings = {
            "ENABLE_ADULT_MODEL": True,
            "ADULT_API_KEY": "test-adult",
            "ADULT_BASE_URL": "https://adult.example/v1",
            "ADULT_MODEL": "grok-4.5",
            "ADULT_TIMEOUT_SECONDS": 45,
        }
        created = []

        def create_client(**kwargs):
            client_instance = object()
            created.append((client_instance, kwargs))
            return client_instance

        scope = load_functions(
            "_is_enabled_config_value",
            "_adult_model_config",
            "get_runtime_adult_client",
            namespace={
                "adult_client": None,
                "adult_client_config_signature": None,
                "ADULT_BASE_URL": settings["ADULT_BASE_URL"],
                "ADULT_MODEL": settings["ADULT_MODEL"],
                "ADULT_TIMEOUT_SECONDS": settings["ADULT_TIMEOUT_SECONDS"],
                "get_dynamic_config": lambda key, default: settings.get(key, default),
                "resolve_model_timeout_seconds": resolve_model_timeout_seconds,
                "OpenAI": create_client,
                "logger": logging.getLogger("bot-consistency-test"),
            },
        )

        first = scope["get_runtime_adult_client"]()
        self.assertIs(scope["get_runtime_adult_client"](), first)
        self.assertEqual(created[0][1]["timeout"], 180.0)
        self.assertEqual(len(created), 1)

        settings["ADULT_MODEL"] = "grok-4.6"
        second = scope["get_runtime_adult_client"]()
        self.assertIsNot(second, first)
        self.assertEqual(len(created), 2)

        settings["ADULT_TIMEOUT_SECONDS"] = 240
        third = scope["get_runtime_adult_client"]()
        self.assertIsNot(third, second)
        self.assertEqual(created[2][1]["timeout"], 240.0)
        self.assertEqual(
            scope["adult_client_config_signature"][-2:],
            ("grok-4.6", 240.0),
        )

    def test_adult_client_and_call_use_only_dedicated_credentials(self):
        initializer = find_openai_client_kwargs("adult_client")
        self.assertEqual(
            initializer["api_key"],
            "Name(id='api_key', ctx=Load())",
        )
        self.assertEqual(
            initializer["base_url"],
            "Name(id='ADULT_BASE_URL', ctx=Load())",
        )

        adult_call = next(
            node for node in BOT_TREE.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "call_adult_chat_api"
        )
        referenced_names = {
            node.id for node in ast.walk(adult_call) if isinstance(node, ast.Name)
        }
        self.assertIn("ADULT_BASE_URL", referenced_names)
        self.assertNotIn("DEEPSEEK_BASE_URL", referenced_names)
        self.assertNotIn("DEEPSEEK_API_KEY", referenced_names)
        self.assertNotIn("ADULT_REUSE_MAIN_CREDENTIALS", referenced_names)

    def test_adult_model_config_defaults_are_optional_and_bounded(self):
        config_path = BOT_PATH.parent / "config.py"
        config_tree = ast.parse(config_path.read_text(encoding="utf-8"))
        values = {}
        for node in config_tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            try:
                values[target.id] = ast.literal_eval(node.value)
            except (TypeError, ValueError):
                continue

        expected = {
            "ENABLE_ADULT_MODEL": False,
            "ADULT_BASE_URL": "https://ai.qiqiapi.top/v1",
            "ADULT_API_KEY": "",
            "ADULT_MODEL": "grok-4.5",
            "ADULT_TEMPERATURE": 0.7,
            "ADULT_MAX_TOKEN": 2000,
            "ADULT_TIMEOUT_SECONDS": 180,
            "ADULT_FALLBACK_TO_MAIN": False,
            "MODEL_REQUEST_TIMEOUT_SECONDS": 180,
        }
        self.assertEqual(
            {key: values.get(key) for key in expected},
            expected,
        )
        self.assertNotIn("ADULT_REUSE_MAIN_CREDENTIALS", values)

    def test_image_recognition_request_has_bounded_connect_and_read_timeouts(self):
        function = next(
            node for node in BOT_TREE.body
            if isinstance(node, ast.FunctionDef) and node.name == "recognize_image_with_moonshot"
        )
        post_calls = [
            node for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "requests"
            and node.func.attr == "post"
        ]

        self.assertEqual(len(post_calls), 1)
        timeout_keyword = next(
            keyword for keyword in post_calls[0].keywords if keyword.arg == "timeout"
        )
        self.assertEqual(ast.literal_eval(timeout_keyword.value), (8, 45))

    def test_send_path_has_real_mutex_and_no_timeout_bypass(self):
        send_node = next(
            node for node in BOT_TREE.body
            if isinstance(node, ast.FunctionDef) and node.name == "send_reply"
        )
        send_source = ast.get_source_segment(BOT_SOURCE, send_node)
        self.assertIn("wx_ui_lock.acquire()", send_source)
        self.assertIn("wx_ui_lock.release()", send_source)
        self.assertNotIn("MAX_WAIT_SENDING", send_source)

    def test_user_info_lock_is_reentrant(self):
        assignment = next(
            node for node in BOT_TREE.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "user_info_lock"
                for target in node.targets
            )
        )
        self.assertIsInstance(assignment.value, ast.Call)
        self.assertIsInstance(assignment.value.func, ast.Attribute)
        self.assertEqual(assignment.value.func.attr, "RLock")

    def test_stable_user_info_signal_gate_accepts_disclosures_only(self):
        scope = load_functions(
            "_has_stable_user_info_signal",
            namespace={"re": __import__("re")},
        )
        has_signal = scope["_has_stable_user_info_signal"]

        for message in (
            "我叫小林",
            "以后叫我阿七",
            "我今年20岁",
            "我住在杭州",
            "我来自成都",
            "我喜欢羽毛球",
            "我不喜欢香菜",
            "我在腾讯工作",
            "我读计算机专业",
            "我是程序员",
        ):
            with self.subTest(message=message):
                self.assertTrue(has_signal(message))

        for message in (
            "你好呀",
            "Python 的 GIL 是什么",
            "今天天气怎么样",
            "我在干嘛",
            "你叫什么名字",
            "我喜欢什么",
            "叫我干嘛",
        ):
            with self.subTest(message=message):
                self.assertFalse(has_signal(message))

    def test_user_info_async_gate_skips_without_signal_and_deduplicates(self):
        update_calls = []
        thread_instances = []

        class ImmediateThread:
            def __init__(self, target, name=None):
                self.target = target
                self.name = name
                self.daemon = False
                thread_instances.append(self)

            def start(self):
                self.target()

        class FakeThreading:
            Thread = ImmediateThread

        scope = load_functions(
            "_has_stable_user_info_signal",
            "update_user_info_from_message_async",
            namespace={
                "re": __import__("re"),
                "hashlib": __import__("hashlib"),
                "time": time,
                "threading": FakeThreading,
                "logger": logging.getLogger("bot-consistency-test"),
                "sanitize_user_id_for_filename": lambda value: str(value),
                "update_user_info_from_message": lambda user, message: update_calls.append((user, message)),
                "user_info_worker_slots": threading.BoundedSemaphore(2),
                "user_info_extraction_state_lock": threading.Lock(),
                "active_user_info_extractions": set(),
                "recent_user_info_extractions": {},
                "USER_INFO_EXTRACTION_COOLDOWN_SECONDS": 120,
            },
        )
        schedule = scope["update_user_info_from_message_async"]

        self.assertFalse(schedule("alice", "你好，今天吃什么？"))
        self.assertEqual(thread_instances, [])
        self.assertEqual(update_calls, [])

        self.assertTrue(schedule("alice", "我叫小林"))
        self.assertFalse(schedule("alice", "我住在杭州"))
        self.assertEqual(len(thread_instances), 1)
        self.assertEqual(update_calls, [("alice", "我叫小林")])
        self.assertFalse(scope["active_user_info_extractions"])

    def test_message_processing_scheduler_runs_two_sessions_and_releases_slots(self):
        started = {user: threading.Event() for user in ("alice", "bob", "charlie")}
        release = {user: threading.Event() for user in ("alice", "bob", "charlie")}

        def process_user(user):
            started[user].set()
            self.assertTrue(release[user].wait(timeout=5), f"未释放会话 {user}")

        scope = load_functions(
            "_schedule_user_message_processing",
            "_adopt_message_processing_slot",
            "_release_message_processing_slot",
            namespace={
                "str": str,
                "threading": threading,
                "logger": logging.getLogger("bot-consistency-test"),
                "sanitize_user_id_for_filename": lambda value: str(value),
                "process_user_messages": process_user,
                "message_processing_slots": threading.BoundedSemaphore(2),
                "message_processing_slot_state": threading.local(),
                "message_processing_schedule_lock": threading.Lock(),
                "scheduled_processing_users": set(),
            },
        )
        schedule = scope["_schedule_user_message_processing"]

        self.assertTrue(schedule("alice"))
        self.assertTrue(schedule("bob"))
        self.assertTrue(started["alice"].wait(timeout=2))
        self.assertTrue(started["bob"].wait(timeout=2))
        self.assertFalse(schedule("charlie"))
        self.assertFalse(schedule("alice"))

        release["alice"].set()
        deadline = time.time() + 2
        while "alice" in scope["scheduled_processing_users"] and time.time() < deadline:
            time.sleep(0.01)
        self.assertNotIn("alice", scope["scheduled_processing_users"])
        self.assertTrue(schedule("charlie"))
        self.assertTrue(started["charlie"].wait(timeout=2))

        release["bob"].set()
        release["charlie"].set()
        deadline = time.time() + 2
        while scope["scheduled_processing_users"] and time.time() < deadline:
            time.sleep(0.01)
        self.assertFalse(scope["scheduled_processing_users"])

    def test_reminder_confirmation_waits_for_shared_processing_slot(self):
        slots = threading.BoundedSemaphore(2)
        slot_state = threading.local()
        confirmation_entered = threading.Event()
        model_started = threading.Event()
        send_started = threading.Event()
        thread_errors = []

        scope = load_functions(
            "send_confirmation_reply",
            "_send_confirmation_reply_now",
            "get_conversation_turn_lock",
            "_conversation_work_state",
            "_acquire_message_processing_slot",
            "_release_message_processing_slot",
            namespace={
                "logger": logging.getLogger("bot-consistency-test"),
                "threading": threading,
                "message_processing_slots": slots,
                "message_processing_slot_state": slot_state,
                "conversation_turn_locks_guard": threading.Lock(),
                "conversation_turn_locks": {},
                "queue_lock": threading.RLock(),
                "user_queues": {},
                "active_processing_users": set(),
                "get_deepseek_response": lambda *_args, **_kwargs: model_started.set() or "确认好了",
                "send_reply": lambda *_args, **_kwargs: send_started.set() or True,
            },
        )

        self.assertTrue(slots.acquire(blocking=False))
        self.assertTrue(slots.acquire(blocking=False))

        def run_confirmation():
            try:
                confirmation_entered.set()
                scope["send_confirmation_reply"](
                    "alice",
                    "confirmation prompt",
                    "[reminder]",
                    "fallback",
                    context_message="提醒我",
                    speaker_id="alice",
                    sender_name="Alice",
                )
            except Exception as exc:
                thread_errors.append(exc)

        confirmation_thread = threading.Thread(target=run_confirmation, name="confirmation-slot-test")
        confirmation_thread.start()
        self.assertTrue(confirmation_entered.wait(timeout=2))
        self.assertFalse(model_started.wait(timeout=0.2))
        self.assertTrue(confirmation_thread.is_alive())

        slots.release()
        confirmation_thread.join(timeout=3)
        self.assertFalse(confirmation_thread.is_alive())
        self.assertEqual(thread_errors, [])
        self.assertTrue(model_started.is_set())
        self.assertTrue(send_started.is_set())

        # One original slot is still held; return it and verify confirmation
        # released the slot it borrowed.
        slots.release()
        self.assertTrue(slots.acquire(blocking=False))
        self.assertTrue(slots.acquire(blocking=False))
        slots.release()
        slots.release()

    def test_reminder_confirmation_reuses_slot_owned_by_current_worker(self):
        slots = threading.BoundedSemaphore(1)
        slot_state = threading.local()
        model_started = threading.Event()

        scope = load_functions(
            "send_confirmation_reply",
            "_send_confirmation_reply_now",
            "get_conversation_turn_lock",
            "_conversation_work_state",
            "_acquire_message_processing_slot",
            "_release_message_processing_slot",
            namespace={
                "logger": logging.getLogger("bot-consistency-test"),
                "threading": threading,
                "message_processing_slots": slots,
                "message_processing_slot_state": slot_state,
                "conversation_turn_locks_guard": threading.Lock(),
                "conversation_turn_locks": {},
                "queue_lock": threading.RLock(),
                "user_queues": {},
                "active_processing_users": set(),
                "get_deepseek_response": lambda *_args, **_kwargs: model_started.set() or "确认好了",
                "send_reply": lambda *_args, **_kwargs: True,
            },
        )

        self.assertTrue(slots.acquire(blocking=False))
        slot_state.held = True
        scope["send_confirmation_reply"](
            "alice",
            "confirmation prompt",
            "[reminder]",
            "fallback",
            context_message="提醒我",
            speaker_id="alice",
            sender_name="Alice",
        )

        self.assertTrue(model_started.is_set())
        slot_state.held = False
        slots.release()

    def test_reminder_confirmation_completes_inside_active_processing_turn(self):
        slots = threading.BoundedSemaphore(1)
        slot_state = threading.local()
        turn_state = threading.local()
        model_calls = []
        send_calls = []

        scope = load_functions(
            "send_confirmation_reply",
            "_send_confirmation_reply_now",
            "get_conversation_turn_lock",
            "_conversation_work_state",
            "_acquire_message_processing_slot",
            "_release_message_processing_slot",
            "_mark_current_processing_turn",
            "_unmark_current_processing_turn",
            "_is_current_processing_turn",
            namespace={
                "logger": logging.getLogger("bot-consistency-test"),
                "threading": threading,
                "message_processing_slots": slots,
                "message_processing_slot_state": slot_state,
                "message_processing_turn_state": turn_state,
                "conversation_turn_locks_guard": threading.Lock(),
                "conversation_turn_locks": {},
                "queue_lock": threading.RLock(),
                "user_queues": {"alice": {"messages": ["new message"]}},
                "active_processing_users": {"alice"},
                "process_user_messages": lambda *_args: self.fail("current turn must not recurse"),
                "get_deepseek_response": lambda *_args, **_kwargs: model_calls.append(True) or "确认好了",
                "send_reply": lambda *_args, **_kwargs: send_calls.append(True) or True,
            },
        )

        self.assertTrue(slots.acquire(blocking=False))
        slot_state.held = True
        scope["_mark_current_processing_turn"]("alice")
        scope["send_confirmation_reply"](
            "alice",
            "confirmation prompt",
            "[reminder]",
            "fallback",
            context_message="提醒我",
            speaker_id="alice",
            sender_name="Alice",
        )

        self.assertEqual(model_calls, [True])
        self.assertEqual(send_calls, [True])
        self.assertEqual(scope["user_queues"]["alice"]["messages"], ["new message"])
        scope["_unmark_current_processing_turn"]("alice")
        slot_state.held = False
        slots.release()

    def test_reminder_parser_waits_for_shared_processing_slot(self):
        slots = threading.BoundedSemaphore(2)
        slot_state = threading.local()
        parser_entered = threading.Event()
        model_started = threading.Event()
        result = []
        thread_errors = []

        def fake_assistant(*_args):
            model_started.set()
            return "null"

        scope = load_functions(
            "try_parse_and_set_reminder",
            "_acquire_message_processing_slot",
            "_release_message_processing_slot",
            namespace={
                "logger": logging.getLogger("bot-consistency-test"),
                "dt": __import__("datetime"),
                "datetime": __import__("datetime").datetime,
                "re": __import__("re"),
                "json": __import__("json"),
                "ENABLE_ASSISTANT_MODEL": True,
                "get_assistant_response": fake_assistant,
                "get_deepseek_response": lambda *_args, **_kwargs: None,
                "extract_last_json_or_null": lambda value: value,
                "message_processing_slots": slots,
                "message_processing_slot_state": slot_state,
            },
        )

        self.assertTrue(slots.acquire(blocking=False))
        self.assertTrue(slots.acquire(blocking=False))

        def run_parser():
            try:
                parser_entered.set()
                result.append(scope["try_parse_and_set_reminder"]("提醒我喝水", "alice"))
            except Exception as exc:
                thread_errors.append(exc)

        parser_thread = threading.Thread(target=run_parser, name="reminder-parser-slot-test")
        parser_thread.start()
        self.assertTrue(parser_entered.wait(timeout=2))
        self.assertFalse(model_started.wait(timeout=0.2))

        slots.release()
        parser_thread.join(timeout=3)
        self.assertFalse(parser_thread.is_alive())
        self.assertEqual(thread_errors, [])
        self.assertEqual(result, [False])
        self.assertTrue(model_started.is_set())

        slots.release()
        self.assertTrue(slots.acquire(blocking=False))
        self.assertTrue(slots.acquire(blocking=False))
        slots.release()
        slots.release()

    def test_concurrent_user_info_extractions_preserve_every_user(self):
        worker_count = 8
        store, lock, load_data, save_data = self._concurrent_user_info_store(worker_count)

        scope = load_functions(
            "create_default_user_info_record",
            "normalize_user_info_record",
            "merge_unique_list",
            "update_user_info_from_message",
            namespace={
                "assistant_client": None,
                "datetime": __import__("datetime").datetime,
                "ENABLE_ASSISTANT_MODEL": False,
                "get_deepseek_response": lambda *_args, **_kwargs: json.dumps({"likes": ["coffee"]}),
                "get_user_info_record": lambda _user_id: {},
                "json": json,
                "load_user_info_data": load_data,
                "logger": logging.getLogger("bot-consistency-test"),
                "re": __import__("re"),
                "save_user_info_data": save_data,
                "user_info_lock": lock,
                "user_info_file_lock": threading.RLock(),
            },
        )

        threads = [
            threading.Thread(
                target=scope["update_user_info_from_message"],
                args=(f"user-{index}", f"message-{index}"),
            )
            for index in range(worker_count)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(set(store["data"]), {f"user-{index}" for index in range(worker_count)})

    def test_concurrent_profile_turn_updates_preserve_every_user(self):
        worker_count = 8
        store, lock, load_data, save_data = self._concurrent_user_info_store(worker_count)

        scope = load_functions(
            "create_default_user_info_record",
            "normalize_user_info_record",
            "merge_unique_list",
            "mark_profile_question_turn",
            namespace={
                "datetime": __import__("datetime").datetime,
                "load_user_info_data": load_data,
                "save_user_info_data": save_data,
                "user_info_lock": lock,
                "user_info_file_lock": threading.RLock(),
            },
        )

        threads = [
            threading.Thread(
                target=scope["mark_profile_question_turn"],
                args=(f"user-{index}", True, "likes"),
            )
            for index in range(worker_count)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(set(store["data"]), {f"user-{index}" for index in range(worker_count)})
        self.assertTrue(all(record["asked_fields"] == ["likes"] for record in store["data"].values()))

    def test_profile_turn_counter_at_cap_skips_unchanged_file_write(self):
        data = {"alice": {"turns_since_profile_question": 999}}
        saved = []
        scope = load_functions(
            "create_default_user_info_record",
            "normalize_user_info_record",
            "merge_unique_list",
            "mark_profile_question_turn",
            namespace={
                "datetime": __import__("datetime").datetime,
                "load_user_info_data": lambda **_kwargs: copy.deepcopy(data),
                "save_user_info_data": lambda value: saved.append(copy.deepcopy(value)),
                "user_info_lock": threading.RLock(),
                "user_info_file_lock": threading.RLock(),
            },
        )

        self.assertFalse(scope["mark_profile_question_turn"]("alice", asked=False))
        self.assertEqual(saved, [])

        data["alice"]["turns_since_profile_question"] = 998
        self.assertTrue(scope["mark_profile_question_turn"]("alice", asked=False))
        self.assertEqual(saved[-1]["alice"]["turns_since_profile_question"], 999)

    def test_user_info_file_io_is_atomic_and_read_failures_block_adult_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            user_info_path = Path(temp_dir) / "user_info.json"
            scope = load_functions(
                "create_default_user_info_record",
                "normalize_user_info_record",
                "load_user_info_data",
                "save_user_info_data",
                "get_user_info_record",
                "_adult_intimacy_user_is_marked_minor",
                namespace={
                    "USER_INFO_FILE": str(user_info_path),
                    "json": json,
                    "logger": logging.getLogger("bot-consistency-test"),
                    "os": os,
                    "threading": threading,
                    "user_info_file_lock": FileLock(str(user_info_path) + ".lock"),
                    "user_info_lock": threading.RLock(),
                },
            )

            user_info_path.write_text("{broken", encoding="utf-8")
            self.assertTrue(scope["get_user_info_record"]("alice")["is_minor"])
            self.assertTrue(scope["_adult_intimacy_user_is_marked_minor"]("alice"))
            self.assertTrue(scope["save_user_info_data"]({
                "alice": {"name": "Alice-initial", "is_minor": True}
            }))

            errors = []

            def writer(index):
                if not scope["save_user_info_data"]({
                    "alice": {"name": f"Alice-{index}", "is_minor": True}
                }):
                    errors.append(f"write-{index}")

            def reader():
                try:
                    for _ in range(30):
                        data = scope["load_user_info_data"](raise_on_error=True)
                        if data and not isinstance(data.get("alice"), dict):
                            errors.append("invalid-record")
                except Exception as exc:
                    errors.append(type(exc).__name__)

            threads = [threading.Thread(target=writer, args=(index,)) for index in range(12)]
            threads.append(threading.Thread(target=reader))
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(errors, [])
            self.assertTrue(scope["load_user_info_data"](raise_on_error=True)["alice"]["is_minor"])
            self.assertEqual(list(Path(temp_dir).glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
