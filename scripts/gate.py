#!/usr/bin/env python3
"""PR 门禁：校验文件白名单，并用 GitHub API 把 PR head SHA 上的 shot 文件下载到临时目录。

- 不 checkout PR 分支，不执行 PR 中的任何内容；PR 信息只从 $GITHUB_EVENT_PATH 文件读取。
- 只允许新增 problems/<题目目录>/shots/<PR 作者 login>/<shot-id>/ 下的 .md/.json/.txt/.csv 普通文件；
  题目由路径决定，题目目录必须已在 main 上；一个 PR 只能涉及一道题、一个 shot 目录。
  文件数、单文件大小、总大小都有上限；符号链接、子目录、子模块、隐藏文件一律拒绝。
- 只发 GET 请求。

用法：python3 -I scripts/gate.py --event "$GITHUB_EVENT_PATH" --work work
输出：work/gate.json、work/shot/<文件>；向 $GITHUB_OUTPUT 写 ok=true|false。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fslib as F  # noqa: E402

MAX_LISTED_FILES = 300   # 最多翻三页 PR 文件列表
MAX_MESSAGES = 20
MAX_PR_FILES = 64        # 读题目配置之前的文件数上限（problem.toml 的 max_files 不会超过它）
SHOT_HINT = F.SHOT_DIR_HINT
SHOT_HINT_EN = F.SHOT_DIR_HINT_EN
bi = F.bi


class GateResult(Exception):
    def __init__(self, status: str, messages: list):
        assert status in ("not_shot", "rejected", "error")
        self.status = status
        self.messages = [str(m) for m in messages][:MAX_MESSAGES]
        super().__init__("；".join(self.messages))


@dataclass(frozen=True)
class Target:
    problem: str      # 题目目录名 <id>-<slug>
    login: str
    shot_id: str
    names: tuple

    @property
    def parts(self) -> tuple:
        return (F.PROBLEMS_DIR, self.problem, "shots", self.login, self.shot_id)


def _show(name: object) -> str:
    """只回显符合白名单字符集的路径，其余用占位符代替。"""
    if isinstance(name, str) and len(name) <= 200 and all(c.isascii() and (c.isalnum() or c in "/._-") for c in name):
        return name
    return "（含不允许字符的路径 · path with disallowed characters）"


# ---------------------------------------------------------------- 纯函数（可单测）


def touches_shots(name: object) -> bool:
    """路径看起来是不是在提交 shot：位于 problems/ 或顶层 shots/ 下、且某一级目录名是 shots。
    这样的 PR 一律按 shot 提交检查，路径不完全合规就拒绝，而不是当作"不涉及 shots/"放过。"""
    if not isinstance(name, str):
        return False
    parts = name.split("/")
    return parts[0] in (F.PROBLEMS_DIR, "shots", "") and "shots" in parts


def classify_pr_files(files: list, author: str, base_root: Path) -> tuple[Target, F.Problem]:
    """根据 PR 文件列表判断：是不是 shot 提交、提交到哪道题、是否只在白名单路径内新增文件。"""
    if not isinstance(files, list) or not files:
        raise GateResult("not_shot", [bi("PR 没有改动任何文件", "The PR changes no files")])
    names = [f.get("filename") if isinstance(f, dict) else None for f in files]
    if not any(touches_shots(n) for n in names):
        if len(files) >= MAX_LISTED_FILES:
            raise GateResult("rejected", [bi("PR 改动的文件过多", "The PR changes too many files")])
        raise GateResult("not_shot", [bi("PR 不涉及任何题目的 shots/，不打分",
                                           "The PR does not touch any problem's shots/; not scored")])
    if not F.LOGIN_RE.fullmatch(author or ""):
        raise GateResult("rejected", [bi("PR 作者的登录名不符合规则（机器人账号不能提交 shot）",
                                           "The PR author's login is not allowed (bot accounts cannot submit shots)")])
    if len(files) > MAX_PR_FILES:
        raise GateResult("rejected", [bi(f"一个 shot 最多 {MAX_PR_FILES} 个文件，本 PR 改动了 {len(files)} 个以上",
                                           f"A shot has at most {MAX_PR_FILES} files; this PR changes "
                                           f"{len(files)} or more")])

    errs: list = []
    targets: set = set()
    fnames: list = []
    for f, name in zip(files, names):
        status = f.get("status") if isinstance(f, dict) else None
        if not isinstance(name, str):
            errs.append(bi("PR 文件列表格式异常", "The PR file list is malformed"))
            continue
        parts = name.split("/")
        if len(parts) != 6 or parts[0] != F.PROBLEMS_DIR or parts[2] != "shots":
            errs.append(bi(f"只允许在 {SHOT_HINT} 下新增文件，不允许改动：{_show(name)}",
                              f"Only new files under {SHOT_HINT_EN} are allowed; this change is not: {_show(name)}"))
            continue
        _, pdir, _, login, sid, fname = parts
        if not F.PROBLEM_DIR_RE.fullmatch(pdir):
            errs.append(bi(f"题目目录不合规：{_show(pdir)}（形如 p001-slug）",
                              f"Invalid problem directory: {_show(pdir)} (must look like p001-slug)"))
        if login != author:
            errs.append(bi(f"只能提交到 shots/{author}/ 下（PR 作者本人的目录），不能写入 shots/{_show(login)}/",
                              f"You can only submit under shots/{author}/ (the PR author's own directory), "
                              f"not shots/{_show(login)}/"))
        if not F.SHOT_ID_RE.fullmatch(sid):
            errs.append(bi(f"shot-id 不合规：{_show(sid)}（只能用小写字母、数字、连字符，最长 64）",
                              f"Invalid shot-id: {_show(sid)} (lowercase letters, digits and hyphens only, at most 64)"))
        if fname.lower() in F.RESERVED_FILES:
            errs.append(bi(*F.RESERVED_TEXT))
        elif not F.FILE_NAME_RE.fullmatch(fname):
            errs.append(bi(f"文件名不允许：{_show(fname)}（只允许字母数字开头的 .md/.json/.txt/.csv，不允许隐藏文件）",
                              f"File name not allowed: {_show(fname)} (only .md/.json/.txt/.csv names that start "
                              "with a letter or digit; no hidden files)"))
        if status != "added":
            errs.append(bi(f"只允许新增文件，{_show(name)} 的状态是 {_show(status)}",
                              f"Only added files are allowed; {_show(name)} has status {_show(status)}"))
        targets.add((pdir, login, sid))
        fnames.append(fname)
    if len({t[0] for t in targets}) > 1:
        errs.append(bi("一个 PR 只能提交到一道题", "A PR can submit to only one problem"))
    elif len(targets) > 1:
        errs.append(bi("一个 PR 只能提交一个 shot 目录", "A PR can submit only one shot directory"))
    if errs:
        raise GateResult("rejected", errs)

    pdir, login, sid = next(iter(targets))
    try:
        problem = F.load_problem_dir(base_root, pdir)
    except (F.ConfigError, OSError, ValueError):
        raise GateResult("rejected", [bi(f"main 上没有题目目录 {F.PROBLEMS_DIR}/{_show(pdir)}",
                                           f"There is no problem directory {F.PROBLEMS_DIR}/{_show(pdir)} on main")]) \
            from None
    if len(files) > problem.max_files:
        raise GateResult("rejected", [bi(f"一个 shot 最多 {problem.max_files} 个文件，本 PR 改动了 {len(files)} 个",
                                           f"A shot has at most {problem.max_files} files; this PR changes {len(files)}")])
    if len(set(fnames)) != len(fnames):
        errs.append(bi("文件列表中有重复的文件名", "The file list has duplicate file names"))
    for req in F.REQUIRED_FILES:
        if req not in fnames:
            errs.append(bi(f"缺少 {req}", f"Missing {req}"))
    if (base_root / F.PROBLEMS_DIR / pdir / "shots" / login / sid).exists():
        errs.append(bi("main 上这道题已经有同名的 shot 目录，请换一个 shot-id",
                       "This problem already has a shot directory with this name on main; use another shot-id"))
    if errs:
        raise GateResult("rejected", errs)
    return Target(pdir, login, sid, tuple(sorted(fnames))), problem


def check_tree_entries(entries: list, target: Target, problem: F.Problem) -> dict:
    """核对 PR head 上 shot 目录的 git 树：只能是普通文件，且与文件列表完全一致。"""
    errs: list = []
    by_name: dict = {}
    present: set = set()
    for e in entries if isinstance(entries, list) else []:
        name, mode, typ = e.get("path"), e.get("mode"), e.get("type")
        shown = name if isinstance(name, str) and F.FILE_NAME_RE.fullmatch(name) else _show(name)
        present.add(name)
        if mode == "120000":
            errs.append(bi(f"{shown} 是符号链接，不允许", f"{shown} is a symlink, which is not allowed"))
        elif typ == "tree" or mode == "040000":
            errs.append(bi(f"{shown} 是子目录，不允许", f"{shown} is a subdirectory, which is not allowed"))
        elif typ == "commit" or mode == "160000":
            errs.append(bi(f"{shown} 是子模块，不允许", f"{shown} is a submodule, which is not allowed"))
        elif typ != "blob" or mode not in ("100644", "100755"):
            errs.append(bi(f"{shown} 不是普通文件", f"{shown} is not a regular file"))
        else:
            by_name[name] = e
    if present != set(target.names):
        errs.append(bi("PR head 上的 shot 目录内容与 PR 文件列表不一致",
                       "The shot directory on the PR head does not match the PR file list"))
    total = 0
    for name, e in by_name.items():
        size = e.get("size")
        if type(size) is not int or size < 0:
            errs.append(bi(f"{_show(name)} 的大小未知", f"The size of {_show(name)} is unknown"))
            continue
        limit = problem.file_limit(name)
        if size > limit:
            errs.append(bi(f"{_show(name)} 有 {size} 字节，超过上限 {limit}",
                           f"{_show(name)} is {size} bytes, over the limit of {limit}"))
        total += size
    if total > problem.max_total_bytes:
        errs.append(bi(f"shot 总大小 {total} 字节，超过上限 {problem.max_total_bytes}",
                       f"The shot is {total} bytes in total, over the limit of {problem.max_total_bytes}"))
    if errs:
        raise GateResult("rejected", errs)
    return by_name


def git_blob_sha(data: bytes, algo: str = "sha1") -> str:
    return hashlib.new(algo, b"blob %d\x00" % len(data) + data).hexdigest()


def check_blob(name: str, data: bytes, entry: dict) -> None:
    sha = entry.get("sha", "")
    algo = "sha256" if len(sha) == 64 else "sha1"
    if len(data) != entry.get("size") or git_blob_sha(data, algo) != sha:
        raise GateResult("error", [bi(f"{name} 下载内容校验失败", f"The downloaded content of {name} failed verification")])
    if b"\x00" in data:
        raise GateResult("rejected", [bi(f"{name} 含有 NUL 字节，只允许文本文件",
                                           f"{name} contains NUL bytes; only text files are allowed")])
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        raise GateResult("rejected", [bi(f"{name} 不是 UTF-8 文本", f"{name} is not UTF-8 text")]) from None


# ---------------------------------------------------------------- 访问 GitHub API（只读）


def list_pr_files(api: F.GitHubAPI, repo: str, number: int) -> list:
    files: list = []
    for page in range(1, MAX_LISTED_FILES // 100 + 1):
        items = api.get(f"/repos/{repo}/pulls/{number}/files", {"per_page": 100, "page": page})
        if not isinstance(items, list):
            raise GateResult("error", [bi("PR 文件列表格式异常", "The PR file list is malformed")])
        files.extend(items)
        if len(items) < 100:
            break
    return files


def _sha(value: object) -> str:
    if not isinstance(value, str) or not F.COMMIT_SHA_RE.fullmatch(value):
        raise GateResult("error", [bi("GitHub API 返回了异常的对象 SHA", "The GitHub API returned a malformed object SHA")])
    return value


def fetch_shot(api: F.GitHubAPI, repo: str, head_sha: str, target: Target, problem: F.Problem) -> dict:
    """沿 git 树走到 problems/<题目>/shots/<login>/<shot-id>，逐个校验并下载 blob。"""
    commit = api.get(f"/repos/{repo}/git/commits/{_sha(head_sha)}")
    tree_sha = _sha((commit or {}).get("tree", {}).get("sha"))
    for part in target.parts:
        tree = api.get(f"/repos/{repo}/git/trees/{tree_sha}")
        if not isinstance(tree, dict) or tree.get("truncated") or not isinstance(tree.get("tree"), list):
            raise GateResult("error", [bi("无法读取 PR head 上的目录树", "Cannot read the directory tree on the PR head")])
        entry = next((e for e in tree["tree"] if e.get("path") == part), None)
        if entry is None or entry.get("type") != "tree" or entry.get("mode") != "040000":
            raise GateResult("rejected", [bi(f"PR head 上找不到目录 {_show(part)}（或它不是普通目录）",
                                               f"Directory {_show(part)} is missing on the PR head "
                                               "(or is not a regular directory)")])
        tree_sha = _sha(entry.get("sha"))
    tree = api.get(f"/repos/{repo}/git/trees/{tree_sha}")
    if not isinstance(tree, dict) or tree.get("truncated") or not isinstance(tree.get("tree"), list):
        raise GateResult("error", [bi("无法读取 shot 目录", "Cannot read the shot directory")])
    entries = check_tree_entries(tree["tree"], target, problem)
    blobs: dict = {}
    for name in target.names:
        e = entries[name]
        blob = api.get(f"/repos/{repo}/git/blobs/{_sha(e.get('sha'))}")
        if not isinstance(blob, dict) or blob.get("encoding") != "base64" or not isinstance(blob.get("content"), str):
            raise GateResult("error", [bi(f"{name} 下载失败", f"Downloading {name} failed")])
        data = base64.b64decode(blob["content"].encode("ascii", "ignore"))
        check_blob(name, data, e)
        blobs[name] = data
    return blobs


def write_shot(shot_dir: Path, blobs: dict) -> None:
    """写到临时目录；目录 0755、文件 0644，供容器内 uid 65534 只读挂载。"""
    if shot_dir.exists():
        shutil.rmtree(shot_dir)
    shot_dir.mkdir(parents=True)
    os.chmod(shot_dir, 0o755)
    for name, data in blobs.items():
        assert F.FILE_NAME_RE.fullmatch(name)
        fd = os.open(shot_dir / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(shot_dir / name, 0o644)


def parse_event(event: dict) -> tuple:
    try:
        pr = event["pull_request"]
        number = pr["number"]
        author = pr["user"]["login"]
        head_sha = pr["head"]["sha"]
        repo = event["repository"]["full_name"]
    except (KeyError, TypeError):
        raise GateResult("error", [bi("事件数据缺少 pull_request 字段",
                                        "The event data is missing pull_request fields")]) from None
    if type(number) is not int or number <= 0 or not isinstance(author, str):
        raise GateResult("error", [bi("事件数据格式异常", "The event data is malformed")])
    if not isinstance(repo, str) or not F.REPO_RE.fullmatch(repo):
        raise GateResult("error", [bi("仓库名格式异常", "The repository name is malformed")])
    return number, author, _sha(head_sha), repo


def run(event: dict, api: F.GitHubAPI, base_root: Path, work: Path) -> dict:
    result = {"schema": 1, "ok": False, "status": "error", "messages": [], "pr": None, "shot": None}
    try:
        number, author, head_sha, repo = parse_event(event)
        result["pr"] = {"number": number, "author": author if F.LOGIN_RE.fullmatch(author) else None,
                        "head_sha": head_sha}
        files = list_pr_files(api, repo, number)
        target, problem = classify_pr_files(files, author, base_root)
        blobs = fetch_shot(api, repo, head_sha, target, problem)
        write_shot(work / "shot", blobs)
        result.update(ok=True, status="ok", messages=[], shot={
            "problem": target.problem, "login": target.login, "shot_id": target.shot_id,
            "files": [{"name": n, "size": len(blobs[n])} for n in target.names],
        })
    except GateResult as g:
        result.update(status=g.status, messages=g.messages)
    except F.GitHubAPIError as e:
        result.update(status="error", messages=[bi(f"GitHub API 调用失败（HTTP {e.status}）",
                                                    f"A GitHub API call failed (HTTP {e.status})")])
    except Exception as e:  # 不把异常细节回显给 PR
        print(f"gate internal error: {type(e).__name__}", file=sys.stderr)
        result.update(status="error", messages=[bi("门禁内部错误，请联系裁判组",
                                                    "Internal error in the gate; please contact the referees")])
    return result


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--event", required=True, help="GitHub 事件 JSON 文件（$GITHUB_EVENT_PATH）")
    ap.add_argument("--work", default="work", help="工作目录")
    ap.add_argument("--root", default=str(F.REPO_ROOT), help="main 的 checkout 目录")
    args = ap.parse_args(argv)
    work = Path(args.work).absolute()
    work.mkdir(parents=True, exist_ok=True)
    root = Path(args.root)
    event = F.read_json_file(args.event, 25 * 1024 * 1024)
    api = F.GitHubAPI(os.environ.get("GITHUB_TOKEN") or None, allow_write=False)
    result = run(event, api, root, work)
    F.write_json_file(work / "gate.json", result)
    F.set_output("ok", "true" if result["ok"] else "false")
    print(f"gate: {result['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
