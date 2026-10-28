"""build_board.py 的单元测试：纪录链、进度、机器人判定核对、README 渲染、缓存、只读 API、单题失败隔离。

夹具：tests/fixtures/board/ 下的题目按 meta.json 的 order 逐个提交进一个临时 git 仓库，
merges / unverified 模拟 GitHub API 返回的合并 PR 与判定核对结果。
更新期望文件：UPDATE_GOLDEN=1 python3 tools/run_tests.py
"""

from __future__ import annotations

import collections
import datetime as dt
import io
import json
import math
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from fractions import Fraction
from pathlib import Path

from helpers import ROOT, SANDBOX, F, git

sys.path.insert(0, str(ROOT / "scripts"))
import build_board as bb  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "board"
NOW = dt.datetime(2001, 1, 4, 12, 0, 0, tzinfo=dt.UTC)
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"
META = json.loads((FIXTURES / "meta.json").read_text("utf-8"))


class FixtureSource:
    """模拟 GitHubSource：合并信息与判定核对来自 meta.json。"""

    def __init__(self, meta: dict = META):
        self.meta = meta
        self.calls: collections.Counter[str] = collections.Counter()

    def merge_info(self, commit: str, path: str):
        self.calls["merge"] += 1
        m = self.meta["merges"].get(path)
        return (m["at"], m["pr"]) if m else (None, None)

    def verdict(self, pr: int, path: str, blob_sha: str):
        self.calls["verdict"] += 1
        return self.meta.get("unverified", {}).get(path)


class NoNetwork:
    def merge_info(self, *a):  # noqa: ANN002
        raise AssertionError("不应访问网络")

    verdict = merge_info


def fixture_repo(extra=None) -> Path:
    """建一个临时 git 仓库：README、各题 problem.toml 先提交，再按 order 逐个提交 shot。"""
    root = Path(tempfile.mkdtemp(prefix="fs-board-"))
    shutil.copy(FIXTURES / "README.md", root / "README.md")
    for d in sorted((FIXTURES / "problems").iterdir()):
        (root / "problems" / d.name).mkdir(parents=True)
        shutil.copy(d / "problem.toml", root / "problems" / d.name / "problem.toml")
    git(root, "init", "-q", "-b", "main")
    # 关掉后台自动 gc / maintenance：它们会在 tearDown 删目录时并发写对象，导致偶发失败
    git(root, "config", "gc.auto", "0")
    git(root, "config", "maintenance.auto", "false")
    clock = [0]

    def commit(msg: str) -> None:
        clock[0] += 1
        when = f"2001-01-01T00:{clock[0]:02d}:00+00:00"
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", msg, env={"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when})

    commit("init")
    for item in META["order"] + list(extra or []):
        pdir, login, sid = item.split("/")
        src = FIXTURES / "problems" / pdir / "shots" / login / sid
        dst = root / "problems" / pdir / "shots" / login / sid
        if src.exists():
            shutil.copytree(src, dst)
        commit(f"shot {item}")
    return root


def add_special_shots(root: Path) -> None:
    """符号链接和超大的 SHOT.md：夹具目录里不放，直接在临时仓库里造。"""
    link = root / "problems" / "p901-demo-max" / "shots" / "zed" / "link"
    link.mkdir(parents=True)
    os.symlink("../../alice/first/SHOT.md", link / "SHOT.md")
    huge = root / "problems" / "p901-demo-max" / "shots" / "oscar" / "huge"
    huge.mkdir(parents=True)
    (huge / "SHOT.md").write_text("+++\n" + "#" * (2 * 1024 * 1024), "utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "special")


def golden(name: str, actual: str) -> str:
    path = FIXTURES / name
    if UPDATE:
        path.write_text(actual, "utf-8")
    return path.read_text("utf-8")


def problem(direction="max", baseline="10", epsilon="1", mode="absolute", transform="linear") -> F.Problem:
    base = F.load_problem_dir(ROOT, SANDBOX)
    return F.Problem(**{**base.__dict__, "direction": direction, "baseline": Fraction(baseline),
                        "epsilon": Fraction(epsilon), "epsilon_mode": mode, "progress_transform": transform})


def shot(login, score, pr=1) -> bb.Merged:
    return bb.Merged(login, "s", Fraction(score), ("M",), "2001-01-01T00:00:00Z", pr)


class ChainTest(unittest.TestCase):
    def test_max_epsilon_in_merge_order(self):
        shots = [shot("alice", "11"), shot("bob", "23/2"), shot("carol", "25/2"), shot("dave", "27/2"),
                 shot("worse", "12")]
        records, skipped = bb.record_chain(problem(), shots)
        self.assertEqual([s.login for s in records], ["alice", "carol", "dave"])
        self.assertEqual([s.login for s, _ in skipped], ["bob", "worse"])

    def test_first_record_must_beat_baseline_by_epsilon(self):
        records, skipped = bb.record_chain(problem(), [shot("a", "21/2")])
        self.assertEqual(records, [])
        self.assertIn("ε", skipped[0][1])
        _, skipped = bb.record_chain(problem(), [shot("a", "9")])
        self.assertIn("基线", skipped[0][1])

    def test_min_direction(self):
        records, _ = bb.record_chain(problem("min", baseline="1000"),
                                     [shot("a", "100"), shot("b", "100"), shot("c", "999/10"), shot("d", "98")])
        self.assertEqual([s.login for s in records], ["a", "d"])

    def test_relative_epsilon(self):
        p = problem("min", baseline="1000", epsilon="1/100", mode="relative")
        self.assertEqual(F.required_score(p, Fraction(49)), Fraction(4851, 100))
        records, skipped = bb.record_chain(p, [shot("a", "990"), shot("b", "981"), shot("c", "9801/10")])
        self.assertEqual([s.login for s in records], ["a", "c"])
        self.assertEqual([s.login for s, _ in skipped], ["b"])

    def test_exact_rationals(self):
        # 1/3 + 1/3 + 1/3 精确等于 1，浮点会出错
        records, _ = bb.record_chain(problem(baseline="0", epsilon="1/3"),
                                     [shot("a", "1/3"), shot("b", "2/3"), shot("c", "1")])
        self.assertEqual(len(records), 3)


class ProgressTest(unittest.TestCase):
    def test_no_record(self):
        self.assertEqual(bb.progress(problem(), None), (None, 0.0))

    def test_linear(self):
        self.assertEqual(bb.progress(problem(), Fraction(15)), ("5", 50.0))
        self.assertEqual(bb.progress(problem(baseline="3"), Fraction(4)), ("1", 33.33))
        self.assertEqual(bb.progress(problem(baseline="10"), Fraction(35)), ("25", 250.0))
        self.assertEqual(bb.progress(problem("min", baseline="2047"), Fraction(1023)), ("1024", 50.02))
        self.assertEqual(bb.progress(problem(baseline="0"), Fraction(1, 2)), ("1/2", None))

    def test_log2(self):
        self.assertEqual(bb.progress(problem("max", baseline="1024", transform="log2"), Fraction(4096)),
                         ("2.000000", 20.0))
        p = problem("min", baseline="1024", transform="log2")
        self.assertEqual(bb.progress(p, Fraction(32)), ("5.000000", 50.0))
        self.assertEqual(bb.progress(p, Fraction(0)), (None, None))
        self.assertEqual(bb.progress(problem("max", baseline="1", transform="log2"), Fraction(8)), ("3.000000", None))
        self.assertEqual(bb.progress(problem("max", baseline=str(2**500), transform="log2"), Fraction(2**1000)),
                         ("500.000000", 100.0))


class RenderTest(unittest.TestCase):
    def test_empty_state_is_header_only(self):
        md = bb.render_board({"updated_at": "2001-01-01T00:00:00Z", "problems": []})
        self.assertEqual([ln for ln in md.splitlines() if ln.startswith("|")], list(bb.TABLE_HEAD))
        self.assertNotIn("**", md)
        self.assertNotRegex(md, r"20[0-9]{2}-[01][0-9]-[0-3][0-9]")  # 表格里不写日期

    def test_table_columns(self):
        self.assertEqual(bb.TABLE_HEAD[0], "| 题目 · Problem | 指标 · Metric | 预设基线 · Baseline | 当前纪录 · Record "
                                           "| 纪录保持者 · Holder | 有效突破 · Breakthroughs | 进度 · Progress |")

    def test_replace_region(self):
        readme = "前\n<!-- board:start -->\n旧\n<!-- board:end -->\n后\n"
        self.assertEqual(bb.replace_region(readme, "新\n"), "前\n<!-- board:start -->\n新\n<!-- board:end -->\n后\n")
        for bad in ["没有标记", "<!-- board:end -->\n<!-- board:start -->",
                    "<!-- board:start --><!-- board:start --><!-- board:end -->"]:
            with self.subTest(bad), self.assertRaises(bb.BoardError):
                bb.replace_region(bad, "x")

    def test_escaping(self):
        self.assertEqual(bb.md_escape("a|b [c](d) <e> *f*"), r"a\|b \[c\]\(d\) \<e\> \*f\*")


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        self.root = fixture_repo()
        add_special_shots(self.root)

    def tearDown(self):
        shutil.rmtree(self.root)

    def build(self, source=None, warnings=None, now=NOW):
        return bb.build(self.root, source or FixtureSource(), now, (warnings if warnings is not None else []).append)

    def save(self, board, readme, cache) -> None:
        (self.root / "data").mkdir(exist_ok=True)
        (self.root / "data" / "board.json").write_text(bb.dump_json(board), "utf-8")
        (self.root / "data" / "cache.json").write_text(bb.dump_json(cache), "utf-8")
        (self.root / "README.md").write_text(readme, "utf-8")

    def test_fixture_board_and_readme(self):
        warnings: list[str] = []
        board, readme, cache = self.build(warnings=warnings)
        actual_json = bb.dump_json(board)
        self.assertEqual(actual_json, golden("expected_board.json", actual_json))
        self.assertEqual(readme, golden("expected_README.md", readme))

        self.assertEqual([p["id"] for p in board["problems"]], ["p901", "p902", "p903"])  # 沙盒题不上榜
        p1, p2, p3 = board["problems"]
        self.assertEqual((p1["dir"], p1["repo"]), ("problems/p901-demo-max", "few-shots/few-shots"))
        self.assertEqual(p1["url"], "https://github.com/few-shots/few-shots/tree/main/problems/p901-demo-max")
        # victor 的 claimed_score 是 1000，但合并 PR 上没有机器人判定的有效突破，不计入
        self.assertEqual([r["login"] for r in p1["records"]], ["alice", "carol", "dave", "mallory"])
        self.assertEqual(p1["current"], {"score": "15", "login": "mallory", "agent": "test-agent", "verified_at": None, "tool": "test-agent", "model": "", "models": p1["records"][-1]["models"],
                                         "at": "2001-01-01T14:00:00Z", "pr": 9})
        self.assertEqual((p1["progress"], p1["progress_pct"]), ("5", 50.0))
        # judy 没有对应的 PR，无法核对机器人的判定，不计入
        self.assertEqual([r["login"] for r in p2["records"]], ["frank", "ivan", "kim"])
        gain = math.log2(1000) - math.log2(48.6)
        self.assertEqual(p2["progress"], f"{gain:.6f}")
        self.assertEqual(p2["progress_pct"], round(gain / math.log2(1000) * 100, 2))
        self.assertIsNone(p3["current"])
        self.assertEqual((p3["records"], p3["progress"], p3["progress_pct"]), ([], None, 0.0))

        joined = "\n".join(warnings)
        for needle in ["eve/nofm", "zed/link", "oscar/huge", "trudy/fake", "bob/tiny", "grace/same", "heidi/tie",
                       "victor/claim", "judy/direct", "沙盒", "few-shots/verdict"]:
            self.assertIn(needle, joined)
        for ignored in ("notes", "bad_login", "zoe/Upper"):   # 层级或命名不对的目录直接忽略
            self.assertNotIn(ignored, joined)
        self.assertNotIn("Model", readme)                    # 模型只进 board.json，不进 README 表格
        self.assertNotIn("<img", readme)
        self.assertIn("上面的文字要原样保留。", readme)
        self.assertIn("下面的文字也要原样保留。", readme)
        self.assertIn("[p901 · 测试题：越大越好](problems/p901-demo-max)", readme)
        self.assertIn("[@mallory](https://github.com/few-shots/few-shots/pull/9)", readme)
        self.assertEqual(cache["version"], bb.CACHE_VERSION)

    def test_updated_at_kept_when_unchanged(self):
        board, readme, cache = self.build()
        self.save(board, readme, cache)
        later = NOW + dt.timedelta(hours=1)
        board2, readme2, _ = self.build(now=later)
        self.assertEqual((board2, readme2), (board, readme))
        # 少一道题，内容变化，updated_at 更新
        shutil.rmtree(self.root / "problems" / "p903-demo-empty")
        board3, _, _ = self.build(now=later)
        self.assertEqual(board3["updated_at"], "2001-01-04T13:00:00Z")

    def test_cache_avoids_repeat_calls(self):
        first = FixtureSource()
        board, readme, cache = self.build(first)
        self.save(board, readme, cache)
        second = FixtureSource()
        board2, _, cache2 = self.build(second)
        self.assertGreater(first.calls["merge"], 0)
        self.assertEqual(second.calls["merge"], 0)
        self.assertEqual(second.calls["verdict"], 1)  # 只有没通过核对的 victor/claim 会重新核对
        self.assertEqual((board2, cache2), (board, cache))

    def test_bad_problem_toml_skips_only_that_problem(self):
        board, readme, cache = self.build()
        self.save(board, readme, cache)
        toml = self.root / "problems" / "p902-demo-min" / "problem.toml"
        toml.write_text(toml.read_text("utf-8").replace('id = "p902"', 'id = "p999"'), "utf-8")
        bad = self.root / "problems" / "p905-new"
        bad.mkdir()
        (bad / "problem.toml").write_text("id = ", "utf-8")
        warnings: list[str] = []
        board2, _, cache2 = self.build(warnings=warnings, now=NOW + dt.timedelta(hours=1))
        # p902 沿用上次的数据；p905 从没成功过，暂不上榜；其他题照常
        self.assertEqual(board2, board)
        self.assertTrue(any("p902" in w and "沿用上次" in w for w in warnings), warnings)
        self.assertTrue(any("p905" in w and "暂不上榜" in w for w in warnings), warnings)
        self.assertEqual(cache2, cache)

    def test_nested_front_matter_only_skips_that_shot(self):
        d = self.root / "problems" / "p901-demo-max" / "shots" / "bomb" / "x"
        d.mkdir(parents=True)
        (d / "SHOT.md").write_bytes(b"+++\nx = " + b"[" * 3000 + b"]" * 3000 + b"\n+++\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "bomb")
        warnings: list[str] = []
        board, _, _ = self.build(warnings=warnings)
        self.assertEqual([p["id"] for p in board["problems"]], ["p901", "p902", "p903"])
        self.assertEqual(len(board["problems"][0]["records"]), 4)
        self.assertTrue(any("bomb/x" in w for w in warnings), warnings)

    def test_uncommitted_shot_is_not_counted(self):
        d = self.root / "problems" / "p901-demo-max" / "shots" / "ghost" / "x"
        shutil.copytree(FIXTURES / "problems" / "p901-demo-max" / "shots" / "mallory" / "inject", d)
        (d / "SHOT.md").write_text((d / "SHOT.md").read_text("utf-8").replace('"mallory"', '"ghost"')
                                   .replace('"15"', '"99"'), "utf-8")
        warnings: list[str] = []
        board, _, _ = self.build(warnings=warnings)
        self.assertEqual(board["problems"][0]["current"]["score"], "15")
        self.assertTrue(any("ghost/x" in w for w in warnings), warnings)


class VerificationTest(unittest.TestCase):
    def test_certificate_fills_only_the_template_blanks(self):
        cache = {"merges": {f"{'a' * 40}:problems/p001-demo/shots/alice/s1/SHOT.md":
                            {"at": "2001-01-01T09:00:00Z", "pr": 9,
                             "verification": {"at": "2001-01-01T08:00:05Z", "hash": "f" * 64, "sha": "c" * 40}}}}
        board = {"problems": [{"dir": "problems/p001-demo", "records": [
            {"login": "alice", "shot": "s1", "score": "605", "pr": 9},
            {"login": "bob", "shot": "s2", "score": "606", "pr": 10},          # 没有验证记录：不生成
            {"login": "x|y", "shot": "s3", "score": "607", "pr": 11},          # 名字不合规：不生成
        ]}]}
        out = bb.render_verifications(ROOT, board, cache)
        self.assertEqual(list(out), ["problems/p001-demo/shots/alice/s1/VERIFIED.md"])
        text = out["problems/p001-demo/shots/alice/s1/VERIFIED.md"]
        template = (ROOT / "templates" / "VERIFIED.md").read_text("utf-8")
        self.assertEqual(text, template.format(problem="p001-demo", login="alice", shot="s1", score="605",
                                               verified_at="2001-01-01T08:00:05Z", hash="f" * 64, sha="c" * 40, pr=9))
        self.assertIn("`2001-01-01T08:00:05Z`", text)
        self.assertEqual(bb.exact_decimal("4443683924061973/10000000000"), "444368.3924061973")
        self.assertEqual(bb.exact_decimal("888736808161061/2000000000"), "444368.4040805305")
        self.assertEqual(bb.exact_decimal("1/3"), "1/3")
        self.assertEqual(bb.exact_decimal("105"), "105")

    def test_workflow_copies_certificates_only_into_existing_shot_dirs(self):
        wf = (ROOT / ".github" / "workflows" / "board.yml").read_text("utf-8")
        self.assertIn('"!problems/*/shots/*/*/VERIFIED.md"', wf, "the board's own commit does not retrigger it")
        self.assertIn('optional+=("problems/$pname/shots/$login/$shot/VERIFIED.md")', wf)


class ToolModelTest(unittest.TestCase):
    def test_tool_is_the_tool_itself(self):
        for agent, tool in (("Codex with GPT-6-Astra research subagents", "Codex"), ("Claude Code", "Claude Code"),
                            ("Cursor (agent mode)", "Cursor"), ("Claude Code + MCP", "Claude Code"), ("", "")):
            self.assertEqual(bb.tool_name(agent), tool)


class CommittedStateTest(unittest.TestCase):
    """仓库里提交的 data/board.json、data/cache.json 和 README 记分牌区必须与脚本输出一致。"""

    def test_in_sync(self):
        board, readme, cache = bb.build(ROOT, NoNetwork(), NOW, lambda m: None)
        self.assertEqual(bb.dump_json(board), (ROOT / "data" / "board.json").read_text("utf-8"))
        self.assertEqual(bb.dump_json(cache), (ROOT / "data" / "cache.json").read_text("utf-8"))
        self.assertEqual(readme, (ROOT / "README.md").read_text("utf-8"))
        self.assertEqual([p["id"] for p in board["problems"]], [f"p{i:03d}" for i in range(1, 11)])  # 沙盒题不在里面
        region = readme[readme.index(bb.BOARD_START):readme.index(bb.BOARD_END)]
        self.assertIn(bb.TABLE_HEAD[0], region)
        self.assertNotRegex(region, r"20[0-9]{2}-[01][0-9]-[0-3][0-9]")


class GitHubSourceTest(unittest.TestCase):
    REPO = "few-shots/few-shots"
    PATH = "problems/p001-demo/shots/a/b/SHOT.md"

    def make(self, routes: dict):
        seen: list[str] = []

        def getter(url: str):
            seen.append(url)
            if url not in routes:
                raise AssertionError(f"未预期的请求：{url}")
            return json.dumps(routes[url]).encode(), {}

        return bb.GitHubSource(getter=getter), seen

    def test_merge_info_uses_merged_pr_into_main(self):
        sha = "a" * 40
        src, seen = self.make({f"{bb.API}/repos/{self.REPO}/commits/{sha}/pulls": [
            {"number": 5, "merged_at": None, "base": {"ref": "main", "repo": {"full_name": self.REPO}}},
            {"number": 6, "merged_at": "2001-01-01T09:00:00Z", "base": {"ref": "dev", "repo": {"full_name": self.REPO}}},
            {"number": 8, "merged_at": "2001-01-01T09:30:00Z", "base": {"ref": "main", "repo": {"full_name": "x/y"}}},
            {"number": 7, "merged_at": "2001-01-01T18:00:00+08:00",
             "base": {"ref": "main", "repo": {"full_name": self.REPO}}},
        ]})
        self.assertEqual(src.merge_info(sha, self.PATH), ("2001-01-01T10:00:00Z", 7))
        self.assertEqual(len(seen), 1)

    def test_verification_reads_only_the_bot_data_line(self):
        url = f"{bb.API}/repos/{self.REPO}/issues/9/comments?per_page=100"
        entry = {"at": "2001-01-01T08:00:05Z", "p": "p001-demo", "hash": "f" * 64, "sha": "c" * 40}
        data = bb.DATA_PREFIX + json.dumps({"first": [entry], "v": 2, "valid": []}) + " -->"
        forged = bb.DATA_PREFIX + json.dumps({"first": [{**entry, "at": "1999-01-01T00:00:00Z"}], "v": 2, "valid": []}) + " -->"
        src, _ = self.make({url: [
            {"user": {"login": "mallory"}, "body": bb.SCORE_MARKER + "\n" + forged},
            {"user": {"login": bb.BOT_LOGIN}, "body": bb.SCORE_MARKER + "\n" + data + "\n### Few Shots"},
        ]})
        self.assertEqual(src.verification(9), {"at": "2001-01-01T08:00:05Z", "hash": "f" * 64, "sha": "c" * 40},
                         "a participant's copy of the data line is ignored")
        src, _ = self.make({url: [{"user": {"login": bb.BOT_LOGIN}, "body": "unrelated"}]})
        self.assertIsNone(src.verification(9))

    def test_merge_info_without_pr(self):
        sha = "c" * 40
        src, _ = self.make({f"{bb.API}/repos/{self.REPO}/commits/{sha}/pulls": []})
        self.assertEqual(src.merge_info(sha, self.PATH), (None, None))
        with self.assertRaises(bb.BoardError):
            src.merge_info("../x", self.PATH)

    def verdict_routes(self, *, state="success", creator=bb.BOT_LOGIN, desc="有效突破：15", blob="b" * 40,
                       merged=True):
        head = "d" * 40
        return {
            f"{bb.API}/repos/{self.REPO}/pulls/9": {"number": 9, "head": {"sha": head},
                                                    "merged_at": "2001-01-01T00:00:00Z" if merged else None},
            f"{bb.API}/repos/{self.REPO}/commits/{head}/statuses?per_page=100": [
                {"context": "few-shots/verdict", "state": state, "description": desc, "creator": {"login": creator}},
                {"context": "few-shots/verdict", "state": "failure", "description": "旧的",
                 "creator": {"login": bb.BOT_LOGIN}},
            ],
            f"{bb.API}/repos/{self.REPO}/contents/{self.PATH}?ref={head}": {"sha": blob},
        }

    def test_verdict_requires_bot_breakthrough_and_same_file(self):
        src, _ = self.make(self.verdict_routes())
        self.assertIsNone(src.verdict(9, self.PATH, "b" * 40))
        for kw, frag in ((dict(state="failure"), "few-shots/verdict"),
                         (dict(desc="不涉及任何题目的 shots/，不打分"), "few-shots/verdict"),
                         (dict(creator="mallory"), "few-shots/verdict"),
                         (dict(blob="c" * 40), "不一致"),
                         (dict(merged=False), "没有合并")):
            with self.subTest(**{k: str(v) for k, v in kw.items()}):
                src, _ = self.make(self.verdict_routes(**kw))
                why = src.verdict(9, self.PATH, "b" * 40)
                self.assertIsNotNone(why)
                self.assertIn(frag, why)

    def test_only_repo_api_paths(self):
        src, _ = self.make({})
        with self.assertRaises(bb.BoardError):
            src._json("/user")

    def test_only_get_requests_and_no_redirects(self):
        src = bb.GitHubSource()
        captured = {}

        class FakeResp:
            headers = {}

            def read(self, n):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_open(req, timeout):
            captured["method"] = req.get_method()
            return FakeResp()

        src._opener.open = fake_open  # type: ignore[method-assign]
        src._get(f"{bb.API}/repos/{self.REPO}")
        self.assertEqual(captured["method"], "GET")
        self.assertTrue(any(isinstance(h, bb._NoRedirect) for h in bb.GitHubSource()._opener.handlers))


class LogSafetyTest(unittest.TestCase):
    def test_warn_strips_newlines(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            bb.warn("a\n::set-output name=x::y\r\nb")
        self.assertEqual(buf.getvalue().count("\n"), 1)


if __name__ == "__main__":
    unittest.main()
