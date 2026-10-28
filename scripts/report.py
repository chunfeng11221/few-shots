#!/usr/bin/env python3
"""生成并发布（或原地更新）PR 上唯一的一条评分评论，并给 PR head 提交写 few-shots/verdict 状态。

- 只读取打分 job 上传的结果文件（in/result.json）和 GitHub 事件文件；结果文件来自处理过不可信输入的
  job，这里把其中每个字段都当作不可信数据：重新校验、清洗后再渲染。
- 发帖前再读一次暂停开关，并用 GitHub API 查 PR 当前的 head 与 main 的当前提交：任一与打分时不同，
  结论标为"结果已过期，请勿合并"。
- 题目信息取自 main 上的 problems/<题目>/problem.toml；结果文件里的题目目录名要重新校验。
- few-shots/verdict 是 main 规则集里的必需检查：只有"有效突破且未过期"（或不涉及任何题目 shots/ 的 PR）为 success，
  暂停时为 pending，其余为 failure / error。合并按钮由它约束，不靠人工比对 SHA。
- 参与者看到的文字都是中文一行、英文一行（短标签写成"中文 · English"）；few-shots/verdict 的描述在 140 字以内，
  有效突破的描述必须以"有效突破"开头（自动裁判与记分牌按这个前缀识别）。
- 评论第二行是机器人自己写的隐藏数据：本 PR 验证到的证书哈希（连同题目目录）及首次时间、每道题首次判为
  有效突破的提交与时间。后续推送只追加、不覆盖；去重时读取其他开放 PR 的同类数据（只认机器人账号写的评论），
  只和同一道题比。

用法：python3 -I scripts/report.py --result in/result.json --event "$GITHUB_EVENT_PATH" --post
      python3 scripts/report.py --result out/result.json --print   # 本地预览，不访问网络
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import control as C  # noqa: E402
import fslib as F  # noqa: E402

MARKER = "<!-- few-shots:score -->"
DATA_PREFIX = "<!-- few-shots:data "
DATA_SUFFIX = " -->"
BOT_LOGIN = "github-actions[bot]"
SCOREBOARD = "https://few-shots.ai"
STATUS_CONTEXT = "few-shots/verdict"
BASE_BRANCH = "main"
MAX_VALID = 20          # 每个 PR 最多记住多少个不同的证书哈希
DATA_VERSION = 2
MAX_OPEN_PRS = 100      # 去重时最多检查多少个开放 PR
TIME_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
RUN_URL_RE = re.compile(r"https://github\.com/[A-Za-z0-9._/-]{3,200}/actions/runs/[0-9]{1,20}")

VERDICT_PREFIX = "有效突破"  # 有效突破的 few-shots/verdict 描述必须以它开头
TITLES = {
    "paused": "打分已暂停 · Scoring paused",
    "not_shot": "不是 shot 提交 · Not a shot submission",
    "rejected": "未通过提交检查 · Failed submission checks",
    "error": "评分流程出错 · Scoring pipeline error",
    "invalid": "验证器判定证书无效 · Certificate rejected by the verifier",
    "claim_mismatch": "claimed_score 与验证结果不一致 · claimed_score does not match the verifier",
    "duplicate": "证书与已有提交本质相同 · Same certificate as an earlier submission",
    **F.VERDICT_TEXT,   # below_baseline / not_record / below_epsilon / breakthrough
}
STALE_TITLE = "结果已过期，请勿合并 · Result is stale; do not merge"
bi = F.bi


@dataclass
class Context:
    """发帖时从 GitHub 读到的状态。--print 预览时 checked 为 False，结论不会被标成可合并。"""
    checked: bool = False
    fresh: bool = False
    stale_reason: str | None = None
    paused: dict | None = None
    prev: dict = field(default_factory=lambda: empty_data())
    others: list = field(default_factory=list)   # [(PR 号, data)]
    others_complete: bool = True
    now: str | None = None


@dataclass(frozen=True)
class Report:
    body: str
    create: bool        # 没有旧评论时是否新建
    status: str         # 本次评论采用的结论
    state: str          # few-shots/verdict 的状态：success | failure | pending | error
    description: str
    data: dict


# ---------------------------------------------------------------- 隐藏数据


def empty_data() -> dict:
    return {"v": DATA_VERSION, "valid": [], "first": []}


def _entry(obj: object) -> dict | None:
    if not isinstance(obj, dict) or set(obj) != {"p", "hash", "sha", "at"}:
        return None
    p, h, sha, at = obj["p"], obj["hash"], obj["sha"], obj["at"]
    if not (isinstance(p, str) and F.PROBLEM_DIR_RE.fullmatch(p)
            and isinstance(h, str) and F.HASH_RE.fullmatch(h) and isinstance(sha, str)
            and F.COMMIT_SHA_RE.fullmatch(sha) and isinstance(at, str) and TIME_RE.fullmatch(at)):
        return None
    return {"p": p, "hash": h, "sha": sha, "at": at}


def _entries(items: object, key) -> list:
    out, seen = [], set()
    for e in items[:MAX_VALID] if isinstance(items, list) else []:
        e = _entry(e)
        if e and key(e) not in seen:
            seen.add(key(e))
            out.append(e)
    return out


def parse_data(body: object) -> dict:
    """读机器人评论第二行的隐藏数据；格式不对一律当作没有。"""
    out = empty_data()
    if not isinstance(body, str) or not body.startswith(MARKER + "\n"):
        return out
    line = body.split("\n", 2)[1]
    if not (line.startswith(DATA_PREFIX) and line.endswith(DATA_SUFFIX)):
        return out
    try:
        obj = json.loads(line[len(DATA_PREFIX):-len(DATA_SUFFIX)])
    except ValueError:
        return out
    if not isinstance(obj, dict) or obj.get("v") != DATA_VERSION:
        return out
    out["valid"] = _entries(obj.get("valid"), lambda e: (e["p"], e["hash"]))
    out["first"] = _entries(obj.get("first"), lambda e: e["p"])
    return out


def data_line(data: dict) -> str:
    return DATA_PREFIX + json.dumps(data, separators=(",", ":"), sort_keys=True) + DATA_SUFFIX


# ---------------------------------------------------------------- 渲染


def _num(value: object) -> str | None:
    """只渲染能重新解析为精确有理数的值。"""
    try:
        return F.fmt(F.parse_rational(value))
    except ValueError:
        return None


def _code(value: object, limit: int = 80) -> str:
    s = F.sanitize_inline(value, limit) if value is not None else ""
    return f"`{s}`" if s else "—"


def _ref(value: object) -> str | None:
    return value if isinstance(value, str) and F.SHOT_REF_RE.fullmatch(value) else None


def _sha(value: object) -> str | None:
    return value if isinstance(value, str) and F.COMMIT_SHA_RE.fullmatch(value) else None


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _breakthrough_description(score: str | None) -> str:
    """有效突破的状态描述：以"有效突破"开头；中英文放不进 140 字时只留中文。"""
    desc = f"{VERDICT_PREFIX}：{score} · Valid breakthrough: {score}"
    return desc if len(desc) <= 140 else _short(f"{VERDICT_PREFIX}：{score}", 140)


def _state(status: str, stale: bool, ctx: Context, score: str | None) -> tuple[str, str]:
    if status == "paused":
        return "pending", "打分已暂停，恢复后重新打分 · Scoring paused; rescored after it resumes"
    if status == "not_shot":
        return "success", "不涉及任何题目的 shots/，不打分 · Touches no problem's shots/; not scored"
    if status == "error":
        return "error", TITLES["error"]
    if status == "breakthrough":
        if ctx.checked and ctx.fresh and not stale:
            return "success", _breakthrough_description(score)
        return "failure", STALE_TITLE
    return "failure", TITLES[status]


def _bilingual(msg: object, limit: int) -> tuple[str, str]:
    """清洗一条说明的中文与英文两行；单语说明两行相同。"""
    zh, en = F.bi_parts(msg)
    zh = F.sanitize_message(zh, limit)
    return zh, (zh if en is None else F.sanitize_message(en, limit))


def _bullets(items: list, limit: int) -> list:
    """每条说明一个列表项：中文一行，英文缩进在下一行（GitHub 评论把换行渲染成换行）。"""
    out = []
    for m in items:
        zh, en = F.bi_parts(m)
        out.append(f"- {F.sanitize_message(zh, limit)}")
        en = F.sanitize_message(en, limit) if en is not None else ""
        if en:
            out.append(f"  {en}")
    return out


def render(result: dict | None, event: dict, problem: F.Problem | None, ctx: Context | None = None) -> Report:
    """生成评论正文与 few-shots/verdict 状态。题目信息取自 main 上的 problem.toml；没有题目时（门禁未通过等）
    只渲染结论与说明。"""
    ctx = ctx or Context()
    pr = event.get("pull_request") if isinstance(event.get("pull_request"), dict) else {}
    number = pr.get("number") if type(pr.get("number")) is int else 0
    head_sha = _sha((pr.get("head") or {}).get("sha"))
    if not isinstance(result, dict):
        result = None

    status = result.get("status") if result and result.get("status") in F.STATUSES else "error"
    messages = [m for m in (result or {}).get("messages", []) if isinstance(m, str)][:20]
    if result is None:
        messages = [bi("没有拿到打分结果文件。请联系裁判组，或在 Actions 日志里查看本次运行。",
                       "The scoring result file is missing. Please contact the referees, or look up this run "
                       "in the Actions log.")]
    if problem is None and status not in ("paused", "not_shot", "rejected", "error"):
        status = "error"
        messages = [bi("结果里的题目目录在 main 上无法读取", "The problem directory in the result cannot be read on main")]
    if ctx.paused:
        status = "paused"
        reason = ctx.paused.get("reason")
        messages = [reason] if isinstance(reason, str) and reason else []

    r = result or {}
    h = r.get("canonical_hash") if isinstance(r.get("canonical_hash"), str) \
        and F.HASH_RE.fullmatch(r["canonical_hash"]) else None
    base_sha = _sha(r.get("base_sha"))

    pdir = problem.dir if problem else None

    # 历史只追加：本 PR 验证到的每个证书哈希（按题目）记首次时间
    data = {"v": DATA_VERSION, "valid": [dict(e) for e in ctx.prev.get("valid", [])][:MAX_VALID],
            "first": [dict(e) for e in ctx.prev.get("first", [])][:MAX_VALID]}
    if pdir and h and head_sha and ctx.now and (pdir, h) not in {(e["p"], e["hash"]) for e in data["valid"]} \
            and len(data["valid"]) < MAX_VALID:
        data["valid"].append({"p": pdir, "hash": h, "sha": head_sha, "at": ctx.now})
    mine = next((e for e in data["valid"] if (e["p"], e["hash"]) == (pdir, h)), None) if h else None

    duplicate_of = None
    if status == "breakthrough" and h and mine:
        for n, other in sorted(ctx.others, key=lambda x: x[0]):
            e = next((x for x in other.get("valid", []) if (x["p"], x["hash"]) == (pdir, h)), None)
            if n != number and e and (e["at"], n) < (mine["at"], number):
                duplicate_of = n
                status = "duplicate"
                messages = []
                break

    stale = status == "breakthrough" and ctx.checked and not ctx.fresh
    first = next((e for e in data["first"] if e["p"] == pdir), None) if pdir else None
    if status == "breakthrough" and ctx.checked and ctx.fresh and first is None \
            and h and head_sha and ctx.now and len(data["first"]) < MAX_VALID:
        first = {"p": pdir, "hash": h, "sha": head_sha, "at": ctx.now}
        data["first"].append(first)

    score, claimed = _num(r.get("score")), _num(r.get("claimed_score"))
    state, description = _state(status, stale, ctx, score)

    lines = [MARKER, data_line(data)]
    where = f" · {problem.id}" if problem else ""
    lines += [f"### Few Shots{where}：{STALE_TITLE if stale else TITLES[status]}", ""]
    why_zh, why_en = _bilingual(ctx.stale_reason or bi("无法确认结果对应当前提交与 main",
                                                      "it cannot be confirmed that the result matches the current "
                                                      "commit and main"), 200)
    if stale:
        lines += [f"本次运行的结论是「有效突破」，但{why_zh}。这个结论不能作为合并依据。",
                  f"This run found a valid breakthrough, but {why_en}. It cannot be used as the basis for merging.",
                  ""]
    elif ctx.checked and not ctx.fresh and status not in ("paused", "not_shot", "error"):
        lines += [f"注意：{why_zh}，以最新一次运行为准。", f"Note: {why_en}; the latest run is what counts.", ""]

    record, required = _num(r.get("record")), _num(r.get("required"))
    record_ref = _ref(r.get("record_ref"))

    if problem and status == "breakthrough" and not stale and score and record:
        verb, verb_en = ("提升到", "increased") if problem.direction == "max" else ("降低到", "decreased")
        lines += [f"验证器确认「{problem.metric_zh}」从 {record} {verb} {score}。",
                  f"The verifier confirms that {problem.metric_en} {verb_en} from {record} to {score}.", ""]
    if duplicate_of is not None:
        lines += [f"证书与 #{duplicate_of} 中更早由机器人验证到的证书本质相同（canonical_hash 相同），"
                  "以先验证到的 PR 为准。",
                  f"The certificate is essentially the same as one the bot verified earlier in #{duplicate_of} "
                  "(same canonical_hash); the PR verified first takes precedence.", ""]

    shot = r.get("shot") if isinstance(r.get("shot"), dict) else None
    rows = []
    if problem:
        rows.append(("题目 · Problem", f"`{F.PROBLEMS_DIR}/{problem.dir}`"))
    if shot:
        rows.append(("shot", _code(f"{shot.get('login')}/{shot.get('shot_id')}")))
    if head_sha:
        rows.append(("评分提交 · Scored commit", f"`{head_sha[:12]}`"))
    if base_sha:
        rows.append(("打分时的 main · main when scored", f"`{base_sha[:12]}`"))
    if score is not None:
        rows.append(("验证器得分 · Verifier score", f"`{score}`"))
    if claimed is not None:
        rows.append(("声明得分 · claimed_score", f"`{claimed}`"))
    if problem and status not in ("paused", "not_shot"):
        rows.append(("预设基线 · Baseline", f"`{F.fmt(problem.baseline)}`"))
    if problem and record is not None:
        holder = f"（{F.sanitize_inline(record_ref, 80)}）" if record_ref else "（即预设基线 · the baseline）"
        rows.append(("当前纪录 · Current record", f"`{record}`{holder}"))
    if problem and required is not None:
        op = "≥" if problem.direction == "max" else "≤"
        mode = "相对 · relative" if problem.epsilon_mode == "relative" else "绝对 · absolute"
        rows.append(("有效突破需要 · Needed for a breakthrough",
                     f"{op} `{required}`（ε = {F.fmt(problem.epsilon)}，{mode}）"))
    progress = r.get("progress")
    if problem and status == "breakthrough" and not stale and isinstance(progress, str):
        rows.append(("相对基线的进度 · Progress over baseline", f"{_code(progress, 40)} {problem.progress_unit}"))
    if h:
        rows.append(("证书规范化哈希 · Canonical hash", f"`{h[:16]}…`"))
    if first:
        rows.append(("首次判为有效突破 · First valid breakthrough", f"`{first['sha'][:12]}`（{first['at']}）"))
    if rows:
        if first and status == "breakthrough" and not stale:
            lines += ["**验证证书 · Verification certificate**：由 Few Shots 验证器生成。纪录的先后以「首次判为有效突破」的"
                      "时间为准（服务器时间，UTC），不看 Git 提交时间。",
                      "Generated by the Few Shots verifier. Records are ordered by the first-valid-breakthrough time below "
                      "(server time, UTC), not by git commit dates.", ""]
        lines += ["| 项目 · Item | 值 · Value |", "| --- | --- |"] + [f"| {k} | {v} |" for k, v in rows] + [""]

    if messages and status != "breakthrough":
        lines += ["说明 · Details："] + _bullets(messages, 300) + [""]

    warnings = [w for w in r.get("chain_warnings", []) if isinstance(w, str)][:20] \
        if isinstance(r.get("chain_warnings"), list) else []
    if warnings and status != "paused":
        lines += ["复算 main 上的纪录链时发现（请裁判核对）：",
                  "Found while re-verifying the record chain on main (for the referees to check):"] + \
                 _bullets(warnings, 200) + [""]
    if status == "breakthrough" and not ctx.others_complete:
        lines += ["注意：开放的 PR 过多或读取失败，没有完成与其他开放 PR 的重复检查，请裁判核对。",
                  "Note: there are too many open PRs or a read failed, so the duplicate check against other open "
                  "PRs is incomplete. Referees, please check.", ""]

    lines += [_next_step(status, bool(problem and problem.sandbox), stale), ""]
    lines += [f"<sub>本评论由评分流水线自动生成，每次推送后原地更新；打分只使用 main 上的验证器与规则；"
              f"能否合并以提交状态 {STATUS_CONTEXT} 为准。</sub>",
              f"<sub>Generated by the scoring pipeline and updated in place after each push. Scoring uses only "
              f"the verifier and rules on main; the commit status {STATUS_CONTEXT} decides whether the PR can "
              f"be merged.</sub>",
              f"<sub>记分牌 · Board：{SCOREBOARD}</sub>"]
    return Report("\n".join(lines) + "\n", status != "not_shot", status, state, description, data)


def _next_step(status: str, sandbox: bool, stale: bool) -> str:
    """下一步：中文一行，英文一行。"""
    if stale:
        return bi("请勿合并。PR 有新推送时机器人会自动重新打分；main 前进时，请先合并最新的 main"
                  "（或由裁判点 Update branch），推送后会重新打分。",
                  "Do not merge. A new push to the PR is rescored automatically; if main has moved on, merge the "
                  "latest main first (or a referee clicks Update branch), and the push is rescored.")
    if status == "breakthrough" and sandbox:
        return bi("下一步：这是沙盒题，用来练习整个流程，验证器的结果就是上面这条。沙盒题不合并、不上记分牌、不发奖励，"
                  "自动裁判会回一条说明后关闭这个 PR。",
                  "Next: this is the sandbox, for practising the whole flow; the verifier's result above is the outcome. "
                  "Sandbox shots are not merged, not on the board and earn no reward; the automatic referee replies and "
                  "closes this PR.")
    if status == "breakthrough":
        zh = ("下一步：自动裁判复核 SHOT.md 和 PR 说明后合并（条件见 RULES.md「合并」，不满足的转人工复核）；"
              "合并后计入纪录链，并汇编进这道题的 FEWSHOT.md。")
        en = ("Next: the automatic referee reviews SHOT.md and the PR text and merges it (conditions in RULES.md, "
              "Merging; otherwise a referee reviews it by hand); once merged it joins the record chain and is "
              "compiled into this problem's FEWSHOT.md.")
        return bi(zh + "每一次有效突破，突破者得到一张 AGI Bar 的酒水兑换券，合并后自动发到 PR 作者的 GitHub 账号。",
                  en + " Each valid breakthrough earns its author an AGI Bar drink voucher, sent automatically "
                  "to the PR author's GitHub account after the merge.")
    if status == "paused":
        return bi("裁判组暂时关闭了打分。恢复后推送一个新提交（或关闭再重新打开 PR）即可重新打分。",
                  "The referees have paused scoring. Once it resumes, push a new commit (or close and reopen "
                  "the PR) to be rescored.")
    if status == "not_shot":
        return bi("这个 PR 不涉及任何题目的 shots/，不打分。",
                  "This PR does not touch any problem's shots/, so it is not scored.")
    if status == "error":
        return bi("这不是你的问题。请联系裁判组；修复后推送一个新提交即可重新打分。",
                  "This is not a problem with your submission. Please contact the referees; after the fix, "
                  "push a new commit to be rescored.")
    if status in ("rejected", "invalid", "claim_mismatch"):
        return bi("请按上面的说明修改后推送新提交，机器人会重新打分。"
                  "本地可先运行 `python3 tools/check.py problems/<题目目录>/shots/<login>/<shot-id>` 自检。",
                  "Fix the issues above and push a new commit; the bot will rescore it. To check locally "
                  "first, run `python3 tools/check.py problems/<problem-dir>/shots/<login>/<shot-id>`.")
    return bi("只有有效突破会被合并，这个 PR 不会被合并。继续改进后推送新提交，机器人会重新打分。",
              "Only valid breakthroughs are merged, so this PR will not be merged. Keep improving and push "
              "a new commit; the bot will rescore it.")


# ---------------------------------------------------------------- 读 GitHub 状态


def find_sticky(api: F.GitHubAPI, repo: str, number: int, max_pages: int = 20) -> tuple[int, str] | None:
    """找机器人账号发的评分评论；返回 (评论 id, 正文)。"""
    for page in range(1, max_pages + 1):
        items = api.get(f"/repos/{repo}/issues/{number}/comments", {"per_page": 100, "page": page})
        if not isinstance(items, list):
            return None
        for c in items:
            if not isinstance(c, dict):
                continue
            user = (c.get("user") or {}).get("login")
            body = c.get("body") or ""
            if user == BOT_LOGIN and isinstance(body, str) and body.startswith(MARKER) \
                    and type(c.get("id")) is int:
                return c["id"], body
        if len(items) < 100:
            return None
    return None


def check_fresh(api: F.GitHubAPI, repo: str, number: int, head_sha: str | None,
                base_sha: str | None) -> tuple[bool, str | None]:
    """结果对应的 head 与打分时的 main 是否仍是当前的 head 与 main。读不到按过期处理。"""
    try:
        pr = api.get(f"/repos/{repo}/pulls/{number}")
        ref = api.get(f"/repos/{repo}/git/ref/heads/{BASE_BRANCH}")
    except F.GitHubAPIError:
        return False, bi("无法读取 PR 或 main 的当前状态", "the current state of the PR or main cannot be read")
    cur_head = _sha(((pr or {}).get("head") or {}).get("sha")) if isinstance(pr, dict) else None
    cur_main = _sha(((ref or {}).get("object") or {}).get("sha")) if isinstance(ref, dict) else None
    if not cur_head or not cur_main:
        return False, bi("无法读取 PR 或 main 的当前状态", "the current state of the PR or main cannot be read")
    if head_sha != cur_head:
        return False, bi("打分后 PR 又有新的推送，以最新一次运行为准",
                         "the PR received a new push after scoring")
    if not base_sha:
        return False, bi("结果里没有打分时 main 的提交", "the result does not record which main commit was scored")
    if base_sha != cur_main:
        return False, bi("打分后 main 已经前进（例如别的 shot 被合并）",
                         "main moved on after scoring (for example, another shot was merged)")
    return True, None


def collect_others(api: F.GitHubAPI, repo: str, number: int) -> tuple[list, bool]:
    """读取其他开放 PR 的机器人评论中的隐藏数据，用于证书去重。返回 ([(PR 号, data)], 是否完整)。"""
    pulls: list = []
    try:
        for page in (1, 2):
            items = api.get(f"/repos/{repo}/pulls", {"state": "open", "sort": "created", "direction": "asc",
                                                     "per_page": 100, "page": page})
            if not isinstance(items, list):
                return [], False
            pulls.extend(items)
            if len(items) < 100:
                break
    except F.GitHubAPIError:
        return [], False
    complete = len(pulls) <= MAX_OPEN_PRS
    others = []
    for p in pulls[:MAX_OPEN_PRS]:
        n = p.get("number") if isinstance(p, dict) else None
        if type(n) is not int or n == number:
            continue
        try:
            sticky = find_sticky(api, repo, n, max_pages=3)
        except F.GitHubAPIError:
            complete = False
            continue
        if sticky:
            d = parse_data(sticky[1])
            if d["valid"]:
                others.append((n, d))
    return others, complete


# ---------------------------------------------------------------- 写


def upsert(api: F.GitHubAPI, repo: str, number: int, body: str, create: bool,
           sticky: tuple[int, str] | None = None) -> str:
    if sticky is None:
        sticky = find_sticky(api, repo, number)
    if sticky is not None:
        api.request("PATCH", f"/repos/{repo}/issues/comments/{sticky[0]}", body={"body": body})
        return "updated"
    if create:
        api.request("POST", f"/repos/{repo}/issues/{number}/comments", body={"body": body})
        return "created"
    return "skipped"


def set_status(api: F.GitHubAPI, repo: str, sha: str, state: str, description: str,
               target_url: str | None = None) -> None:
    assert state in ("success", "failure", "pending", "error") and F.COMMIT_SHA_RE.fullmatch(sha)
    body = {"state": state, "context": STATUS_CONTEXT, "description": _short(description, 140)}
    if target_url:
        body["target_url"] = target_url
    api.request("POST", f"/repos/{repo}/statuses/{sha}", body=body)


def run_url() -> str | None:
    url = "{}/{}/actions/runs/{}".format(os.environ.get("GITHUB_SERVER_URL", ""),
                                         os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_RUN_ID", ""))
    return url if RUN_URL_RE.fullmatch(url) else None


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_result(path: str) -> dict | None:
    try:
        obj = F.read_json_file(path, 256 * 1024)
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def resolve_problem(root: Path, result: dict | None) -> F.Problem | None:
    """结果文件里的题目目录名是不可信数据：必须是 main 上合规的题目目录，才读它的 problem.toml。"""
    info = result.get("problem") if isinstance(result, dict) else None
    name = info.get("dir") if isinstance(info, dict) else None
    try:
        return F.load_problem_dir(root, name)
    except (F.ConfigError, OSError, ValueError):
        return None


def post(api: F.GitHubAPI, result: dict | None, event: dict, problem: F.Problem | None, repo: str, number: int,
         head_sha: str, now: str | None = None, target_url: str | None = None) -> Report:
    """发帖前重新读暂停开关、PR 与 main 的当前状态和其他开放 PR 的数据，然后更新评论和提交状态。"""
    sticky = find_sticky(api, repo, number)
    ctx = Context(checked=True, now=now or utc_now())
    ctx.prev = parse_data(sticky[1]) if sticky else empty_data()
    control = C.fetch_control(api)
    if control.get("paused") is not False:
        ctx.paused = control
    base_sha = result.get("base_sha") if isinstance(result, dict) else None
    ctx.fresh, ctx.stale_reason = check_fresh(api, repo, number, head_sha, _sha(base_sha))
    h = result.get("canonical_hash") if isinstance(result, dict) else None
    if not ctx.paused and isinstance(result, dict) and result.get("status") == "breakthrough" \
            and isinstance(h, str) and F.HASH_RE.fullmatch(h):
        ctx.others, ctx.others_complete = collect_others(api, repo, number)
    rep = render(result, event, problem, ctx)
    upsert(api, repo, number, rep.body, rep.create, sticky)
    set_status(api, repo, head_sha, rep.state, rep.description, target_url)
    return rep


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成并发布评分评论")
    ap.add_argument("--result", required=True)
    ap.add_argument("--event", default=os.environ.get("GITHUB_EVENT_PATH"))
    ap.add_argument("--root", default=str(F.REPO_ROOT), help="main 的 checkout 目录")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--post", action="store_true", help="发布或更新评论，并写提交状态")
    mode.add_argument("--print", action="store_true", help="只打印评论正文")
    args = ap.parse_args(argv)
    event = F.read_json_file(args.event, 25 * 1024 * 1024) if args.event else {}
    result = load_result(args.result)
    problem = resolve_problem(Path(args.root), result)
    if args.print:
        sys.stdout.write(render(result, event, problem).body)
        return 0
    pr = event.get("pull_request") or {}
    number = pr.get("number")
    repo = (event.get("repository") or {}).get("full_name", "")
    head_sha = _sha((pr.get("head") or {}).get("sha"))
    if type(number) is not int or not isinstance(repo, str) or not F.REPO_RE.fullmatch(repo) or not head_sha:
        print("事件数据缺少 PR 编号、仓库名或 head SHA", file=sys.stderr)
        return 1
    api = F.GitHubAPI(os.environ.get("GITHUB_TOKEN"), allow_write=True)
    try:
        rep = post(api, result, event, problem, repo, number, head_sha, target_url=run_url())
    except F.GitHubAPIError as e:
        # 没写成状态，few-shots/verdict 缺失，PR 无法合并（失败即关闭）
        print(f"回帖或写状态失败：HTTP {e.status}", file=sys.stderr)
        return 1
    print(f"comment: {rep.status}; {STATUS_CONTEXT}: {rep.state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
