import ast
import copy
from datetime import datetime
import logging
from pathlib import Path
import re
import threading
import unittest
from tempfile import TemporaryDirectory
from unittest import mock

import config_editor


BOT_PATH = Path(__file__).resolve().parents[1] / "bot.py"
BOT_TREE = ast.parse(BOT_PATH.read_text(encoding="utf-8"))


def load_adult_scope(*, global_enabled=True, user_enabled=True, minor=False, group=False):
    state = {}
    context = {
        "global_enabled": global_enabled,
        "user_enabled": user_enabled,
        "minor": minor,
        "group": group,
    }
    names = {
        "create_default_adult_intimacy_state",
        "normalize_adult_intimacy_state",
        "classify_adult_intimacy_signal",
        "is_user_adult_mode_enabled",
        "update_adult_intimacy_state",
        "build_adult_intimacy_prompt",
    }
    names.update(
        node.name
        for node in BOT_TREE.body
        if isinstance(node, ast.FunctionDef)
        and node.name.startswith("_")
        and ("adult" in node.name or "intimacy" in node.name)
    )
    scope = {
        "re": re,
        "datetime": datetime,
        "threading": threading,
        "logger": logging.getLogger("adult-intimacy-regression-test"),
        "adult_intimacy_state_lock": threading.RLock(),
        "get_dynamic_config": lambda key, default: (
            context["global_enabled"]
            if key == "ENABLE_ADULT_INTIMACY"
            else default
        ),
        "load_adult_intimacy_state_data": lambda: copy.deepcopy(state),
        "save_adult_intimacy_state_data": lambda data: (
            state.clear(),
            state.update(copy.deepcopy(data)),
        ),
        "get_user_favorability": lambda _user_id: {"score": 0},
        "get_user_info_record": lambda _user_id: {
            "is_minor": context["minor"],
            "adult_mode_enabled": context["user_enabled"],
        },
        "is_group_speaker_id": lambda _user_id: context["group"],
        "is_user_group_chat": lambda _user_id: context["group"],
    }
    selected = [
        node for node in BOT_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(BOT_PATH), "exec"), scope)
    scope["get_user_adult_intimacy_state"] = lambda user_id: scope[
        "normalize_adult_intimacy_state"
    ](state.get(str(user_id), {}))
    return scope, state, context


class AdultIntimacyRegressionTests(unittest.TestCase):
    def test_manual_switch_enables_every_turn_without_signal_or_score_gate(self):
        scope, state, _context = load_adult_scope()
        state["alice"] = {
            "active": False,
            "last_signal": "stop",
            "last_positive_at": "2000-01-01 00:00:00",
        }

        for score, message in (
            (0, "今天吃什么？"),
            (50, "别说了，换个话题"),
            (100, "想和你亲热"),
        ):
            with self.subTest(score=score, message=message):
                updated = scope["update_adult_intimacy_state"](
                    "alice", message, score
                )
                self.assertTrue(updated["active"])
                self.assertEqual(updated["last_signal"], "manual")
                self.assertTrue(scope["build_adult_intimacy_prompt"](
                    "alice", score=score, current_message=message
                ))

    def test_global_user_minor_and_group_gates_remain_closed(self):
        scope, _state, context = load_adult_scope()
        update = scope["update_adult_intimacy_state"]
        prompt = scope["build_adult_intimacy_prompt"]

        for key in ("global_enabled", "user_enabled"):
            context[key] = False
            with self.subTest(gate=key):
                self.assertFalse(update("alice", "成人关键词", 100)["active"])
                self.assertEqual(prompt("alice", score=100, current_message="成人关键词"), "")
            context[key] = True

        context["minor"] = True
        self.assertFalse(update("alice", "成人关键词", 100)["active"])
        self.assertEqual(prompt("alice", score=100, current_message="成人关键词"), "")
        context["minor"] = False

        context["group"] = True
        self.assertFalse(update("alice", "成人关键词", 100, is_group_chat=True)["active"])
        self.assertEqual(
            prompt("alice", score=100, is_group_chat=True, current_message="成人关键词"),
            "",
        )

    def test_manual_favorability_sync_only_mirrors_persistent_user_switch(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_file = root / "adult_intimacy_state.json"
            user_info_file = root / "user_info.json"
            user_info_file.write_text(
                '{"alice": {"adult_mode_enabled": true, "is_minor": false}}',
                encoding="utf-8",
            )
            config = {"ENABLE_ADULT_INTIMACY": True}
            with mock.patch.object(config_editor, "ADULT_INTIMACY_STATE_FILE", str(state_file)), \
                    mock.patch.object(config_editor, "ADULT_INTIMACY_STATE_LOCK_FILE", str(state_file) + ".lock"), \
                    mock.patch.object(config_editor, "USER_INFO_FILE", str(user_info_file)), \
                    mock.patch.object(config_editor, "parse_config", return_value=config):
                low_score = config_editor.sync_adult_intimacy_state_for_manual_favorability(
                    "alice", 0
                )
                high_score = config_editor.sync_adult_intimacy_state_for_manual_favorability(
                    "alice", 100
                )
                self.assertTrue(low_score["active"])
                self.assertTrue(high_score["active"])
                self.assertEqual(high_score["last_signal"], "manual")

                user_info_file.write_text(
                    '{"alice": {"adult_mode_enabled": false, "is_minor": false}}',
                    encoding="utf-8",
                )
                disabled = config_editor.sync_adult_intimacy_state_for_manual_favorability(
                    "alice", 100
                )
                self.assertFalse(disabled["active"])
