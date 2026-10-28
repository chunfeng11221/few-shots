#!/usr/bin/env python3
"""Few Shots 记分牌生成器（只用 Python 3.12 标准库）。

读 main 的 checkout：problems/<题目>/problem.toml 与 problems/<题目>/shots/<login>/<shot-id>/SHOT.md，
按 main 第一父链上的合并顺序排列 → 用 GitHub API（只读）核对每个 shot 的合并 PR 上有打分机器人写的
few-shots/verdict 有效突破状态 → 从预设基线出发算纪录链和进度 → 写 data/board.json，
并重写 README.md 中 <!-- board:start --> 与 <!-- board:end --> 之间的记分牌区。

安全约束：只发 GET 请求，只访问 api.github.com，不跟随重定向；
SHOT.md 是第三方数据，只按 TOML 解析（scripts/fslib.py），不执行、不内插进命令；
写进 README 和日志的第三方文本一律清洗、转义。
记分牌不直接相信 SHOT.md 里的 claimed_score：只有合并 PR 的 head 上有机器人判定的有效突破、
且 main 上的 SHOT.md 与打分时完全相同的 shot 才计入纪录链。
一道题读取失败只影响这一道题（沿用上次的数据），不中断整个构建。沙盒题不上榜。

用法：python3 -I scripts/build_board.py        # 需要网络；设置 GITHUB_TOKEN 可提高 API 限额
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Protocol

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fslib as F  # noqa: E402

API = "https://api.github.com"
BOARD_START = "<!-- board:start -->"
BOARD_END = "<!-- board:end -->"
CACHE_VERSION = 4
VERDICT_CONTEXT = "few-shots/verdict"
VERDICT_PREFIX = "有效突破"
BOT_LOGIN = "github-actions[bot]"
SCORE_MARKER = "<!-- few-shots:score -->"     # 打分机器人回帖的第一行（scripts/report.py MARKER）
DATA_PREFIX = "<!-- few-shots:data "          # 第二行：隐藏数据，含首次判为有效突破的时间
MAX_RESPONSE = 32 * 1024 * 1024
MAX_SHOTS_PER_PROBLEM = 1000
MAX_MODELS = 10
MAX_MODEL_LEN = 100
REGULAR_MODES = frozenset({"100644", "100755"})
SHA_RE = F.COMMIT_SHA_RE
HASH_RE = F.HASH_RE
MD_SPECIAL_RE = re.compile(r"([\\`*_{}\[\]()<>#+!|~&])")
TABLE_HEAD = (
    "| 题目 · Problem | 指标 · Metric | 预设基线 · Baseline | 当前纪录 · Record | 纪录保持者 · Holder "
    "| 有效突破 · Breakthroughs | 进度 · Progress |",
    "| --- | --- | ---: | ---: | --- | ---: | ---: |",
)


class BoardError(Exception):
    """这一道题（或整次构建）失败：不更新它的数据。"""


class ShotSkip(Exception):
    """单个 shot 不计入：跳过并告警。"""


# ---------- 基础 ----------

def iso_utc(value: str) -> str:
    t = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError("时间缺少时区")
    return t.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean_text(value: str, limit: int) -> str:
    """去掉控制字符和换行，压缩空白，截断。"""
    s = "".join(ch if ch.isprintable() else " " for ch in value)
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _git(root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=60, env=F._git_env())
    except (OSError, subprocess.SubprocessError):
        raise BoardError("无法运行 git") from None
    if proc.returncode != 0:
        raise BoardError("读取 git 历史失败")
    return proc.stdout.decode("utf-8", "replace")


def tree_entries(root: Path, ref: str, prefix: str) -> dict[str, tuple[str, str]]:
    """main 上 prefix 下所有文件：{路径: (mode, blob sha)}。"""
    out: dict[str, tuple[str, str]] = {}
    for item in _git(root, "ls-tree", "-r", "-z", "--full-tree", ref, "--", prefix).split("\0"):
        if not item:
            continue
        meta, _, path = item.partition("\t")
        parts = meta.split()
        if len(parts) == 3:
            out[path] = (parts[0], parts[2])
    return out


def commit_time(root: Path, sha: str) -> str:
    if not SHA_RE.fullmatch(sha):
        raise BoardError("提交 sha 格式异常")
    return iso_utc(_git(root, "show", "-s", "--format=%cI", sha).strip())


# ---------- 读 GitHub（只读） ----------

class Source(Protocol):
    def merge_info(self, commit: str, path: str) -> tuple[str | None, int | None]: ...
    def verdict(self, pr: int, path: str, blob_sha: str) -> str | None: ...
    def verification(self, pr: int) -> dict | None: ...


Getter = Callable[[str], tuple[bytes, dict[str, str]]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None  # 不跟随重定向，避免把令牌带到别的地址


class GitHubSource:
    """通过 GitHub REST API 只读地查本仓库的 PR 与提交状态。"""

    def __init__(self, token: str | None = None, repo: str = F.REPO, branch: str = "main",
                 getter: Getter | None = None):
        self.token = token
        self.repo = repo
        self.branch = branch
        self._get = getter or self._urlopen
        self._opener = urllib.request.build_opener(_NoRedirect)

    def _urlopen(self, url: str) -> tuple[bytes, dict[str, str]]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "few-shots-board",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with self._opener.open(req, timeout=30) as resp:
                body = resp.read(MAX_RESPONSE + 1)
                return body, {k.lower(): v for k, v in resp.headers.items()}
        except urllib.error.HTTPError as e:
            raise BoardError(f"GitHub API 返回 {e.code}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise BoardError(f"无法访问 GitHub API：{type(e).__name__}") from None

    def _json(self, path: str, params: dict[str, str] | None = None) -> Any:
        url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
        if not url.startswith(API + "/repos/"):
            raise BoardError("只允许访问 api.github.com")
        body, _headers = self._get(url)
        if len(body) > MAX_RESPONSE:
            raise BoardError("GitHub API 响应过大")
        try:
            return json.loads(body)
        except ValueError:
            raise BoardError("GitHub API 返回的不是 JSON") from None

    def merge_info(self, commit: str, path: str) -> tuple[str | None, int | None]:
        """引入这个 SHOT.md 的提交对应的已合并 PR：返回 (merged_at, PR 号)；找不到返回 (None, None)。"""
        if not SHA_RE.fullmatch(commit):
            raise BoardError("提交 sha 格式异常")
        pulls = self._json(f"/repos/{self.repo}/commits/{commit}/pulls")
        merged: list[tuple[str, int]] = []
        for p in pulls if isinstance(pulls, list) else []:
            try:
                base = p["base"]
                if p.get("merged_at") and base["ref"] == self.branch \
                        and base["repo"]["full_name"].lower() == self.repo.lower():
                    merged.append((iso_utc(p["merged_at"]), int(p["number"])))
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
        return min(merged) if merged else (None, None)

    def verification(self, pr: int) -> dict | None:
        """验证器首次判定这个 PR 为有效突破的记录 {at, hash, sha}：打分机器人回帖第二行隐藏数据里的 first[]
        （时间是服务器时间）。只认机器人账号写的回帖；读不到返回 None。纪录的先后、显示的时间和验证证书都以它为准，
        不看 Git 提交时间。"""
        comments = self._json(f"/repos/{self.repo}/issues/{int(pr)}/comments", {"per_page": "100"})
        for c in comments if isinstance(comments, list) else []:
            if not isinstance(c, dict) or (c.get("user") or {}).get("login") != BOT_LOGIN:
                continue
            lines = str(c.get("body") or "").split("\n")
            if len(lines) < 2 or lines[0] != SCORE_MARKER or not (lines[1].startswith(DATA_PREFIX) and lines[1].endswith(" -->")):
                continue
            try:
                data = json.loads(lines[1][len(DATA_PREFIX):-len(" -->")])
                found = [{"at": iso_utc(e["at"]), "hash": e["hash"], "sha": e["sha"]} for e in data.get("first") or []
                         if isinstance(e, dict) and isinstance(e.get("at"), str)
                         and HASH_RE.fullmatch(str(e.get("hash"))) and SHA_RE.fullmatch(str(e.get("sha")))]
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            if found:
                return min(found, key=lambda e: e["at"])
        return None

    def verdict(self, pr: int, path: str, blob_sha: str) -> str | None:
        """核对打分机器人的判定：合并 PR 的 head 上最新的 few-shots/verdict 由 github-actions[bot]
        写成"有效突破"，且该 head 上的 SHOT.md 与 main 上的完全相同。通过返回 None，否则返回原因。"""
        pull = self._json(f"/repos/{self.repo}/pulls/{int(pr)}")
        if not isinstance(pull, dict) or not pull.get("merged_at"):
            return "对应的 PR 没有合并记录"
        head = (pull.get("head") or {}).get("sha") if isinstance(pull.get("head"), dict) else None
        if not isinstance(head, str) or not SHA_RE.fullmatch(head):
            return "对应 PR 的 head 提交格式异常"
        statuses = self._json(f"/repos/{self.repo}/commits/{head}/statuses", {"per_page": "100"})
        latest = None
        for st in statuses if isinstance(statuses, list) else []:  # 新的在前
            if isinstance(st, dict) and st.get("context") == VERDICT_CONTEXT \
                    and (st.get("creator") or {}).get("login") == BOT_LOGIN:
                latest = st
                break
        if latest is None or latest.get("state") != "success" \
                or not str(latest.get("description") or "").startswith(VERDICT_PREFIX):
            return f"合并 PR 的 head 上没有机器人判定的有效突破（{VERDICT_CONTEXT}）"
        content = self._json(f"/repos/{self.repo}/contents/{urllib.parse.quote(path, safe='/')}", {"ref": head})
        if not isinstance(content, dict) or content.get("sha") != blob_sha:
            return "main 上的 SHOT.md 与打分时的不一致"
        return None


# ---------- 缓存（减少 API 调用；不是对外接口） ----------

def empty_cache() -> dict[str, Any]:
    return {"version": CACHE_VERSION, "merges": {}, "verdicts": {}}


def load_cache(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, ValueError):
        return empty_cache()
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
        return empty_cache()
    merges, verdicts = data.get("merges"), data.get("verdicts")
    return {
        "version": CACHE_VERSION,
        "merges": merges if isinstance(merges, dict) else {},
        "verdicts": verdicts if isinstance(verdicts, dict) else {},
    }


def shot_merge(source: Source, root: Path, commit: str, path: str, cache_in: dict, cache_out: dict) -> tuple[str, int | None, dict | None]:
    """合并时间、PR 号和验证记录 {at, hash, sha}；按"提交:路径"缓存。没有 PR 时取提交时间、没有验证记录。"""
    key = f"{commit}:{path}"
    hit = cache_in["merges"].get(key)
    at: str | None = None
    pr: int | None = None
    verified: dict | None = None
    cached_verified = False
    if isinstance(hit, dict) and isinstance(hit.get("at"), str):
        pr_hit = hit.get("pr")
        if pr_hit is None or (type(pr_hit) is int and pr_hit > 0):
            try:
                at, pr = iso_utc(hit["at"]), pr_hit
                if "verification" in hit:
                    verified = _verification(hit["verification"])
                    cached_verified = True
            except ValueError:
                at = None
    if at is None:
        raw_at, pr = source.merge_info(commit, path)
        try:
            at = iso_utc(raw_at) if raw_at else commit_time(root, commit)
        except ValueError:
            raise BoardError("合并时间格式异常") from None
    if not cached_verified:
        verified = _verification(source.verification(pr)) if pr is not None and hasattr(source, "verification") else None
    cache_out["merges"][key] = {"at": at, "pr": pr, "verification": verified}
    return at, pr, verified


def _verification(v: Any) -> dict | None:
    """校验一份验证记录（来自 API 或缓存）：{at: UTC 时间, hash: 64 位十六进制, sha: 40 位十六进制}。"""
    if not isinstance(v, dict) or not isinstance(v.get("at"), str):
        return None
    try:
        at = iso_utc(v["at"])
    except ValueError:
        return None
    if not (HASH_RE.fullmatch(str(v.get("hash"))) and SHA_RE.fullmatch(str(v.get("sha")))):
        return None
    return {"at": at, "hash": v["hash"], "sha": v["sha"]}


def shot_verdict(source: Source, pr: int | None, path: str, blob: str, cache_in: dict, cache_out: dict) -> None:
    """只缓存通过的核对结果（键含路径、blob sha 与 PR 号）；不通过时抛 ShotSkip。"""
    if pr is None:
        raise ShotSkip("找不到对应的已合并 PR，无法核对打分机器人的判定")
    key = f"{path}:{blob}:{pr}"
    if cache_in["verdicts"].get(key) is not True:
        why = source.verdict(pr, path, blob)
        if why is not None:
            raise ShotSkip(why)
    cache_out["verdicts"][key] = True


# ---------- 纪录链与进度（判定规则即 fslib.judge） ----------

@dataclass(frozen=True)
class Merged:
    login: str
    shot_id: str
    score: Fraction
    models: tuple[str, ...]
    at: str
    pr: int | None
    agent: str = ""
    verification: dict | None = None      # 验证证书：{at, hash, sha}
    tool: str = ""                        # 工具本身的名字（agent 去掉「with 子 agent……」之类的说明）
    model: str = ""                       # 这次突破主要出力的那一个模型
    model_incomplete: bool = False        # model 只写了系列名，等作者补完整型号


TOOL_CUT_RE = re.compile(r"\s+(?:with|using|via|plus|\+|and)\s+|\s*[(（,，;；/]", re.I)


def tool_name(agent: str) -> str:
    """只取工具本身的名字：「Codex with GPT-6-Astra research subagents」→「Codex」。"""
    return TOOL_CUT_RE.split(agent, maxsplit=1)[0].strip()[:40]


def tool_label(agent: str) -> str:
    """纪录和排行用的工具：工具列表里的显示名（如 "Claude Code"）、"other: 名字" 或 "none"；没写是空字符串。
    早先自由填写的 agent 先去掉「with 子 agent」之类的说明，再到列表里找。"""
    if not agent:
        return ""
    c = F.agent_choice(agent) or F.agent_choice(tool_name(agent))
    if c is None:
        return f"other: {tool_name(agent) or agent[:40]}"
    return f"other: {c[1]}" if c[0] == "other" else c[1]


DECLARED = "declared.toml"
DECLARED_KEY_RE = re.compile(r"^(p[0-9]{3,6}-[a-z0-9-]+)/([A-Za-z0-9-]{1,39})/([a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?)$")
DECLARED_SOURCE_RE = re.compile(r"^https://github\.com/few-shots/few-shots/pull/[0-9]{1,7}#issuecomment-[0-9]{1,20}$")


def load_declared(root: Path, warn: Warn) -> dict[str, dict]:
    """裁判组维护的补充声明：已合并的 shot 当初没写的工具（agent）和主力模型（model）。SHOT.md 合并后不能再改，
    所以作者在原 PR 里回复，裁判照录到 declared.toml，每条带回复的链接。只补 SHOT.md 里没写的，不覆盖。"""
    try:
        data = tomllib.loads((root / DECLARED).read_text("utf-8"))
    except FileNotFoundError:
        return {}
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        warn(f"{DECLARED} 无法解析，本次不用")
        return {}
    out: dict[str, dict] = {}
    for key, v in data.items():
        if not (DECLARED_KEY_RE.fullmatch(key) and isinstance(v, dict) and DECLARED_SOURCE_RE.fullmatch(str(v.get("source", "")))):
            warn(f"{DECLARED} 里有一条格式不对，跳过")
            continue
        agent = v.get("agent", "")
        model = v.get("model", "")
        if not isinstance(agent, str) or (agent and F.agent_choice(agent) is None) \
                or not isinstance(model, str) or len(model) > MAX_MODEL_LEN or F.model_incomplete(model):
            warn(f"{DECLARED} 里 {key} 的 agent / model 不合规，跳过")
            continue
        out[key] = {"agent": agent.strip(), "model": clean_text(model, MAX_MODEL_LEN)}
    return out


def record_chain(problem: F.Problem, shots: list[Merged]) -> tuple[list[Merged], list[tuple[Merged, str]]]:
    """从预设基线出发，按合并顺序逐个判定；有效突破才进纪录链。"""
    records: list[Merged] = []
    skipped: list[tuple[Merged, str]] = []
    record = problem.baseline
    for s in shots:
        verdict = F.judge(problem, record, s.score)
        if verdict == "breakthrough":
            records.append(s)
            record = s.score
        else:
            skipped.append((s, f"{F.fmt(s.score)} {F.VERDICT_TEXT[verdict]}（当时纪录 {F.fmt(record)}）"))
    return records, skipped


def progress(problem: F.Problem, current: Fraction | None) -> tuple[str | None, float | None]:
    """返回 (相对预设基线的进度, 进度百分比)。进度与打分机器人回帖一致（fslib.progress）；
    百分比 = 进度 / |T(基线)| × 100（T 为 linear 的恒等或 log2）；T(基线) 为 0 时为 null；没有纪录时为 0。"""
    if current is None:
        return None, 0.0
    text = F.progress(problem, current)
    if text is None:
        return None, None
    b = problem.baseline
    if problem.progress_transform == "linear":
        gain = F.parse_rational(text)
        return text, (None if b == 0 else float(round(gain / abs(b) * 100, 2)))
    denom = abs(F._log2(b))
    return text, (None if denom == 0 else round(float(text) / denom * 100, 2))


# ---------- 组装 ----------

Warn = Callable[[str], None]


def shot_json(s: Merged, with_shot: bool) -> dict[str, Any]:
    out: dict[str, Any] = {"score": F.fmt(s.score), "login": s.login, "agent": s.agent, "models": list(s.models),
                           "at": s.at,
                           "verified_at": s.verification["at"] if s.verification else None, "pr": s.pr,
                           "tool": s.tool, "model": s.model, "model_incomplete": s.model_incomplete}
    if with_shot:
        out["shot"] = s.shot_id
    return out


def problem_entry(root: Path, pdir: str, source: Source, cache_in: dict, cache_out: dict, warn: Warn,
                  ref: str = "HEAD") -> dict[str, Any] | None:
    try:
        problem = F.load_problem_dir(root, pdir)
    except (F.ConfigError, OSError, ValueError) as e:
        raise BoardError(f"problem.toml 无法读取：{e}") from None
    if problem.sandbox:
        warn(f"{F.PROBLEMS_DIR}/{pdir} 是沙盒题，不上记分牌")
        return None

    merged, kind = F.list_merged_shots(root, pdir, ref=ref)
    if merged and kind != "git":
        raise BoardError("无法读取 git 历史")
    if len(merged) > MAX_SHOTS_PER_PROBLEM:
        raise BoardError(f"shot 数超过上限 {MAX_SHOTS_PER_PROBLEM}")
    blobs = tree_entries(root, ref, f"{F.PROBLEMS_DIR}/{pdir}/shots") if merged else {}
    declared = load_declared(root, warn) if merged else {}
    shots: list[Merged] = []
    for m in merged:
        path = f"{F.PROBLEMS_DIR}/{pdir}/shots/{m.login}/{m.shot_id}/SHOT.md"
        try:
            if m.shot is None:
                raise ShotSkip(m.error or "SHOT.md 无法解析")
            if m.commit is None:
                raise ShotSkip("git 历史里找不到引入它的提交")
            mode, blob = blobs.get(path, ("", ""))
            if mode not in REGULAR_MODES:
                raise ShotSkip("SHOT.md 必须是 main 上的普通文件")
            at, pr, verified = shot_merge(source, root, m.commit, path, cache_in, cache_out)
            shot_verdict(source, pr, path, blob, cache_in, cache_out)
            models = tuple(c for c in (clean_text(x, MAX_MODEL_LEN) for x in m.shot.models[:MAX_MODELS]) if c)
            agent = clean_text(m.shot.agent or "", MAX_MODEL_LEN)
            # 主力模型：写了 model 就用它；早先只写了 models 的，只有一个时就是它，多个时不替参与者猜
            main = clean_text(m.shot.model or "", MAX_MODEL_LEN) or (models[0] if len(models) == 1 else "")
            # 只写了系列名（如 gpt-6）的算没写全：补充声明里有完整型号就用它，没有先照原样显示并标出来
            vague = main if F.model_incomplete(main) else ""
            main = "" if vague else main
            # SHOT.md 没写（或没写全）的，用补充声明里作者后来在 PR 里回复的
            extra = declared.get(f"{pdir}/{m.login}/{m.shot_id}", {})
            agent = agent or extra.get("agent", "")
            main = main or extra.get("model", "")
            incomplete = not main and bool(vague)
            main = main or vague
            shots.append(Merged(m.login, m.shot_id, m.shot.claimed_score, models, at, pr, agent, verified,
                                tool_label(agent), main, incomplete))
        except ShotSkip as err:
            warn(f"跳过 {path}：{err}")

    records, skipped = record_chain(problem, shots)
    for s, why in skipped:
        warn(f"不计入纪录链 {F.PROBLEMS_DIR}/{pdir}/shots/{s.login}/{s.shot_id}：{why}")
    prog, pct = progress(problem, records[-1].score if records else None)
    return {
        "id": problem.id,
        "slug": problem.slug,
        "dir": f"{F.PROBLEMS_DIR}/{pdir}",
        "title_zh": problem.title_zh,
        "title_en": problem.title_en,
        "repo": F.REPO,
        "url": f"https://github.com/{F.REPO}/tree/main/{F.PROBLEMS_DIR}/{pdir}",
        "metric_zh": problem.metric_zh,
        "metric_en": problem.metric_en,
        "direction": problem.direction,
        "unit": problem.progress_unit,
        "baseline": F.fmt(problem.baseline),
        "epsilon": F.fmt(problem.epsilon),
        "epsilon_mode": problem.epsilon_mode,
        "progress_transform": problem.progress_transform,
        "current": shot_json(records[-1], with_shot=False) if records else None,
        "records": [shot_json(r, with_shot=True) for r in records],
        "progress": prog,
        "progress_pct": pct,
    }


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def _carry_cache(cache_in: dict, cache_out: dict, pdir: str) -> None:
    """某道题这次读取失败时，保留它的缓存条目，下次不必重新请求。"""
    needle = f"{F.PROBLEMS_DIR}/{pdir}/"
    for key in ("merges", "verdicts"):
        for k, v in cache_in[key].items():
            if needle in k:
                cache_out[key].setdefault(k, v)


def build(root: Path, source: Source, now: dt.datetime, warn: Warn, ref: str = "HEAD") -> tuple[dict, str, dict]:
    """返回 (board.json 内容, 新 README 文本, 新缓存)。不写文件。

    某一道题读取或解析失败时只告警，沿用上次 board.json 里这道题的数据。
    """
    cache_in = load_cache(root / "data" / "cache.json")
    cache_out = empty_cache()
    old = load_json(root / "data" / "board.json")
    old_by_dir = {}
    if isinstance(old, dict) and isinstance(old.get("problems"), list):
        old_by_dir = {p.get("dir"): p for p in old["problems"] if isinstance(p, dict)}

    problems = []
    for pdir in F.list_problem_dirs(root):
        try:
            p = problem_entry(root, pdir, source, cache_in, cache_out, warn, ref)
        except Exception as err:  # 包括 BoardError、RecursionError 等：只影响这一道题
            detail = str(err) if isinstance(err, BoardError) else type(err).__name__
            kept = old_by_dir.get(f"{F.PROBLEMS_DIR}/{pdir}")
            warn(f"{F.PROBLEMS_DIR}/{pdir} 本次读取失败（{detail}），" + ("沿用上次的数据" if kept else "暂不上榜"))
            _carry_cache(cache_in, cache_out, pdir)
            if kept:
                problems.append(kept)
            continue
        if p is not None:
            problems.append(p)

    # 内容没变就沿用旧的 updated_at，这样文件不变、工作流不提交
    updated_at = now.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(old, dict) and isinstance(old.get("updated_at"), str) and old.get("problems") == problems:
        updated_at = old["updated_at"]

    board = {"updated_at": updated_at, "problems": problems}
    try:
        readme = (root / "README.md").read_text("utf-8")
    except FileNotFoundError:
        raise BoardError("找不到 README.md") from None
    return board, replace_region(readme, render_board(board)), cache_out


# ---------- 验证证书（每个已合并 shot 目录里的 VERIFIED.md） ----------

VERIFIED_NAME = "VERIFIED.md"            # 保留文件名：只由记分牌工作流生成，参与者提交里不允许出现
LOGIN_RE = re.compile(r"^[A-Za-z0-9-]{1,39}$")
SHOT_ID_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$")
SCORE_RE = re.compile(r"^-?[0-9]{1,80}(/[0-9]{1,80}|\.[0-9]{1,80})?$")


def exact_decimal(score: str) -> str:
    """分母只含 2 和 5 的（有限小数）写成小数，和验证器输出一致（如 444368.3924061973）；否则保留分数。"""
    q = F.parse_rational(score)
    d, twos, fives = q.denominator, 0, 0
    while d % 2 == 0:
        d //= 2; twos += 1
    while d % 5 == 0:
        d //= 5; fives += 1
    if d != 1:
        return score
    places = max(twos, fives)
    n = abs(q.numerator) * 10 ** places // q.denominator
    whole, frac = divmod(n, 10 ** places)
    return ("-" if q < 0 else "") + str(whole) + (f".{frac:0{places}d}".rstrip("0").rstrip(".") if places else "")


def render_verifications(root: Path, board: dict, cache: dict) -> dict[str, str]:
    """按 templates/VERIFIED.md 给每条纪录填一份验证证书：{仓库内路径: 文本}。模板是固定的，验证器的这次运行
    只提供几个值（成绩、时间、哈希、提交、PR），都按格式校验过；没有验证记录的 shot 不生成。"""
    template = (root / "templates" / VERIFIED_NAME).read_text("utf-8")
    by_path = {k.split(":", 1)[1]: v for k, v in cache.get("merges", {}).items() if ":" in k and isinstance(v, dict)}
    out: dict[str, str] = {}
    for p in board.get("problems", []):
        pdir = str(p.get("dir", "")).removeprefix(f"{F.PROBLEMS_DIR}/")
        for r in p.get("records", []):
            login, shot = str(r.get("login", "")), str(r.get("shot", ""))
            if not (LOGIN_RE.fullmatch(login) and SHOT_ID_RE.fullmatch(shot) and type(r.get("pr")) is int
                    and SCORE_RE.fullmatch(str(r.get("score", "")))):
                continue
            entry = by_path.get(f"{F.PROBLEMS_DIR}/{pdir}/shots/{login}/{shot}/SHOT.md") or {}
            v = _verification(entry.get("verification"))
            if v is None:
                continue
            out[f"{F.PROBLEMS_DIR}/{pdir}/shots/{login}/{shot}/{VERIFIED_NAME}"] = template.format(
                problem=pdir, login=login, shot=shot, score=exact_decimal(r["score"]),
                verified_at=v["at"], hash=v["hash"], sha=v["sha"], pr=r["pr"])
    return out


# ---------- README 渲染 ----------

def md_escape(text: str, limit: int = 120) -> str:
    return MD_SPECIAL_RE.sub(r"\\\1", clean_text(text, limit))


def progress_cell(p: dict[str, Any]) -> str:
    if p["progress"] is None:
        return "—"
    # 带方向的符号：越小越好的题，进度写成减少了多少（−2 门），越大越好的写成增加了多少（+2 点）
    sign = "−" if p.get("direction") == "min" else "+"
    cell = f"{sign}{p['progress']} {md_escape(p['unit'])}"
    return cell if p["progress_pct"] is None else f"{cell}（{sign}{p['progress_pct']:.1f}%）"


def _en_line(text: Any, suffix: str = "") -> str:
    """表格单元格里的英文第二行（text 是第三方数据，转义；suffix 是固定文字）；没有英文时为空。"""
    return f"<br>{md_escape(text)}{suffix}" if isinstance(text, str) and text.strip() else ""


def render_board(board: dict[str, Any]) -> str:
    """记分牌区：表头总是显示；没有题目时只有表头。不写日期，也不写状态文字。
    题目与指标两格是中文一行、英文一行（<br> 换行）。"""
    lines: list[str] = list(TABLE_HEAD)
    for p in board["problems"]:
        title = f"[{md_escape(p['id'])} · {md_escape(p['title_zh'])}]({p['dir']})" + _en_line(p.get("title_en"))
        if p["direction"] == "max":
            arrow, arrow_en = "↑ 越大越好", "↑ higher is better"
        else:
            arrow, arrow_en = "↓ 越小越好", "↓ lower is better"
        metric = f"{md_escape(p['metric_zh'])}（{arrow}）" + _en_line(p.get("metric_en"), f", {arrow_en}")
        cur = p["current"]
        if cur:
            last = p["records"][-1]
            link = f"https://github.com/{F.REPO}/pull/{cur['pr']}" if cur["pr"] \
                else f"{p['dir']}/shots/{cur['login']}/{last['shot']}"
            score, holder = F.fmt_display(F.parse_rational(cur["score"])), f"[@{cur['login']}]({link})"
        else:
            score = holder = "—"
        lines.append(
            f"| {title} | {metric} | {F.fmt_display(F.parse_rational(p['baseline']))} | {score} | {holder} "
            f"| {len(p['records'])} | {progress_cell(p)} |"
        )
    lines += ["", "<sub>机器可读版本 · Machine-readable: [data/board.json](data/board.json)</sub>"]
    return "\n".join(lines)


def replace_region(readme: str, body: str) -> str:
    if readme.count(BOARD_START) != 1 or readme.count(BOARD_END) != 1:
        raise BoardError("README.md 必须各有一个 board:start 和 board:end 标记")
    s, e = readme.index(BOARD_START), readme.index(BOARD_END)
    if e < s:
        raise BoardError("README.md 的 board:end 在 board:start 之前")
    return readme[: s + len(BOARD_START)] + "\n" + body.rstrip("\n") + "\n" + readme[e:]


# ---------- 入口 ----------

def warn(msg: str) -> None:
    prefix = "::warning::" if os.environ.get("GITHUB_ACTIONS") == "true" else "警告："
    print(prefix + clean_text(msg, 500), file=sys.stderr)


def write_if_changed(path: Path, text: str) -> bool:
    try:
        if path.read_text("utf-8") == text:
            return False
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")
    return True


def dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成 Few Shots 记分牌")
    ap.add_argument("--root", type=Path, default=F.REPO_ROOT, help="本仓库 main 的 checkout（需要完整历史）")
    args = ap.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None
    try:
        board, readme, cache = build(args.root, GitHubSource(token), dt.datetime.now(dt.UTC), warn)
    except BoardError as e:
        prefix = "::error::" if os.environ.get("GITHUB_ACTIONS") == "true" else "错误："
        print(prefix + clean_text(str(e), 500), file=sys.stderr)
        return 1
    outputs = [
        ("data/board.json", args.root / "data" / "board.json", dump_json(board)),
        ("data/cache.json", args.root / "data" / "cache.json", dump_json(cache)),
        ("README.md", args.root / "README.md", readme),
    ] + [(rel, args.root / rel, text) for rel, text in sorted(render_verifications(args.root, board, cache).items())]
    changed = [name for name, path, text in outputs if write_if_changed(path, text)]
    print(f"{len(board['problems'])} 道题；改动：{', '.join(changed) or '无'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
