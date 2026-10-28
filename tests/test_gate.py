"""门禁测试：文件白名单、git 树检查、下载校验，以及完整流程（用假的只读 API）。"""

from __future__ import annotations

import base64
import json
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from helpers import OPTIMAL_12, ROOT, SANDBOX, F, cert, copy_problem, shot_md

sys.path.insert(0, str(ROOT / "scripts"))
import gate as G  # noqa: E402

AUTHOR = "alice"
HEAD = "a" * 40
P = f"problems/{SANDBOX}"


def added(*paths: str, status: str = "added") -> list:
    return [{"filename": p, "status": status} for p in paths]


def ok_files(login: str = AUTHOR, sid: str = "first-try", problem: str = SANDBOX) -> list:
    d = f"problems/{problem}/shots/{login}/{sid}"
    return added(f"{d}/SHOT.md", f"{d}/certificate.json")


class ClassifyTest(unittest.TestCase):
    def setUp(self):
        self.problem = F.load_problem_dir(ROOT, SANDBOX)
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        copy_problem(ROOT, self.base)
        copy_problem(ROOT, self.base, new_name="p001-other")

    def tearDown(self):
        self._tmp.cleanup()

    def classify(self, files, author=AUTHOR):
        target, problem = G.classify_pr_files(files, author, self.base)
        self.assertEqual(problem.dir, target.problem)
        return target

    def assertGate(self, status, files, author=AUTHOR, fragment=""):
        with self.assertRaises(G.GateResult) as cm:
            self.classify(files, author)
        self.assertEqual(cm.exception.status, status, cm.exception.messages)
        if fragment:
            self.assertTrue(any(fragment in m for m in cm.exception.messages), cm.exception.messages)
        return cm.exception

    def test_valid_shot(self):
        files = ok_files() + added(f"{P}/shots/alice/first-try/notes.md", f"{P}/shots/alice/first-try/data.csv",
                                   f"{P}/shots/alice/first-try/log.txt")
        t = self.classify(files)
        self.assertEqual((t.problem, t.login, t.shot_id), (SANDBOX, "alice", "first-try"))
        self.assertEqual(t.names, ("SHOT.md", "certificate.json", "data.csv", "log.txt", "notes.md"))
        self.assertEqual(t.parts, ("problems", SANDBOX, "shots", "alice", "first-try"))

    def test_verification_certificate_cannot_be_submitted(self):
        for name in ("VERIFIED.md", "verified.md"):
            self.assertGate("rejected", ok_files() + added(f"{P}/shots/alice/first-try/{name}"), fragment="VERIFIED.md")

    def test_problem_is_taken_from_the_path(self):
        t = self.classify(ok_files(problem="p001-other"))
        self.assertEqual(t.problem, "p001-other")

    def test_not_a_shot_pr(self):
        self.assertGate("not_shot", added("README.md", f"{P}/verify/verify.py", "control.toml"))
        self.assertGate("not_shot", added(f"{P}/FEWSHOT.md", "tests/fixtures/board/problems/p901-x/shots/a/b/SHOT.md"))
        self.assertGate("not_shot", [])
        # 旧的顶层 shots/ 路径按 shot 提交检查并拒绝
        self.assertGate("rejected", added("shots/alice/x/SHOT.md", "shots/alice/x/certificate.json"),
                        fragment="只允许在 problems/<题目目录>/shots/")

    def test_paths_outside_whitelist(self):
        for extra in (".github/workflows/score.yml", f"{P}/verify/verify.py", "scripts/score.py",
                      f"{P}/problem.toml", f"{P}/FEWSHOT.md", f"{P}/PROBLEM.md", "control.toml",
                      "data/board.json", "README.md", f"{P}/shots/README.md", f"{P}/shots/alice/x.json",
                      "shots/alice/first-try/SHOT.md"):
            self.assertGate("rejected", ok_files() + added(extra), fragment="只允许在 problems/<题目目录>/shots/")

    def test_two_problems_in_one_pr(self):
        exc = self.assertGate("rejected", ok_files() + ok_files(problem="p001-other"), fragment="只能提交到一道题")
        self.assertFalse(any("只能提交一个 shot" in m for m in exc.messages))

    def test_unknown_or_malformed_problem_dir(self):
        self.assertGate("rejected", ok_files(problem="p999-nothing"), fragment="main 上没有题目目录")
        for bad in ("P000-sandbox", "p0-x", "p000_sandbox", "p000-", "sandbox", "p000-Sandbox", "..", "."):
            self.assertGate("rejected", ok_files(problem=bad), fragment="题目目录不合规")

    def test_problem_dir_must_match_problem_toml(self):
        # 目录名和 problem.toml 的 id、slug 对不上时，不认这个题目目录
        (self.base / "problems" / "p001-other").rename(self.base / "problems" / "p002-renamed")
        self.assertGate("rejected", ok_files(problem="p002-renamed"), fragment="main 上没有题目目录")

    def test_symlinked_problem_dir_is_not_a_problem(self):
        os.symlink(self.base / "problems" / SANDBOX, self.base / "problems" / "p003-link")
        self.assertGate("rejected", ok_files(problem="p003-link"), fragment="main 上没有题目目录")

    def test_path_traversal(self):
        for files in (
            added(f"problems/../shots/alice/x/SHOT.md", f"problems/../shots/alice/x/certificate.json"),
            added(f"{P}/shots/../shots/alice/SHOT.md"),
            added(f"{P}/shots/alice/../x/SHOT.md", f"{P}/shots/alice/x/certificate.json"),
            added(f"{P}/shots/alice/x/../SHOT.md", f"{P}/shots/alice/x/certificate.json"),
            added(f"problems/{SANDBOX}/../p001-other/shots/alice/x/SHOT.md"),
            added(f"/{P}/shots/alice/x/SHOT.md", f"{P}/shots/alice/x/certificate.json"),
            added(f"{P}//shots/alice/x/SHOT.md"),
        ):
            with self.subTest(files=files):
                self.assertGate("rejected", files)

    def test_other_users_login(self):
        self.assertGate("rejected", ok_files(login="bob"), fragment="只能提交到 shots/alice/")
        self.assertGate("rejected", ok_files(login="Alice"), fragment="只能提交到")

    def test_bot_author_rejected(self):
        self.assertGate("rejected", ok_files(login="dependabot[bot]"), author="dependabot[bot]",
                        fragment="机器人")

    def test_hidden_and_disallowed_files(self):
        for name in (".hidden.json", ".SHOT.md", "run.py", "run.sh", "cert.JSON", "a.b.json",
                     "image.png", "-x.md", "_x.md", "空格 名.md", "x.md​", "x.json.exe"):
            self.assertGate("rejected", ok_files() + added(f"{P}/shots/alice/first-try/{name}"),
                            fragment="文件名不允许")

    def test_subdirectories(self):
        self.assertGate("rejected", ok_files() + added(f"{P}/shots/alice/first-try/sub/a.json"))
        self.assertGate("rejected", ok_files(sid="Bad_ID"), fragment="shot-id 不合规")
        self.assertGate("rejected", ok_files(sid="x" * 65), fragment="shot-id 不合规")

    def test_symlink_like_names_are_only_names_here(self):
        # 文件列表看不出符号链接，必须由 git 树检查兜底（见 TreeTest.test_symlink_rejected）
        t = self.classify(ok_files())
        self.assertIn("certificate.json", t.names)

    def test_only_additions_allowed(self):
        for status in ("modified", "removed", "renamed", "copied", "changed"):
            files = ok_files()
            files[0]["status"] = status
            self.assertGate("rejected", files, fragment="只允许新增文件")

    def test_too_many_files(self):
        files = ok_files() + added(*[f"{P}/shots/alice/first-try/n{i}.md" for i in range(self.problem.max_files)])
        self.assertGate("rejected", files, fragment="最多")
        files = ok_files() + added(*[f"{P}/shots/alice/first-try/n{i}.md" for i in range(G.MAX_PR_FILES)])
        self.assertGate("rejected", files, fragment="最多")

    def test_one_shot_per_pr(self):
        self.assertGate("rejected", ok_files(sid="one") + ok_files(sid="two"), fragment="只能提交一个")

    def test_required_files(self):
        self.assertGate("rejected", added(f"{P}/shots/alice/x/SHOT.md"), fragment="缺少 certificate.json")
        self.assertGate("rejected", added(f"{P}/shots/alice/x/certificate.json"), fragment="缺少 SHOT.md")

    def test_existing_shot_dir_on_main(self):
        (self.base / "problems" / SANDBOX / "shots" / "alice" / "first-try").mkdir(parents=True)
        self.assertGate("rejected", ok_files(), fragment="已经有同名")
        # 同名的 shot 在另一道题里不冲突
        self.assertEqual(self.classify(ok_files(problem="p001-other")).problem, "p001-other")

    def test_messages_do_not_echo_odd_characters(self):
        exc = self.assertGate("rejected", ok_files() + added(f"{P}/shots/alice/first-try/<img src=x>.md"))
        self.assertFalse(any("<img" in m for m in exc.messages))
        exc = self.assertGate("rejected", ok_files(problem="p001-<img src=x>"))
        self.assertFalse(any("<img" in m for m in exc.messages))


class TreeTest(unittest.TestCase):
    def setUp(self):
        self.problem = F.load_problem_dir(ROOT, SANDBOX)
        self.target = G.Target(SANDBOX, "alice", "first-try", ("SHOT.md", "certificate.json"))

    def entry(self, path, size=100, mode="100644", typ="blob"):
        return {"path": path, "mode": mode, "type": typ, "size": size, "sha": "b" * 40}

    def check(self, entries):
        return G.check_tree_entries(entries, self.target, self.problem)

    def assertRejected(self, entries, fragment=""):
        with self.assertRaises(G.GateResult) as cm:
            self.check(entries)
        self.assertEqual(cm.exception.status, "rejected")
        self.assertTrue(any(fragment in m for m in cm.exception.messages), cm.exception.messages)

    def test_ok(self):
        out = self.check([self.entry("SHOT.md"), self.entry("certificate.json", mode="100755")])
        self.assertEqual(set(out), {"SHOT.md", "certificate.json"})

    def test_symlink_rejected(self):
        self.assertRejected([self.entry("SHOT.md"), self.entry("certificate.json", mode="120000")], "符号链接")

    def test_submodule_and_subdir_rejected(self):
        self.assertRejected([self.entry("SHOT.md"), self.entry("certificate.json", mode="160000", typ="commit")],
                            "子模块")
        self.assertRejected([self.entry("SHOT.md"), self.entry("certificate.json", mode="040000", typ="tree")],
                            "子目录")

    def test_extra_entry_in_tree(self):
        self.assertRejected([self.entry("SHOT.md"), self.entry("certificate.json"), self.entry("x.md")],
                            "不一致")

    def test_oversized_files(self):
        limit = self.problem.max_certificate_bytes
        self.assertRejected([self.entry("SHOT.md"), self.entry("certificate.json", size=limit + 1)], "超过上限")
        self.assertRejected([self.entry("SHOT.md", size=self.problem.max_shot_md_bytes + 1),
                             self.entry("certificate.json")], "超过上限")

    def test_total_size(self):
        names = ("SHOT.md", "certificate.json", "a.md", "b.md", "c.md")
        target = G.Target(SANDBOX, "alice", "first-try", names)
        size = self.problem.max_total_bytes // 4
        entries = [self.entry("SHOT.md"), self.entry("certificate.json")] + \
                  [self.entry(n, size=size) for n in ("a.md", "b.md", "c.md")]
        entries[2]["size"] = self.problem.max_file_bytes  # 单个不超
        with self.assertRaises(G.GateResult) as cm:
            G.check_tree_entries(entries, target, self.problem)
        self.assertTrue(any("总大小" in m for m in cm.exception.messages), cm.exception.messages)


class BlobTest(unittest.TestCase):
    def entry(self, data):
        return {"size": len(data), "sha": G.git_blob_sha(data)}

    def test_git_blob_sha_matches_git(self):
        self.assertEqual(G.git_blob_sha(b""), "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391")

    def test_tampered_content(self):
        with self.assertRaises(G.GateResult) as cm:
            G.check_blob("certificate.json", b"{}", self.entry(b"[]"))
        self.assertEqual(cm.exception.status, "error")

    def test_binary_content(self):
        for data in (b"\x00\x01", b"\xff\xfe\xfd"):
            with self.assertRaises(G.GateResult) as cm:
                G.check_blob("notes.md", data, self.entry(data))
            self.assertEqual(cm.exception.status, "rejected")


class FakeAPI:
    """只读的假 GitHub API：记录请求，任何非 GET 都会失败。"""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls = []

    def get(self, path, params=None):
        self.calls.append(path)
        if path not in self.routes:
            raise F.GitHubAPIError(404)
        value = self.routes[path]
        if isinstance(value, list) and params and params.get("page", 1) > 1:
            return []
        return value

    def request(self, method, path, params=None, body=None):
        raise AssertionError("gate 不应发出非 GET 请求")


def build_routes(repo, files: dict, pr_files=None, modes=None):
    """files: {name: bytes}，生成 PR 文件列表、提交、树、blob。"""
    modes = modes or {}
    routes = {}
    entries = []
    for i, (name, data) in enumerate(sorted(files.items())):
        sha = G.git_blob_sha(data)
        entries.append({"path": name, "mode": modes.get(name, "100644"), "type": "blob",
                        "size": len(data), "sha": sha})
        routes[f"/repos/{repo}/git/blobs/{sha}"] = {
            "encoding": "base64", "size": len(data),
            "content": base64.encodebytes(data).decode("ascii"),
        }
    shot_tree, login_tree, shots_tree, root_tree = "1" * 40, "2" * 40, "3" * 40, "4" * 40
    problems_tree, problem_tree = "5" * 40, "6" * 40
    routes[f"/repos/{repo}/git/commits/{HEAD}"] = {"tree": {"sha": root_tree}}
    routes[f"/repos/{repo}/git/trees/{root_tree}"] = {"truncated": False, "tree": [
        {"path": "problems", "mode": "040000", "type": "tree", "sha": problems_tree}]}
    routes[f"/repos/{repo}/git/trees/{problems_tree}"] = {"truncated": False, "tree": [
        {"path": SANDBOX, "mode": "040000", "type": "tree", "sha": problem_tree}]}
    routes[f"/repos/{repo}/git/trees/{problem_tree}"] = {"truncated": False, "tree": [
        {"path": "shots", "mode": "040000", "type": "tree", "sha": shots_tree}]}
    routes[f"/repos/{repo}/git/trees/{shots_tree}"] = {"truncated": False, "tree": [
        {"path": "alice", "mode": "040000", "type": "tree", "sha": login_tree}]}
    routes[f"/repos/{repo}/git/trees/{login_tree}"] = {"truncated": False, "tree": [
        {"path": "first-try", "mode": "040000", "type": "tree", "sha": shot_tree}]}
    routes[f"/repos/{repo}/git/trees/{shot_tree}"] = {"truncated": False, "tree": entries}
    routes[f"/repos/{repo}/pulls/7/files"] = pr_files if pr_files is not None else \
        added(*[f"{P}/shots/alice/first-try/{n}" for n in files])
    return routes


class RunTest(unittest.TestCase):
    repo = "few-shots/few-shots"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.event = {"pull_request": {"number": 7, "user": {"login": AUTHOR}, "head": {"sha": HEAD}},
                      "repository": {"full_name": self.repo}}

    def tearDown(self):
        self._tmp.cleanup()

    def run_gate(self, routes):
        api = FakeAPI(routes)
        work = self.tmp / "work"
        return G.run(self.event, api, ROOT, work), api, work

    def test_happy_path_downloads_files_with_safe_modes(self):
        files = {"SHOT.md": shot_md("alice", "85").encode(), "certificate.json": cert(OPTIMAL_12).encode()}
        res, api, work = self.run_gate(build_routes(self.repo, files))
        self.assertTrue(res["ok"], res)
        self.assertEqual((res["shot"]["problem"], res["shot"]["login"]), (SANDBOX, "alice"))
        for name, data in files.items():
            p = work / "shot" / name
            self.assertEqual(p.read_bytes(), data)
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE((work / "shot").stat().st_mode), 0o755)
        self.assertTrue(all(c.startswith(f"/repos/{self.repo}/") for c in api.calls))

    def test_problem_dir_symlink_in_head_tree_rejected(self):
        files = {"SHOT.md": shot_md("alice", "85").encode(), "certificate.json": cert(OPTIMAL_12).encode()}
        routes = build_routes(self.repo, files)
        routes[f"/repos/{self.repo}/git/trees/{'5' * 40}"]["tree"][0]["mode"] = "120000"
        res, _, work = self.run_gate(routes)
        self.assertEqual(res["status"], "rejected")
        self.assertFalse((work / "shot").exists())

    def test_symlink_in_head_tree_rejected(self):
        files = {"SHOT.md": shot_md("alice", "85").encode(), "certificate.json": b"../../etc/passwd"}
        res, _, work = self.run_gate(build_routes(self.repo, files, modes={"certificate.json": "120000"}))
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "rejected")
        self.assertFalse((work / "shot").exists())

    def test_tampered_blob_is_error(self):
        files = {"SHOT.md": b"x", "certificate.json": b"{}"}
        routes = build_routes(self.repo, files)
        sha = G.git_blob_sha(b"{}")
        routes[f"/repos/{self.repo}/git/blobs/{sha}"]["content"] = base64.b64encode(b"[]").decode()
        res, _, _ = self.run_gate(routes)
        self.assertEqual(res["status"], "error")

    def test_not_shot_and_rejections_flow_through(self):
        res, _, _ = self.run_gate(build_routes(self.repo, {"SHOT.md": b"x", "certificate.json": b"{}"},
                                               pr_files=added("README.md")))
        self.assertEqual(res["status"], "not_shot")
        res, _, _ = self.run_gate(build_routes(self.repo, {"SHOT.md": b"x", "certificate.json": b"{}"},
                                               pr_files=ok_files(login="bob")))
        self.assertEqual(res["status"], "rejected")

    def test_api_failure_is_error(self):
        res, _, _ = self.run_gate({})
        self.assertEqual(res["status"], "error")
        self.assertIn("HTTP 404", res["messages"][0])

    def test_bad_event(self):
        self.event["pull_request"]["head"]["sha"] = "$(id)"
        res, api, _ = self.run_gate(build_routes(self.repo, {"SHOT.md": b"x", "certificate.json": b"{}"}))
        self.assertEqual(res["status"], "error")
        self.assertEqual(api.calls, [])

    def test_main_writes_gate_json_and_output(self):
        event_path = self.tmp / "event.json"
        event_path.write_text(json.dumps(self.event))
        out = self.tmp / "gh_output"
        os.environ["GITHUB_OUTPUT"] = str(out)
        try:
            # API 调用失败时也必须产出 ok=false 与 gate.json
            with mock.patch.object(F.GitHubAPI, "get", side_effect=F.GitHubAPIError(503)):
                G.main(["--event", str(event_path), "--work", str(self.tmp / "w"), "--root", str(ROOT)])
        finally:
            del os.environ["GITHUB_OUTPUT"]
        self.assertEqual(out.read_text(), "ok=false\n")
        gate = json.loads((self.tmp / "w" / "gate.json").read_text())
        self.assertEqual(gate["status"], "error")


class ReadOnlyClientTest(unittest.TestCase):
    def test_gate_client_refuses_writes(self):
        api = F.GitHubAPI(None, allow_write=False)
        for method in ("POST", "PATCH", "PUT", "DELETE"):
            with self.assertRaises(PermissionError):
                api.request(method, "/repos/a/b/issues/1/comments", body={})

    def test_bad_paths_refused(self):
        api = F.GitHubAPI(None)
        for path in ("repos/a/b", "/repos/a/../b", "/repos/a/b?x=1"):
            with self.assertRaises(ValueError):
                api.get(path)


if __name__ == "__main__":
    unittest.main()
