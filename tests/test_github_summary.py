import json
import unittest

from wuditask.github_summary import body_summary
from tests.test_github_graph import MemoryReader, issue, node, snapshot


class BodySummaryTests(unittest.TestCase):
    def test_overview_beats_later_design_and_history(self):
        body = """## ① 当前概况
> **目标：** 打通真实模型调用。
> **状态：** 已交接，尚未验收。
> **当前结论：** 底层合入不等于系统完成。
> **下一步：** 确认首个场景。
> **阻塞／需要协助：** 输入权限待核定。
## ② 交付与进度
### Current status
Historical candidate was green.
<details>
## 下一步
HIDDEN HISTORY
</details>
"""
        summary = body_summary(body)
        self.assertEqual(["打通真实模型调用。"], summary["goal"]["items"])
        self.assertEqual(
            ["已交接，尚未验收。", "底层合入不等于系统完成。"],
            summary["progress"]["items"],
        )
        self.assertEqual(["输入权限待核定。"], summary["remaining"]["items"])
        self.assertEqual(["确认首个场景。"], summary["next"]["items"])
        self.assertNotIn("HIDDEN", json.dumps(summary))

    def test_sections_keep_blockers_and_next_steps_separate(self):
        summary = body_summary("""## Goal
Deliver the backend.
### 已交付基线
- Driver merged.
- Qualification remains open.
### 当前阻塞与解除条件
1. Input unavailable; recheck access.
2. Numerical mismatch remains.
### 近期交付顺序与完成标准
- [ ] Obtain inputs.
- [ ] Validate the model.
## Historical evidence
Do not surface old success as current progress.
""")
        self.assertEqual(
            ["Input unavailable; recheck access.", "Numerical mismatch remains."],
            summary["remaining"]["items"],
        )
        self.assertEqual(
            ["Obtain inputs.", "Validate the model."], summary["next"]["items"]
        )
        self.assertNotIn("old success", json.dumps(summary))

    def test_unknown_sections_are_not_guessed_and_missing_body_is_empty(self):
        self.assertEqual({}, body_summary(None))
        self.assertEqual({}, body_summary("## Logs\nEverything is green"))
        self.assertEqual({"goal"}, set(body_summary("A short issue description.")))

    def test_bounded_excerpts_and_code_comments_hidden_history(self):
        body = """<!-- HIDDEN COMMENT -->
## Summary
**Human goal** and [reference](https://github.com/acme/lib/issues/1).
```text
SECRET CODE
```
<details>
<details>nested</details>
SECRET HISTORY
</details>
## Next steps
""" + "\n".join("- " + str(i) + "x" * 500 for i in range(4))
        summary = body_summary(body)
        self.assertEqual(["Human goal and reference."], summary["goal"]["items"])
        self.assertNotIn("SECRET", json.dumps(summary))
        self.assertTrue(summary["next"]["truncated"])
        self.assertEqual(3, len(summary["next"]["items"]))
        self.assertTrue(all(len(item) == 421 for item in summary["next"]["items"]))

    def test_code_html_does_not_hide_subsequent_prose(self):
        summary = body_summary(
            "## Goal\n```html\n<details>\n```\nUseful work\n## Next steps\nRun acceptance"
        )
        self.assertEqual(["Useful work"], summary["goal"]["items"])
        self.assertEqual(["Run acceptance"], summary["next"]["items"])
        self.assertEqual({}, body_summary("<!-- Unfinished hidden comment"))

    def test_only_public_nodes_publish_body_excerpts(self):
        reader = MemoryReader()
        reader.objects["repos/acme/service/issues/1"]["body"] = "## Goal\nPublic work"
        reader.private.add("secret/private")
        reader.link(
            reader.objects["repos/acme/service/issues/1"],
            issue(27, "secret/private", body="## Goal\nSECRET WORK"),
            "blocks",
        )
        data = snapshot(reader)
        self.assertEqual(["Public work"], node(data, 1)["summary"]["goal"]["items"])
        self.assertNotIn("SECRET WORK", json.dumps(data))
        self.assertNotIn("body", node(data, 1))
