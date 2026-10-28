"""打分判定测试：未达基线、达到基线未破纪录、破纪录不足 ε、有效突破、claimed_score 不一致等。"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from unittest import mock
from fractions import Fraction
from pathlib import Path

from helpers import (BASELINE_12, ERDOS_TURAN_12, OPTIMAL_12, ROOT, SANDBOX, F, TempRepo, git, is_golomb,
                     ruler_with_last, write_shot_dir)

sys.path.insert(0, str(ROOT / "scripts"))
import score as S  # noqa: E402

RULER_3000 = ruler_with_last(3000)   # 长度 3000，差于基线
RULER_2040 = ruler_with_last(2040)   # 长度 2040，只比基线好 7


class FixtureSanity(unittest.TestCase):
    def test_fixtures_are_golomb_rulers(self):
        for marks, length in ((BASELINE_12, 2047), (ERDOS_TURAN_12, 290), (OPTIMAL_12, 85),
                              (RULER_3000, 3000), (RULER_2040, 2040)):
            self.assertTrue(is_golomb(marks), marks)
            self.assertEqual(max(marks) - min(marks), length)


class JudgeTest(unittest.TestCase):
    """纯函数：judge / required_score / current_record / progress。"""

    def problem(self, **kw) -> F.Problem:
        base = F.load_problem_dir(ROOT, SANDBOX)
        fields = {**base.__dict__, **kw}
        return F.Problem(**fields)

    def test_min_absolute(self):
        p = self.problem(epsilon=Fraction(10))
        B = Fraction(2047)
        self.assertEqual(F.judge(p, B, Fraction(3000)), "below_baseline")
        self.assertEqual(F.judge(p, B, Fraction(2047)), "not_record")
        self.assertEqual(F.judge(p, B, Fraction(2040)), "below_epsilon")
        self.assertEqual(F.judge(p, B, Fraction(2037)), "breakthrough")   # 恰好改进 ε 也算
        self.assertEqual(F.judge(p, B, Fraction(290)), "breakthrough")
        # 已有纪录 290：介于基线和纪录之间是"达到基线但未破纪录"
        self.assertEqual(F.judge(p, Fraction(290), Fraction(1000)), "not_record")
        self.assertEqual(F.judge(p, Fraction(290), Fraction(290)), "not_record")
        self.assertEqual(F.judge(p, Fraction(290), Fraction(281)), "below_epsilon")
        self.assertEqual(F.judge(p, Fraction(290), Fraction(280)), "breakthrough")

    def test_max_relative_with_rationals(self):
        p = self.problem(direction="max", baseline=Fraction(10), epsilon=Fraction(1, 100),
                         epsilon_mode="relative")
        rec = Fraction(20)
        self.assertEqual(F.required_score(p, rec), Fraction(101, 5))  # 20 × 1.01
        self.assertEqual(F.judge(p, rec, Fraction(9)), "below_baseline")
        self.assertEqual(F.judge(p, rec, Fraction(10)), "not_record")
        self.assertEqual(F.judge(p, rec, Fraction(2019, 100)), "below_epsilon")
        self.assertEqual(F.judge(p, rec, Fraction(101, 5)), "breakthrough")
        # 负数纪录时幅度取 |纪录| × ε
        self.assertEqual(F.required_score(p, Fraction(-20)), Fraction(-99, 5))

    def test_exactness_no_float_rounding(self):
        p = self.problem(direction="max", baseline=Fraction(0), epsilon=Fraction(1, 10**30))
        rec = Fraction(1, 3)
        self.assertEqual(F.judge(p, rec, rec + Fraction(1, 10**30)), "breakthrough")
        self.assertEqual(F.judge(p, rec, rec + Fraction(1, 10**30 + 1)), "below_epsilon")

    def test_progress(self):
        p = self.problem()
        self.assertEqual(F.progress(p, Fraction(290)), "1757")
        p2 = self.problem(progress_transform="log2")
        self.assertEqual(F.progress(p2, Fraction(2047, 4)), "2.000000")

    def test_parse_rational_strict(self):
        for good, value in (("85", 85), ("-3", -3), ("22/7", Fraction(22, 7)), ("0.125", Fraction(1, 8)),
                            ("4/2", 2)):
            self.assertEqual(F.parse_rational(good), value)
        for bad in ("", " 85", "85 ", "1e3", "1_000", "inf", "nan", "0x10", "1/0", "01", "+1",
                    "1.", ".5", "1/-2", "85\n", 85, None, "9" * 500):
            with self.assertRaises(ValueError, msg=repr(bad)):
                F.parse_rational(bad)


class EvaluateTest(unittest.TestCase):
    """完整判定：本地 python 跑验证器（线上换成容器）。"""

    def setUp(self):
        self.repo = TempRepo(with_git=True)
        self.root = self.repo.root

    def tearDown(self):
        self.repo.cleanup()

    def verify_py(self, problem: str = SANDBOX) -> Path:
        return self.root / "problems" / problem / "verify" / "verify.py"

    def evaluate(self, shot_dir: Path, login: str = "alice", exclude=frozenset(), problem: str = SANDBOX):
        p = F.load_problem_dir(self.root, problem)
        merged, _ = F.list_merged_shots(self.root, problem, exclude=exclude)

        def verifier(d):
            return F.run_verifier_local(d, p.verifier_timeout_sec, self.verify_py(problem))

        return F.evaluate_shot(p, shot_dir, login, verifier, merged)

    def candidate(self, marks, claimed=None, login="alice", sid="cand"):
        tmp = Path(self.repo._tmp.name) / "pr"
        return write_shot_dir(tmp, login, sid, marks, claimed)

    def test_below_baseline(self):
        res = self.evaluate(self.candidate(RULER_3000))
        self.assertEqual(res["status"], "below_baseline", res)
        self.assertEqual(res["record"], "2047")
        self.assertEqual(res["required"], "2046")

    def test_reaches_baseline_but_not_record(self):
        res = self.evaluate(self.candidate(BASELINE_12))
        self.assertEqual(res["status"], "not_record", res)
        self.repo.add_shot("bob", "et", ERDOS_TURAN_12, commit=True)
        res = self.evaluate(self.candidate(RULER_2040))
        self.assertEqual(res["status"], "not_record", res)
        self.assertEqual((res["record"], res["record_ref"]), ("290", "bob/et"))

    def test_record_but_below_epsilon(self):
        repo = TempRepo({'epsilon = "1"': 'epsilon = "10"'}, with_git=True)
        try:
            self.root = repo.root
            res = self.evaluate(self.candidate(RULER_2040))
            self.assertEqual(res["status"], "below_epsilon", res)
            self.assertEqual(res["required"], "2037")
        finally:
            repo.cleanup()

    def test_valid_breakthrough(self):
        res = self.evaluate(self.candidate(ERDOS_TURAN_12))
        self.assertEqual(res["status"], "breakthrough", res)
        self.assertEqual(res["score"], "290")
        self.assertEqual(res["progress"], "1757")
        self.assertRegex(res["canonical_hash"], "^[0-9a-f]{64}$")

    def test_new_submission_must_state_tool_and_main_model(self):
        for i, (old, new) in enumerate((('model = "model-main"\n', ""), ('agent = "claude-code"\n', 'agent = ""\n'),
                                        ('agent = "claude-code"\n', 'agent = "Codex with subagents"\n'),
                                        ('model = "model-main"\n', 'model = "gpt-6"\n'), ('model = "model-main"\n', 'model = "Claude Opus"\n'))):
            d = self.candidate(ERDOS_TURAN_12, sid=f"nomodel{i}")
            md = d / "SHOT.md"
            md.write_text(md.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
            res = self.evaluate(d)
            self.assertEqual(res["status"], "rejected", res)
            self.assertTrue(any("model" in m or "agent" in m for m in res["messages"]), res["messages"])
        for agent in ("codex", "Claude Code", "other: My Harness", "none"):
            d = self.candidate(ERDOS_TURAN_12, sid="ok-" + agent.split(":")[0].replace(" ", "-").lower())
            md = d / "SHOT.md"
            md.write_text(md.read_text(encoding="utf-8").replace('agent = "claude-code"', f'agent = "{agent}"'), encoding="utf-8")
            self.assertEqual(self.evaluate(d)["status"], "breakthrough", agent)

    def test_claimed_score_mismatch(self):
        for claimed in ("291", "290.5", "580/2"):
            res = self.evaluate(self.candidate(ERDOS_TURAN_12, claimed=claimed, sid=f"c{len(claimed)}"))
            expected = "breakthrough" if claimed == "580/2" else "claim_mismatch"  # 580/2 与 290 数值相等
            self.assertEqual(res["status"], expected, (claimed, res))

    def test_invalid_certificate_and_bad_shot_md(self):
        bad = self.candidate(list(range(12)))
        self.assertEqual(self.evaluate(bad)["status"], "invalid")
        d = self.candidate(ERDOS_TURAN_12, sid="badmd")
        (d / "SHOT.md").write_text("no front matter", encoding="utf-8")
        self.assertEqual(self.evaluate(d)["status"], "rejected")
        d = self.candidate(ERDOS_TURAN_12, login="mallory", sid="wrong-login")
        self.assertEqual(self.evaluate(d, login="alice")["status"], "rejected")

    def test_record_is_best_in_merge_order(self):
        # 先合并 zed（290），后合并 amy（85）；目录名顺序与合并顺序相反
        self.repo.add_shot("zed", "a", ERDOS_TURAN_12, commit=True)
        self.repo.add_shot("amy", "b", OPTIMAL_12, commit=True)
        merged, source = F.list_merged_shots(self.root, SANDBOX)
        self.assertEqual(source, "git")
        self.assertEqual([m.ref for m in merged], ["zed/a", "amy/b"])
        res = self.evaluate(self.candidate(RULER_2040))
        self.assertEqual((res["status"], res["record"], res["record_ref"]), ("not_record", "85", "amy/b"))

    def test_copy_of_main_certificate_is_duplicate(self):
        # 平移、镜像后的同一把尺子 canonical_hash 相同
        self.repo.add_shot("zed", "a", ERDOS_TURAN_12, commit=True)
        mirrored = sorted(290 + 5 - m for m in ERDOS_TURAN_12)
        res = self.evaluate(self.candidate(mirrored))
        self.assertEqual(res["status"], "duplicate", res)
        self.assertIn("zed/a", res["messages"][0])

    def test_claimed_score_on_main_is_not_trusted(self):
        # 红队路径一的后果：main 上一个 SHOT.md 写着 claimed_score = "1"，证书其实是 290
        self.repo.add_shot("mallory", "fake", ERDOS_TURAN_12, claimed="1", commit=True)
        res = self.evaluate(self.candidate(OPTIMAL_12))
        self.assertEqual((res["status"], res["record"], res["record_ref"]), ("breakthrough", "2047", None), res)
        self.assertTrue(any("mallory/fake" in w and "不一致" in w for w in res["chain_warnings"]), res)

    def test_invalid_certificate_on_main_is_not_counted(self):
        d = self.repo.add_shot("mallory", "bad", ERDOS_TURAN_12, commit=True)
        (d / "certificate.json").write_text('{"marks": [0, 1, 2]}', encoding="utf-8")
        git(self.root, "commit", "-qam", "tamper")
        res = self.evaluate(self.candidate(RULER_2040))
        self.assertEqual((res["status"], res["record"]), ("breakthrough", "2047"), res)
        self.assertTrue(any("mallory/bad" in w and "无效" in w for w in res["chain_warnings"]), res)

    def test_main_shot_timeout_fails_closed(self):
        self.repo.add_shot("zed", "a", ERDOS_TURAN_12, commit=True)
        problem = F.load_problem_dir(self.root, SANDBOX)
        merged, _ = F.list_merged_shots(self.root, SANDBOX)

        def verifier(d):
            if d.name == "a":
                raise F.VerifierRejected("验证超时（60 秒）")
            return F.run_verifier_local(d, 30, self.verify_py())

        res = F.evaluate_shot(problem, self.candidate(OPTIMAL_12), "alice", verifier, merged)
        self.assertEqual(res["status"], "error", res)

    def test_deeply_nested_front_matter_is_rejected_not_crash(self):
        bomb = "x = " + "[" * 3000 + "]" * 3000 + "\n"
        d = self.candidate(ERDOS_TURAN_12)
        text = (d / "SHOT.md").read_text(encoding="utf-8").replace("+++\n", "+++\n" + bomb, 1)
        (d / "SHOT.md").write_text(text, encoding="utf-8")
        self.assertEqual(self.evaluate(d)["status"], "rejected")
        # 同样的文件进入 main 后：只跳过这个 shot，后续打分照常
        m = self.repo.add_shot("mallory", "bomb", ERDOS_TURAN_12)
        (m / "SHOT.md").write_text(text.replace('login = "alice"', 'login = "mallory"'), encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "bomb")
        merged, _ = F.list_merged_shots(self.root, SANDBOX)
        self.assertIsNotNone(merged[0].error)
        res = self.evaluate(self.candidate(OPTIMAL_12, sid="opt"))
        self.assertEqual(res["status"], "breakthrough", res)
        self.assertTrue(any("mallory/bomb" in w for w in res["chain_warnings"]))

    def test_merge_commits_follow_first_parent_order(self):
        root = self.root
        git(root, "switch", "-q", "-c", "pr1")
        self.repo.add_shot("zed", "late-branch", ERDOS_TURAN_12, commit=True)
        git(root, "switch", "-q", "main")
        self.repo.add_shot("amy", "merged-first", RULER_2040, commit=True)
        git(root, "merge", "-q", "--no-ff", "-m", "merge pr1", "pr1")
        merged, _ = F.list_merged_shots(root, SANDBOX)
        self.assertEqual([m.ref for m in merged], ["amy/merged-first", "zed/late-branch"])

    def test_filesystem_fallback_without_git(self):
        repo = TempRepo(with_git=False)
        try:
            repo.add_shot("bob", "x", ERDOS_TURAN_12)
            merged, source = F.list_merged_shots(repo.root, SANDBOX)
            self.assertEqual((source, [m.ref for m in merged]), ("filesystem", ["bob/x"]))
        finally:
            repo.cleanup()

    def test_other_problems_do_not_affect_record_or_duplicates(self):
        repo = TempRepo(with_git=True, extra_problems=("p001-other",))
        try:
            self.root = repo.root
            repo.add_shot("zed", "et", ERDOS_TURAN_12, commit=True, problem="p001-other")
            repo.add_shot("amy", "opt", OPTIMAL_12, commit=True, problem="p001-other")
            res = self.evaluate(self.candidate(ERDOS_TURAN_12))
            self.assertEqual((res["status"], res["record"], res["record_ref"]), ("breakthrough", "2047", None), res)
            merged, _ = F.list_merged_shots(repo.root, "p001-other")
            self.assertEqual([m.ref for m in merged], ["zed/et", "amy/opt"])
            res = self.evaluate(self.candidate(ERDOS_TURAN_12, sid="dup"), problem="p001-other")
            self.assertEqual(res["status"], "duplicate", res)
        finally:
            repo.cleanup()

    def test_shots_under_other_paths_are_not_merged_shots(self):
        # 只有 problems/<题目>/shots/<login>/<id>/SHOT.md 算这道题的 shot
        d = self.root / "problems" / SANDBOX / "shots" / "alice"
        d.mkdir(parents=True)
        (d / "SHOT.md").write_text("x", encoding="utf-8")
        nested = self.repo.add_shot("bob", "x", ERDOS_TURAN_12)
        (nested / "deeper").mkdir()
        (self.root / "shots" / "carl" / "y").mkdir(parents=True)
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "odd paths")
        merged, _ = F.list_merged_shots(self.root, SANDBOX)
        self.assertEqual([m.ref for m in merged], ["bob/x"])


class BuildResultTest(unittest.TestCase):
    """score.py 的整体流程：暂停开关、门禁结果透传、写结果文件。"""

    def setUp(self):
        self.repo = TempRepo(with_git=True)
        self.work = Path(self.repo._tmp.name) / "work"
        self.work.mkdir()

    def tearDown(self):
        self.repo.cleanup()

    def write(self, name, obj):
        (self.work / name).write_text(json.dumps(obj), encoding="utf-8")

    def gate_ok(self, marks, claimed=None, problem=SANDBOX):
        write_shot_dir(self.work / "pr", "alice", "x", marks, claimed)
        (self.work / "pr" / "shots" / "alice" / "x").rename(self.work / "shot")
        self.write("gate.json", {"ok": True, "status": "ok", "messages": [],
                                 "pr": {"number": 1, "author": "alice", "head_sha": "a" * 40},
                                 "shot": {"problem": problem, "login": "alice", "shot_id": "x"}})

    def build(self):
        def make_verifier(problem):
            verify_py = self.repo.root / "problems" / problem.dir / "verify" / "verify.py"
            return lambda d: F.run_verifier_local(d, 30, verify_py)

        return S.build_result(self.work, self.repo.root, make_verifier)

    def test_paused(self):
        self.write("control.json", {"paused": True, "reason": "维护中"})
        self.gate_ok(ERDOS_TURAN_12)
        res = self.build()
        self.assertEqual((res["status"], res["messages"]), ("paused", ["维护中"]))
        self.assertIsNone(res["score"])

    def test_missing_control_is_error(self):
        self.assertEqual(self.build()["status"], "error")

    def test_gate_rejection_passthrough(self):
        self.write("control.json", {"paused": False, "reason": ""})
        self.write("gate.json", {"ok": False, "status": "rejected", "messages": ["不允许"], "pr": None})
        res = self.build()
        self.assertEqual((res["status"], res["messages"]), ("rejected", ["不允许"]))
        self.write("gate.json", {"ok": False, "status": "weird", "messages": [1, "x"]})
        res = self.build()
        self.assertEqual((res["status"], res["messages"]), ("error", ["x"]))

    def test_breakthrough_end_to_end(self):
        self.write("control.json", {"paused": False, "reason": ""})
        self.gate_ok(ERDOS_TURAN_12)
        res = self.build()
        self.assertEqual(res["status"], "breakthrough", res)
        self.assertEqual(res["shot"], {"problem": SANDBOX, "login": "alice", "shot_id": "x"})
        self.assertEqual((res["problem"]["dir"], res["problem"]["id"], res["baseline"]), (SANDBOX, "p000", "2047"))

    def test_gate_problem_is_rechecked_against_main(self):
        self.write("control.json", {"paused": False, "reason": ""})
        for bad in ("p999-nothing", "../p000-sandbox", None, 7):
            self.gate_ok(ERDOS_TURAN_12, problem=bad)
            res = self.build()
            self.assertEqual((res["status"], res["problem"]), ("error", None), bad)
            (self.work / "shot").rename(self.work / f"old-{abs(hash(str(bad)))}")

    def test_verifier_build_failure_is_error(self):
        self.write("control.json", {"paused": False, "reason": ""})
        self.gate_ok(ERDOS_TURAN_12)

        def make_verifier(problem):
            raise F.VerifierError("构建验证器镜像失败")

        res = S.build_result(self.work, self.repo.root, make_verifier)
        self.assertEqual(res["status"], "error")
        self.assertIn("构建验证器镜像失败", res["messages"][0])

    def test_records_main_sha_used_for_scoring(self):
        self.write("control.json", {"paused": False, "reason": ""})
        self.gate_ok(ERDOS_TURAN_12)
        res = self.build()
        self.assertEqual(res["base_sha"], git(self.repo.root, "rev-parse", "HEAD").strip())

    def test_cli_local_runner_writes_result(self):
        self.write("control.json", {"paused": False, "reason": ""})
        self.gate_ok(ERDOS_TURAN_12, claimed="300")
        out = self.work / "out" / "result.json"
        proc = subprocess.run([sys.executable, "-I", str(self.repo.root / "scripts" / "score.py"),
                               "--work", str(self.work), "--root", str(self.repo.root),
                               "--runner", "local", "--out", str(out)], capture_output=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        res = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(res["status"], "claim_mismatch")
        self.assertEqual((res["score"], res["claimed_score"]), ("290", "300"))


class DockerCommandTest(unittest.TestCase):
    def test_build_uses_problem_dir_as_context(self):
        cmd = S.build_command("fewshots-verifier:main", Path("/w/repo/problems/p000-sandbox"))
        self.assertEqual(cmd, ["docker", "build", "--pull", "-t", "fewshots-verifier:main",
                               "-f", "/w/repo/problems/p000-sandbox/verify/Dockerfile", "/w/repo/problems/p000-sandbox"])
        with self.assertRaises(F.VerifierError):
            S.build_command("img:1", Path("/w/repo/problems/a b"))

    def test_required_isolation_flags(self):
        cmd = S.docker_command("fewshots-verifier:main", Path("/tmp/work/shot"), "fewshots-verify-x")
        for flag in ("--rm", "--network=none", "--read-only", "--memory=4g", "--pids-limit=256",
                     "--cap-drop=ALL", "--security-opt=no-new-privileges"):
            self.assertIn(flag, cmd)
        self.assertEqual(cmd[cmd.index("--user") + 1], "65534:65534")
        self.assertIn("type=bind,source=/tmp/work/shot,target=/shot,readonly", cmd)
        self.assertEqual(cmd[-2:], ["fewshots-verifier:main", "/shot"])

    def test_rejects_unsafe_mount_paths(self):
        for bad in ("/tmp/a,b", "/tmp/a b", "/tmp/$(id)"):
            with self.assertRaises(F.VerifierError):
                S.docker_command("img:1", Path(bad), "n")

    def test_timeout_kills_container(self):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            if cmd[1] == "run":
                raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        with mock.patch.object(S.subprocess, "run", fake_run):
            with self.assertRaises(F.VerifierRejected):
                S.run_verifier_docker("img:1", Path("/tmp/shot"), 5)
        self.assertEqual([c[1] for c in calls], ["run", "kill", "rm"])

    def test_verifier_output_contract(self):
        good = b'{"valid":true,"score":"85","canonical_hash":"' + b"a" * 64 + b'","message":"ok"}\n'
        self.assertEqual(F.parse_verifier_output(good)["score"], Fraction(85))
        for bad in (good + good, b"", b'{"valid":true}', good.replace(b'"85"', b"85"),
                    good.replace(b'"85"', b'"1e9"'), b'{"valid":"yes","score":null,"canonical_hash":null,"message":""}',
                    b"x" * 70000):
            with self.assertRaises(F.VerifierError, msg=bad[:60]):
                F.parse_verifier_output(bad)


if __name__ == "__main__":
    unittest.main()
