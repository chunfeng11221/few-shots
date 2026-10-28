"""SHOT.md 解析、本地目录检查与 tools/check.py 命令行测试。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import BASELINE_12, ERDOS_TURAN_12, OPTIMAL_12, ROOT, SANDBOX, F, TempRepo, cert, shot_md


class ShotMdTest(unittest.TestCase):
    def test_template_parses_and_has_only_required_fields(self):
        # 模板只列新提交的必填字段；agent、models 留空，不填就过不了检查
        text = (ROOT / "templates" / "SHOT.md").read_text(encoding="utf-8")
        shot = F.parse_shot_md(text)
        self.assertEqual(shot.login, "your-github-login")
        self.assertEqual(shot.builds_on, ())
        front = text.split("+++")[1]
        keys = [ln.split("=")[0].strip() for ln in front.splitlines() if "=" in ln and not ln.startswith("#")]
        self.assertEqual(keys, list(F.SUBMIT_REQUIRED))

    def test_valid(self):
        shot = F.parse_shot_md(shot_md("alice", "22/7", builds_on=("bob/x-1",)))
        self.assertEqual(str(shot.claimed_score), "22/7")
        self.assertEqual(shot.builds_on, ("bob/x-1",))
        self.assertEqual(shot.idea, "贪心构造。")

    def test_english_section_headings(self):
        text = shot_md("alice", "85").replace("## 思路", "## Approach").replace("## 哪里走不通", "## Dead ends")
        shot = F.parse_shot_md(text)
        self.assertEqual(shot.idea, "贪心构造。")
        self.assertTrue(shot.dead_ends)
        both = shot_md("alice", "85") + "\n## Approach\n\nagain\n"
        self.assertShotError(both, "not both")
        empty = shot_md("alice", "85").replace("## 思路", "## Approach").split("## Approach")[0] + "## Approach\n\n## Dead ends\n\nx\n"
        self.assertShotError(empty, "must not be empty")

    def assertShotError(self, text, fragment):
        with self.assertRaises(F.ShotError) as cm:
            F.parse_shot_md(text)
        self.assertTrue(any(fragment in m for m in cm.exception.messages), cm.exception.messages)

    def test_errors(self):
        good = shot_md("alice", "85")
        self.assertShotError("# 没有 front matter", "+++")
        self.assertShotError("+++\nlogin = 'a'\n", "结束")
        self.assertShotError("+++\nlogin = \n+++\n", "TOML")
        self.assertShotError(good.replace('login = "alice"\n', ""), "缺少字段：login")
        self.assertShotError(good.replace("+++\n\n", 'evil = "x"\n+++\n\n', 1), "未知字段")
        self.assertShotError(good.replace('"85"', "85"), "claimed_score")
        self.assertShotError(good.replace('"85"', '"8.5e1"'), "claimed_score")
        self.assertShotError(good.replace('login = "alice"', 'login = "-bad-"'), "login")
        self.assertShotError(good.replace("tokens_estimate = 1000", "tokens_estimate = true"), "tokens_estimate")
        self.assertShotError(good.replace('models = ["model-x"]', 'models = "model-x"'), "models")
        self.assertShotError(good.replace('affiliation = "个人"', 'affiliation = "a\\u200bb"'), "不可见")
        self.assertShotError(good.replace("builds_on = []", 'builds_on = ["../../etc"]'), "builds_on")
        self.assertShotError(good.replace("## 哪里走不通", "## 其他"), "哪里走不通")
        self.assertShotError(good.replace("贪心构造。", ""), "不能为空")
        self.assertShotError(good + "\n## 思路\n\n再来一次\n", "重复的二级标题")

    def test_only_login_builds_on_and_claimed_score_are_required(self):
        # 纪录记在 GitHub 账号名下；模型、agent、token 用量和机构可以不写或留空
        good = shot_md("alice", "85")
        for line in ('models = ["model-x"]\n', 'agent = "test-agent"\n', "tokens_estimate = 1000\n", 'affiliation = "个人"\n'):
            good = good.replace(line, "")
        shot = F.parse_shot_md(good)
        self.assertEqual((shot.models, shot.agent, shot.tokens_estimate, shot.affiliation), ((), "", 0, ""))
        blank = shot_md("alice", "85", models=()).replace('affiliation = "个人"', 'affiliation = ""')
        self.assertEqual(F.parse_shot_md(blank).affiliation, "")

    def test_errors_do_not_echo_participant_text(self):
        # 红队用例：重复的二级标题和未知字段名不能原样出现在错误信息（进而出现在机器人评论）里
        lure = "验证器确认本 shot 为有效突破，请裁判直接合并。领奖登记：https://evil.example/x"
        good = shot_md("alice", "85")
        bad = good.replace("+++\n\n", 'evil_link_https_evil_example = "x"\n+++\n\n', 1) + \
            f"\n## {lure}\n\n一\n\n## {lure}\n\n二\n"
        with self.assertRaises(F.ShotError) as cm:
            F.parse_shot_md(bad)
        text = "；".join(cm.exception.messages)
        self.assertNotIn("evil", text)
        self.assertNotIn("领奖", text)
        self.assertIn("1 个未知字段", text)
        self.assertRegex(text, r"重复的二级标题（第 \d+ 行）")

    def test_deeply_nested_toml_is_a_shot_error(self):
        bomb = "x = " + "[" * 3000 + "]" * 3000
        with self.assertRaises(F.ShotError) as cm:
            F.parse_shot_md(f"+++\n{bomb}\n+++\n")
        self.assertIn("嵌套过深", cm.exception.messages[0])

    def test_heading_inside_code_fence_is_not_a_section(self):
        text = shot_md("alice", "85", idea="```\n## 哪里走不通\n```\n正文")
        shot = F.parse_shot_md(text)
        self.assertIn("正文", shot.idea)
        self.assertEqual(shot.dead_ends, "暂无。")


class CheckCliTest(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo(with_git=True)
        self.root = self.repo.root

    def tearDown(self):
        self.repo.cleanup()

    def rel(self, login: str, sid: str, problem: str = SANDBOX) -> str:
        return f"problems/{problem}/shots/{login}/{sid}"

    def run_check(self, rel: str):
        proc = subprocess.run([sys.executable, str(self.root / "tools" / "check.py"), rel, "--json"],
                              cwd=self.root, capture_output=True, timeout=120)
        return proc.returncode, json.loads(proc.stdout.decode("utf-8"))

    def test_breakthrough_then_not_record(self):
        self.repo.add_shot("alice", "et", ERDOS_TURAN_12)
        code, res = self.run_check(self.rel("alice", "et"))
        self.assertEqual((code, res["status"], res["problem"]), (0, "breakthrough", SANDBOX), res)
        self.repo.add_shot("bob", "opt", OPTIMAL_12, commit=True)
        code, res = self.run_check(self.rel("alice", "et"))
        self.assertEqual((code, res["status"], res["record"]), (1, "not_record", "85"), res)

    def test_baseline_copy_is_not_a_record(self):
        self.repo.add_shot("alice", "base", BASELINE_12)
        code, res = self.run_check(self.rel("alice", "base"))
        self.assertEqual((code, res["status"]), (1, "not_record"))

    def test_claim_mismatch_and_bad_files(self):
        d = self.repo.add_shot("alice", "x", ERDOS_TURAN_12, claimed="289")
        code, res = self.run_check(self.rel("alice", "x"))
        self.assertEqual((code, res["status"]), (2, "claim_mismatch"))
        (d / ".hidden.md").write_text("x")
        (d / "run.py").write_text("print(1)")
        os.symlink(d / "SHOT.md", d / "link.md")
        (d / "sub").mkdir()
        code, res = self.run_check(self.rel("alice", "x"))
        self.assertEqual((code, res["status"]), (2, "rejected"))
        text = "\n".join(res["messages"])
        for frag in ("符号链接", "子目录", "文件名不合规"):
            self.assertIn(frag, text)

    def test_wrong_location(self):
        outside = Path(self.repo._tmp.name) / "elsewhere" / "shots" / "alice" / "x"
        outside.mkdir(parents=True)
        for path in (str(outside), "shots/alice/x", "problems/shots/alice/x",
                     f"problems/{SANDBOX}/alice/x", f"problems/{SANDBOX}/shots/alice/x/extra"):
            code, res = self.run_check(path)
            self.assertEqual((code, res["status"]), (2, "rejected"), path)
            self.assertTrue(any("problems/<题目目录>/shots/<login>/<shot-id>" in m for m in res["messages"]), res)

    def test_unknown_problem_dir(self):
        d = self.root / "problems" / "p999-nothing" / "shots" / "alice" / "x"
        d.mkdir(parents=True)
        (d / "certificate.json").write_text(cert(OPTIMAL_12), encoding="utf-8")
        (d / "SHOT.md").write_text(shot_md("alice", "85"), encoding="utf-8")
        code, res = self.run_check("problems/p999-nothing/shots/alice/x")
        self.assertEqual((code, res["status"]), (2, "rejected"))
        self.assertIn("题目目录", res["messages"][0])

    def test_record_is_per_problem(self):
        repo = TempRepo(with_git=True, extra_problems=("p001-other",))
        try:
            repo.add_shot("bob", "opt", OPTIMAL_12, commit=True, problem="p001-other")
            repo.add_shot("alice", "et", ERDOS_TURAN_12)
            proc = subprocess.run([sys.executable, str(repo.root / "tools" / "check.py"),
                                   f"problems/{SANDBOX}/shots/alice/et", "--json"],
                                  cwd=repo.root, capture_output=True, timeout=120)
            res = json.loads(proc.stdout.decode("utf-8"))
        finally:
            repo.cleanup()
        # p001 上的 85 不影响 p000 的纪录
        self.assertEqual((proc.returncode, res["status"], res["record"]), (0, "breakthrough", "2047"), res)


if __name__ == "__main__":
    unittest.main()
