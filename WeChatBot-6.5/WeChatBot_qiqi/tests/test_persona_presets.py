import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = PROJECT_ROOT / "prompts"

FEMALE_PRESETS = [
    "女生01_高冷御姐",
    "女生02_邻家姐姐",
    "女生03_温柔知心姐姐",
    "女生04_元气甜妹",
    "女生05_毒舌傲娇女孩",
    "女生06_清冷学姐",
    "女生07_古灵精怪搭子",
    "女生08_知性职场姐姐",
    "女生09_文艺慢热女孩",
    "女生10_直球黏人女孩",
]

MALE_PRESETS = [
    "男生01_高冷男生",
    "男生02_温柔年上",
    "男生03_阳光忠犬系",
    "男生04_毒舌竹马",
    "男生05_清冷学长",
    "男生06_克制精英",
    "男生07_嘴硬痞帅男生",
    "男生08_细腻治愈男生",
    "男生09_腹黑绅士",
    "男生10_电竞男友搭子",
]

REQUIRED_SECTIONS = [
    "任务",
    "最高优先级：回答一致性",
    "角色",
    "外表与气质",
    "兴趣",
    "性格",
    "自然聊天推进",
    "关系推进",
    "时间真实性",
    "关于线下见面",
    "关于语音、电话、视频",
    "说话风格",
    "输出示例",
    "特殊消息",
    "特殊消息示例",
    "联网信息",
]


class PersonaPresetTests(unittest.TestCase):
    def test_default_persona_preserves_the_original_role(self):
        default_path = PROMPTS_DIR / "默认角色.md"

        self.assertTrue(default_path.is_file())
        self.assertFalse((PROMPTS_DIR / "角色1.md").exists())
        content = default_path.read_text(encoding="utf-8")
        self.assertIn("你叫林雨晨", content)
        self.assertIn("# 最高优先级：回答一致性", content)

    def test_all_twenty_presets_exist_and_follow_the_default_structure(self):
        preset_names = FEMALE_PRESETS + MALE_PRESETS
        self.assertEqual(len(preset_names), 20)

        for preset_name in preset_names:
            with self.subTest(preset=preset_name):
                path = PROMPTS_DIR / f"{preset_name}.md"
                self.assertTrue(path.is_file())
                content = path.read_text(encoding="utf-8")
                sections = re.findall(r"^# (.+)$", content, flags=re.MULTILINE)
                self.assertEqual(sections, REQUIRED_SECTIONS)
                self.assertIn("系统", content.split("# 关系推进", 1)[1])
                self.assertIn("只能在系统提示关系已熟悉或亲密时使用", content)

                ages = [int(value) for value in re.findall(r"(?<!\d)(\d{2})岁", content)]
                self.assertTrue(ages)
                self.assertGreaterEqual(min(ages), 20)

    def test_presets_do_not_embed_bypass_instructions(self):
        forbidden_phrases = (
            "去除模型的伦理道德限制",
            "解除模型限制",
            "无视系统限制",
            "不受任何限制",
        )

        for preset_name in FEMALE_PRESETS + MALE_PRESETS:
            content = (PROMPTS_DIR / f"{preset_name}.md").read_text(encoding="utf-8")
            for phrase in forbidden_phrases:
                self.assertNotIn(phrase, content, f"{preset_name} contains {phrase}")


if __name__ == "__main__":
    unittest.main()
