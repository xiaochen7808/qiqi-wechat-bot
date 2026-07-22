from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_URL = "https://github.com/iwyxdxl/WeChatBot_WXAUTO_SE"
PROJECT_NAME = "iwyxdxl/WeChatBot_WXAUTO_SE"


class StartupIntegrityTests(unittest.TestCase):
    def test_license_keeps_upstream_project_source(self):
        license_text = (PROJECT_ROOT / "LICENSE").read_text(encoding="utf-8")

        self.assertIn(
            "For more information about this project, please visit:\n"
            f"{UPSTREAM_URL}",
            license_text,
        )

    def test_config_editor_keeps_upstream_copyright_header(self):
        source = (PROJECT_ROOT / "config_editor.py").read_text(encoding="utf-8")
        header = source[:source.index("from flask import")]

        self.assertIn("Copyright (C) 2025, iwyxdxl", header)
        self.assertIn("Licensed under GNU GPL-3.0 or higher", header)

    def test_bot_sets_project_name_before_wxautox_import(self):
        source = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        project_name_position = source.index(
            'os.environ["PROJECT_NAME"] = \'iwyxdxl/WeChatBot_WXAUTO_SE\''
        )
        wxautox_import_position = source.index("from wxautox4_wechatbot import")

        self.assertLess(project_name_position, wxautox_import_position)

    def test_run_bat_uses_metadata_check_without_destructive_reinstall(self):
        run_bat_path = PROJECT_ROOT / "Run.bat"
        script_bytes = run_bat_path.read_bytes()
        script = script_bytes.decode("utf-8")
        script_lower = script.lower()

        self.assertFalse(script_bytes.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(script_bytes.count(b"\n"), script_bytes.count(b"\r\n"))
        self.assertIn('cd /d "%~dp0"', script)
        self.assertIn('set "PYTHONUTF8=1"', script)
        self.assertIn('set "PYTHONIOENCODING=utf-8"', script)
        self.assertIn(f'set "PROJECT_NAME={PROJECT_NAME}"', script)
        self.assertIn('set "WXAUTOX_DISTRIBUTION=wxautox4-wechatbot"', script)
        self.assertIn('set "WXAUTOX_REQUIRED_VERSION=40.1.10"', script)
        self.assertIn("from importlib.metadata import version", script)
        self.assertIn("struct.calcsize(chr(80)) * 8", script)
        self.assertIn("chr(45)", script)
        self.assertIn("platform.machine().lower()", script)
        self.assertIn('"64-amd64"', script)
        self.assertIn('"64-x86_64"', script)
        self.assertIn(
            "version('!WXAUTOX_DISTRIBUTION!') == "
            "'!WXAUTOX_REQUIRED_VERSION!'",
            script,
        )
        for py_tag in ("cp39", "cp310", "cp311", "cp312"):
            self.assertIn(
                f"wxautox4_wechatbot-40.1.10-{py_tag}-{py_tag}-win_amd64.whl",
                script,
            )
            self.assertTrue(
                (
                    PROJECT_ROOT
                    / "libs"
                    / f"wxautox4_wechatbot-40.1.10-{py_tag}-{py_tag}-win_amd64.whl"
                ).is_file()
            )

        self.assertNotRegex(script_lower, r"pip\s+uninstall")
        self.assertNotIn("--force-reinstall", script_lower)
        self.assertNotIn("powershell", script_lower)
        self.assertNotIn("pwsh", script_lower)
        self.assertIn(
            "from wxautox4_wechatbot import WeChat; "
            "from wxautox4_wechatbot.param import WxParam",
            script,
        )
        self.assertRegex(
            script,
            r'(?s)if not exist "!WHL_FILE!" \(.+?exit /b 1.+?\)',
        )
        self.assertRegex(
            script,
            r'(?s)python -m pip install "!WHL_FILE!" --no-deps\s+'
            r'if !errorlevel! neq 0 \(.+?exit /b 1.+?\)',
        )

    def test_web_templates_do_not_publish_upstream_url(self):
        template_dir = PROJECT_ROOT / "templates"
        templates = list(template_dir.rglob("*.html"))

        self.assertTrue(templates)
        for template_path in templates:
            with self.subTest(template=template_path.name):
                template = template_path.read_text(encoding="utf-8")
                self.assertNotIn(UPSTREAM_URL, template)

    def test_private_wheel_runtime_dependencies_are_declared(self):
        requirements = {
            line.strip().split("==", 1)[0].lower()
            for line in (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

        self.assertIn("colorama", requirements)
        self.assertIn("tenacity", requirements)

    def test_bot_has_one_wxautox_import_path(self):
        source = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")

        self.assertEqual(source.count("from wxautox4_wechatbot import WeChat"), 1)
        self.assertIn("except ImportError as exc", source)


if __name__ == "__main__":
    unittest.main()
