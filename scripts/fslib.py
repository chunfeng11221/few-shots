"""Few Shots 共享库（只用 Python 3.12 标准库）。

scripts/ 下的流水线脚本与 tools/ 下的本地工具共用这里的规则，保证本地自检与线上打分一致。
这里的代码只解析 TOML / JSON / 整数 / 有理数文本，不执行任何参与者提交的内容。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import tomllib
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable

if sys.version_info < (3, 11):
    raise SystemExit("需要 Python 3.11 及以上（推荐 3.12）\nRequires Python 3.11 or later (3.12 recommended)")

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO = "few-shots/few-shots"        # 本仓库在 GitHub 上的全名
PROBLEMS_DIR = "problems"           # 每道题一个目录：problems/<id>-<slug>/

# ---------------------------------------------------------------- 命名规则

LOGIN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}")
SHOT_ID_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
FILE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.(?:md|json|txt|csv)")
SHOT_REF_RE = re.compile(rf"(?:{LOGIN_RE.pattern})/(?:{SHOT_ID_RE.pattern})")
PROBLEM_ID_RE = re.compile(r"p[0-9]{3,6}")
PROBLEM_DIR_RE = re.compile(rf"(?:{PROBLEM_ID_RE.pattern})-(?:{SHOT_ID_RE.pattern})")
REPO_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}")
COMMIT_SHA_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
HASH_RE = re.compile(r"[0-9a-f]{64}")

REQUIRED_FILES = ("SHOT.md", "certificate.json")
RESERVED_FILES = ("verified.md",)      # 验证证书：合并后由记分牌工作流按模板生成，参与者不能提交（不区分大小写）
RESERVED_TEXT = ("VERIFIED.md 是 Few Shots 验证器生成的验证证书，合并后自动放进 shot 目录，不能自己提交",
                 "VERIFIED.md is the verification certificate the Few Shots verifier issues after the merge; it cannot be submitted")
ALLOWED_EXTENSIONS = (".md", ".json", ".txt", ".csv")

STATUSES = (
    "paused",          # 暂停开关打开
    "not_shot",        # PR 不涉及任何题目的 shots/，不打分
    "rejected",        # 文件白名单或 SHOT.md 不合规
    "error",           # 流水线自身出错
    "invalid",         # 验证器判定证书无效
    "claim_mismatch",  # claimed_score 与验证结果不一致
    "duplicate",       # 证书与 main 上的 shot 或更早的开放 PR 本质相同（canonical_hash 相同）
    "below_baseline",  # 未达到预设基线
    "not_record",      # 达到基线但未刷新纪录
    "below_epsilon",   # 刷新纪录但改进不足 ε
    "breakthrough",    # 有效突破
)


class ShotError(ValueError):
    """shot 不合规；messages 是给参与者看的说明（中文一行、英文一行，见 bi）。"""

    def __init__(self, messages):
        self.messages = [str(m) for m in messages]
        super().__init__("；".join(self.messages))


class ConfigError(ValueError):
    pass


# ---------------------------------------------------------------- 双语说明
# 参与者看到的说明都是中文一行、英文一行（中间用换行分开）。回帖与本地自检按行拆开、各自清洗后再显示。


def bi(zh: str, en: str) -> str:
    return f"{zh}\n{en}"


def bi_parts(msg: object) -> tuple[str, str | None]:
    """拆成 (中文, 英文)；不是双语的说明返回 (原文, None)。"""
    zh, sep, en = str(msg).partition("\n")
    return zh, (en if sep else None)


def bi_prefix(zh_prefix: str, en_prefix: str, msg: object) -> str:
    """给一条说明的中英文两行分别加前缀；单语说明两行都用原文。"""
    zh, en = bi_parts(msg)
    return bi(zh_prefix + zh, en_prefix + (zh if en is None else en))


# ---------------------------------------------------------------- 精确有理数

RATIONAL_RE = re.compile(r"(-?)(0|[1-9][0-9]*)(?:/([1-9][0-9]*)|\.([0-9]+))?")
MAX_RATIONAL_CHARS = 400


def parse_rational(text: object) -> Fraction:
    """只接受 "85"、"-3"、"22/7"、"0.125" 这类写法；不接受指数、空格、下划线、inf/nan。"""
    if not isinstance(text, str):
        raise ValueError(bi('必须是字符串形式的精确有理数，例如 "85" 或 "22/7"',
                              'must be an exact rational number written as a string, e.g. "85" or "22/7"'))
    if len(text) > MAX_RATIONAL_CHARS:
        raise ValueError(bi(f"有理数字符串超过 {MAX_RATIONAL_CHARS} 个字符",
                              f"rational string is longer than {MAX_RATIONAL_CHARS} characters"))
    m = RATIONAL_RE.fullmatch(text)
    if not m:
        raise ValueError(bi('不是合法的精确有理数（允许 "85"、"-3"、"22/7"、"0.125"）',
                              'is not a valid exact rational number (allowed: "85", "-3", "22/7", "0.125")'))
    sign, whole, den, frac = m.groups()
    if den is not None:
        value = Fraction(int(whole), int(den))
    elif frac is not None:
        value = Fraction(int(whole + frac), 10 ** len(frac))
    else:
        value = Fraction(int(whole))
    return -value if sign else value


def fmt(x: Fraction | None) -> str | None:
    return None if x is None else str(x)


# ---------------------------------------------------------------- 题目配置


@dataclass(frozen=True)
class Problem:
    id: str
    slug: str
    title_zh: str
    title_en: str
    kind: str
    sandbox: bool
    metric_zh: str
    metric_en: str
    direction: str
    progress_unit: str
    baseline: Fraction
    epsilon: Fraction
    epsilon_mode: str
    progress_transform: str
    verifier_timeout_sec: int
    max_certificate_bytes: int
    max_files: int
    max_file_bytes: int
    max_shot_md_bytes: int
    max_total_bytes: int
    params: dict
    dir: str = ""       # 题目目录名 <id>-<slug>；由 load_problem_dir 填写

    def file_limit(self, name: str) -> int:
        if name == "certificate.json":
            return self.max_certificate_bytes
        if name == "SHOT.md":
            return self.max_shot_md_bytes
        return self.max_file_bytes

    @property
    def direction_zh(self) -> str:
        return "越大越好" if self.direction == "max" else "越小越好"

    @property
    def direction_en(self) -> str:
        return "higher is better" if self.direction == "max" else "lower is better"


def _cfg_str(d: dict, key: str, *, pattern=None, choices=None, max_len=200) -> str:
    v = d.get(key)
    if not isinstance(v, str) or not v.strip() or len(v) > max_len:
        raise ConfigError(bi(f"problem.toml: {key} 必须是非空字符串", f"problem.toml: {key} must be a non-empty string"))
    if pattern is not None and not pattern.fullmatch(v):
        raise ConfigError(bi(f"problem.toml: {key} 格式不对", f"problem.toml: {key} has the wrong format"))
    if choices is not None and v not in choices:
        raise ConfigError(bi(f"problem.toml: {key} 只能是 {'|'.join(choices)}",
                               f"problem.toml: {key} must be one of {'|'.join(choices)}"))
    return v


def _cfg_int(d: dict, key: str, lo: int, hi: int) -> int:
    v = d.get(key)
    if type(v) is not int or not lo <= v <= hi:
        raise ConfigError(bi(f"problem.toml: {key} 必须是 {lo}..{hi} 之间的整数",
                               f"problem.toml: {key} must be an integer in {lo}..{hi}"))
    return v


def load_problem(path: str | os.PathLike) -> Problem:
    """读一个 problem.toml。题目目录用 load_problem_dir，它还会核对目录名。"""
    with open(path, "rb") as f:
        d = tomllib.load(f)
    sub = d.get("submission", {})
    params = d.get("params", {})
    if not isinstance(sub, dict) or not isinstance(params, dict):
        raise ConfigError(bi("problem.toml: [submission] 与 [params] 必须是表",
                               "problem.toml: [submission] and [params] must be tables"))
    try:
        baseline = parse_rational(d.get("baseline_score"))
        epsilon = parse_rational(d.get("epsilon"))
    except ValueError as e:
        raise ConfigError(bi_prefix("problem.toml: baseline_score / epsilon ",
                                      "problem.toml: baseline_score / epsilon ", e)) from None
    if epsilon <= 0:
        raise ConfigError(bi("problem.toml: epsilon 必须 > 0", "problem.toml: epsilon must be > 0"))
    sandbox = d.get("sandbox", False)
    if type(sandbox) is not bool:
        raise ConfigError(bi("problem.toml: sandbox 必须是布尔值", "problem.toml: sandbox must be a boolean"))
    p = Problem(
        id=_cfg_str(d, "id", pattern=PROBLEM_ID_RE),
        slug=_cfg_str(d, "slug", pattern=SHOT_ID_RE),
        title_zh=_cfg_str(d, "title_zh"),
        title_en=_cfg_str(d, "title_en"),
        kind=_cfg_str(d, "kind", choices=("certificate",)),
        sandbox=sandbox,
        metric_zh=_cfg_str(d, "metric_zh", max_len=40),
        metric_en=_cfg_str(d, "metric_en", max_len=40),
        direction=_cfg_str(d, "direction", choices=("max", "min")),
        progress_unit=_cfg_str(d, "progress_unit", max_len=40),
        baseline=baseline,
        epsilon=epsilon,
        epsilon_mode=_cfg_str(d, "epsilon_mode", choices=("absolute", "relative")),
        progress_transform=_cfg_str(d, "progress_transform", choices=("linear", "log2")),
        verifier_timeout_sec=_cfg_int(d, "verifier_timeout_sec", 1, 3600),
        max_certificate_bytes=_cfg_int(d, "max_certificate_bytes", 1, 16 * 1024 * 1024),
        max_files=_cfg_int(sub, "max_files", 2, 64),
        max_file_bytes=_cfg_int(sub, "max_file_bytes", 1, 16 * 1024 * 1024),
        max_shot_md_bytes=_cfg_int(sub, "max_shot_md_bytes", 256, 1024 * 1024),
        max_total_bytes=_cfg_int(sub, "max_total_bytes", 1, 64 * 1024 * 1024),
        params=params,
    )
    if p.progress_transform == "log2" and p.baseline <= 0:
        raise ConfigError(bi("problem.toml: progress_transform = log2 要求基线 > 0",
                               "problem.toml: progress_transform = log2 requires a baseline > 0"))
    return p


def problem_path(root: str | os.PathLike, name: str) -> Path:
    """problems/<name>/ 的路径；name 必须是合规的题目目录名，目录与 problem.toml 都不能是符号链接。"""
    if not isinstance(name, str) or not PROBLEM_DIR_RE.fullmatch(name):
        raise ConfigError(bi("题目目录名必须形如 p001-slug", "The problem directory name must look like p001-slug"))
    base = Path(root) / PROBLEMS_DIR
    d = base / name
    if base.is_symlink() or d.is_symlink() or not d.is_dir():
        raise ConfigError(bi(f"main 上没有题目目录 {PROBLEMS_DIR}/{name}",
                               f"There is no problem directory {PROBLEMS_DIR}/{name} on main"))
    toml = d / "problem.toml"
    if toml.is_symlink() or not toml.is_file():
        raise ConfigError(bi(f"{PROBLEMS_DIR}/{name} 里没有 problem.toml", f"{PROBLEMS_DIR}/{name} has no problem.toml"))
    return d


def load_problem_dir(root: str | os.PathLike, name: str) -> Problem:
    """读 problems/<name>/problem.toml，并核对 <id>-<slug> 与目录名一致。"""
    d = problem_path(root, name)
    p = load_problem(d / "problem.toml")
    if f"{p.id}-{p.slug}" != name:
        raise ConfigError(bi(f"{PROBLEMS_DIR}/{name}/problem.toml 的 id 与 slug 和目录名不一致",
                               f"{PROBLEMS_DIR}/{name}/problem.toml: id and slug do not match the directory name"))
    return dataclasses.replace(p, dir=name)


def list_problem_dirs(root: str | os.PathLike = REPO_ROOT) -> list:
    """按目录名排序列出 problems/ 下所有合规的题目目录名（不读 problem.toml 的内容）。"""
    base = Path(root) / PROBLEMS_DIR
    if base.is_symlink() or not base.is_dir():
        return []
    out = []
    for d in sorted(base.iterdir()):
        if PROBLEM_DIR_RE.fullmatch(d.name) and not d.is_symlink() and d.is_dir():
            toml = d / "problem.toml"
            if toml.is_file() and not toml.is_symlink():
                out.append(d.name)
    return out


HEADER_START = "<!-- few-shots:header -->"
HEADER_END = "<!-- /few-shots:header -->"


def verifier_command(problem: Problem) -> str:
    return f"python3 {PROBLEMS_DIR}/{problem.dir}/verify/verify.py <shot_dir>"


def fmt_display(x: Fraction) -> str:
    """给人看的写法：分母只含 2 和 5 时写成精确的有限小数，否则同 fmt。"""
    d, k2, k5 = x.denominator, 0, 0
    while d % 2 == 0:
        d //= 2; k2 += 1
    while d % 5 == 0:
        d //= 5; k5 += 1
    if d != 1 or x.denominator == 1:
        return fmt(x)
    k = max(k2, k5)
    n = abs(x.numerator) * (10 ** k // x.denominator)
    digits = str(n).rjust(k + 1, "0")
    return ("-" if x < 0 else "") + digits[:-k] + "." + digits[-k:]


def render_problem_header(problem: Problem) -> str:
    """PROBLEM.md 开头的固定表头：每道题同样的键、同样的顺序，数值取自 problem.toml。"""
    rows = (
        ("id", f"`{problem.id}`"),
        ("metric", f"{problem.metric_zh} / {problem.metric_en}"),
        ("direction", f"`{problem.direction}` ({'higher' if problem.direction == 'max' else 'lower'} is better)"),
        ("baseline", f"`{fmt_display(problem.baseline)}`"),
        ("epsilon", f"`{fmt_display(problem.epsilon)}` (`{problem.epsilon_mode}`)"),
        ("verifier", f"`{verifier_command(problem)}`"),
    )
    lines = [HEADER_START, "| key | value |", "| --- | --- |"]
    lines += [f"| {k} | {v} |" for k, v in rows]
    lines.append(HEADER_END)
    return "\n".join(lines)


# ---------------------------------------------------------------- 文件读取


def read_bytes_limited(path: str | os.PathLike, limit: int) -> bytes:
    """读普通文件；拒绝符号链接、FIFO 等；超过 limit 字节报错。"""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise ValueError(bi(f"{Path(path).name} 不是普通文件", f"{Path(path).name} is not a regular file"))
        data = f.read(limit + 1)
    if len(data) > limit:
        raise ValueError(bi(f"{Path(path).name} 超过 {limit} 字节", f"{Path(path).name} is larger than {limit} bytes"))
    return data


def read_json_file(path: str | os.PathLike, limit: int = 1024 * 1024):
    return json.loads(read_bytes_limited(path, limit).decode("utf-8"))


def write_json_file(path: str | os.PathLike, obj) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")


def set_output(name: str, value: str) -> None:
    """写 $GITHUB_OUTPUT；只允许固定词表，绝不写入参与者数据。"""
    if not re.fullmatch(r"[a-z_]{1,32}", name) or value not in ("true", "false"):
        raise ValueError("set_output 只接受固定词表")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")


# ---------------------------------------------------------------- SHOT.md

SHOT_FIELDS = ("login", "builds_on", "claimed_score", "agent", "model", "models", "tokens_estimate", "affiliation")
# 纪录记在 login 这个 GitHub 账号名下；model 是这次突破主要出力的那一个模型，models 列其他帮了忙的模型（不进排行）
SHOT_REQUIRED = ("login", "builds_on", "claimed_score")
# 新提交还要写明用的工具和主要出力的模型（没用就写 "none"）；已合并的旧 shot 只按 SHOT_REQUIRED 解析，不受影响
SUBMIT_REQUIRED = SHOT_REQUIRED + ("agent", "model")
# agent 的取值：从这张表里选一个 ID；表里没有的写 "other: 工具名"，没用工具写 "none"。
# 分三组：模型厂自己的工具、编程 IDE / 编辑器、通用的开源或第三方 harness。改动时同步 AGENTS.md 里的表（有测试）。
AGENT_CHOICES = (
    ("claude-code", "Claude Code"), ("codex", "Codex"), ("gemini-cli", "Gemini CLI"), ("antigravity", "Antigravity"),
    ("jules", "Jules"), ("kimi-code", "Kimi Code"), ("qwen-code", "Qwen Code"), ("qoder", "Qoder"),
    ("trae", "TRAE"), ("codebuddy", "CodeBuddy"), ("comate", "Comate"), ("deepseek-harness", "DeepSeek Harness"),
    ("grok-build", "Grok Build"), ("mistral-vibe", "Mistral Vibe"), ("kiro", "Kiro"), ("github-copilot", "GitHub Copilot"),
    ("cursor", "Cursor"), ("windsurf", "Windsurf / Devin"), ("zed", "Zed"), ("junie", "Junie"),
    ("opencode", "OpenCode"), ("cline", "Cline"), ("roo-code", "Roo Code"), ("kilo-code", "Kilo Code"),
    ("aider", "Aider"), ("goose", "Goose"), ("openhands", "OpenHands"), ("amp", "Amp"),
    ("droid", "Droid"), ("warp", "Warp"), ("augment", "Augment Code"), ("continue", "Continue"),
)
_AGENT_BY_KEY = {k: (i, n) for i, n in AGENT_CHOICES for k in (i, n.lower(), n.lower().replace(" ", "-"))}
_AGENT_OTHER_RE = re.compile(r"other\s*[:：]\s*(\S.{0,59})", re.I)


def agent_choice(value: str) -> tuple[str, str] | None:
    """agent 字段 → (ID, 显示名)。表里的 ID 或名字（不分大小写）、"none"、"other: 工具名" 之外返回 None。"""
    v = (value or "").strip()
    if v.lower() == "none":
        return ("none", "none")
    m = _AGENT_OTHER_RE.fullmatch(v)
    if m:
        return ("other", m.group(1).strip())
    return _AGENT_BY_KEY.get(v.lower())


# model 要写完整型号（带小版本号），只写系列名的不算：如 gpt-6 有 sol、luna、astra 几个型号，要写 gpt-6-astra
_INCOMPLETE_MODEL_RE = re.compile(
    r"(gpt|o|claude|claude-(opus|sonnet|haiku|fable)|gemini|gemini-[0-9]+(\.[0-9]+)?|grok|deepseek|deepseek-v[0-9]+(\.[0-9]+)?"
    r"|qwen|kimi|glm|minimax|mistral|llama|gpt-6(\.0)?)", re.I)


def model_incomplete(value: str) -> bool:
    """只写了模型系列、没写具体型号（"gpt-6"、"claude-opus"、"gemini-3"）时为 True。"""
    v = re.sub(r"[\s_]+", "-", (value or "").strip().lower())
    return bool(v) and v != "none" and _INCOMPLETE_MODEL_RE.fullmatch(v) is not None


SECTION_IDEA = "思路"
SECTION_DEAD_ENDS = "哪里走不通"
# SHOT.md 也可以用英文标题；两种写法等价，同一份里只能用一种
SECTION_ALIASES = {SECTION_IDEA: "Approach", SECTION_DEAD_ENDS: "Dead ends"}
_HEADING_RE = re.compile(r"(#{1,6})[ \t]+(.*?)[ \t#]*")


@dataclass(frozen=True)
class Shot:
    login: str
    models: tuple
    agent: str
    model: str
    tokens_estimate: int
    affiliation: str
    builds_on: tuple
    claimed_score: Fraction
    claimed_score_text: str
    idea: str
    dead_ends: str


def _has_invisible(s: str) -> bool:
    return any(unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs", "Cn") for ch in s)


def _meta_str(meta: dict, key: str, max_len: int, errs: list) -> str:
    v = meta.get(key)
    if not isinstance(v, str) or not v.strip():
        errs.append(bi(f"{key} 必须是非空字符串", f"{key} must be a non-empty string"))
        return ""
    if len(v) > max_len:
        errs.append(bi(f"{key} 不能超过 {max_len} 个字符", f"{key} must be at most {max_len} characters"))
    if _has_invisible(v):
        errs.append(bi(f"{key} 含有控制字符或不可见字符", f"{key} contains control or invisible characters"))
    return v.strip()


def _opt_str(meta: dict, key: str, max_len: int, errs: list) -> str:
    """可以不写或写空字符串的单行字段；写了就按必填字段的规则校验。"""
    v = meta.get(key, "")
    if isinstance(v, str) and not v.strip():
        return ""
    return _meta_str(meta, key, max_len, errs)


def split_sections(body: str, first_line: int = 1) -> tuple[dict, list]:
    """按二级标题（## ）切分正文；代码块里的 ## 不算标题。返回 (章节, 错误)。

    错误信息不回显参与者写的标题文字，只给行号（first_line 是 body 第一行在文件中的行号）。
    """
    sections: dict = {}
    errs: list = []
    current = None
    fence = None
    for lineno, line in enumerate(body.split("\n"), first_line):
        s = line.strip()
        if fence:
            if s.startswith(fence):
                fence = None
        elif s.startswith("```") or s.startswith("~~~"):
            fence = s[:3]
        else:
            m = _HEADING_RE.fullmatch(s)
            if m and len(m.group(1)) == 2:
                name = m.group(2).strip()
                if name in sections:
                    errs.append(bi(f"有重复的二级标题（第 {lineno} 行）", f"Duplicate level-2 heading (line {lineno})"))
                sections[name] = []
                current = name
                continue
        if current is not None:
            sections[current].append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}, errs


def parse_shot_md(text: str) -> Shot:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.split("\n")
    if not lines or lines[0].strip() != "+++":
        raise ShotError([bi("SHOT.md 必须以 +++ 包裹的 TOML front matter 开头",
                              "SHOT.md must start with TOML front matter enclosed in +++")])
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "+++"), None)
    if end is None:
        raise ShotError([bi("front matter 缺少结束的 +++", "The front matter is missing the closing +++")])
    try:
        meta = tomllib.loads("\n".join(lines[1:end]))
    except tomllib.TOMLDecodeError:
        raise ShotError([bi("front matter 不是合法的 TOML", "The front matter is not valid TOML")]) from None
    except (RecursionError, MemoryError):
        # 深层嵌套的数组会让 tomllib 递归溢出
        raise ShotError([bi("front matter 嵌套过深或过大", "The front matter is nested too deeply or too large")]) from None

    errs: list = []
    unknown = set(meta) - set(SHOT_FIELDS)
    if unknown:
        # 不回显参与者写的键名
        errs.append(bi(f"front matter 有 {len(unknown)} 个未知字段（只允许：{', '.join(SHOT_FIELDS)}）",
                          f"The front matter has {len(unknown)} unknown field(s) (allowed: {', '.join(SHOT_FIELDS)})"))
    missing = [k for k in SHOT_REQUIRED if k not in meta]
    if missing:
        errs.append(bi("front matter 缺少字段：" + ", ".join(missing),
                          "The front matter is missing field(s): " + ", ".join(missing)))

    login = _meta_str(meta, "login", 39, errs)
    if login and not LOGIN_RE.fullmatch(login):
        errs.append(bi("login 不是合法的 GitHub 登录名", "login is not a valid GitHub login"))
    models = meta.get("models", [])
    if not isinstance(models, list) or len(models) > 10:
        errs.append(bi("models 必须是最多 10 个字符串组成的数组", "models must be an array of at most 10 strings"))
        models = []
    else:
        for m in models:
            if not isinstance(m, str) or not m.strip() or len(m) > 100 or _has_invisible(m):
                errs.append(bi("models 中每一项都必须是不超过 100 字符的单行字符串",
                              "Each item in models must be a single-line string of at most 100 characters"))
                break
    agent = _opt_str(meta, "agent", 100, errs)
    model = _opt_str(meta, "model", 100, errs)
    affiliation = _opt_str(meta, "affiliation", 200, errs)
    tokens = meta.get("tokens_estimate", 0)
    if type(tokens) is not int or not 0 <= tokens <= 10**13:
        errs.append(bi("tokens_estimate 必须是 0 到 10^13 之间的整数",
                          "tokens_estimate must be an integer from 0 to 10^13"))
        tokens = 0
    builds_on = meta.get("builds_on")
    if not isinstance(builds_on, list) or len(builds_on) > 20:
        errs.append(bi('builds_on 必须是数组（最多 20 项），每项形如 "login/shot-id"；没有就写 []',
                          'builds_on must be an array of at most 20 items like "login/shot-id"; write [] if none'))
        builds_on = []
    elif not all(isinstance(b, str) and SHOT_REF_RE.fullmatch(b) for b in builds_on):
        errs.append(bi('builds_on 的每一项都必须形如 "login/shot-id"',
                          'Each item in builds_on must look like "login/shot-id"'))
    claimed_text = meta.get("claimed_score")
    claimed = Fraction(0)
    try:
        claimed = parse_rational(claimed_text)
    except ValueError as e:
        errs.append(bi_prefix("claimed_score ", "claimed_score ", e))

    sections, sec_errs = split_sections("\n".join(lines[end + 1:]), first_line=end + 2)
    errs.extend(sec_errs)
    for name in (SECTION_IDEA, SECTION_DEAD_ENDS):
        alias = SECTION_ALIASES[name]
        if name in sections and alias in sections:
            errs.append(bi(f"「## {name}」和「## {alias}」只能写一个", f"Use either ## {name} or ## {alias}, not both"))
        elif alias in sections:
            sections[name] = sections.pop(alias)
        if name not in sections:
            errs.append(bi(f"正文缺少二级标题「## {name}」或「## {alias}」", f"Missing section ## {alias} (or ## {name})"))
        elif not sections[name]:
            errs.append(bi(f"「## {name}」不能为空", f"## {alias} must not be empty"))
    if errs:
        raise ShotError(errs)
    return Shot(
        login=login,
        models=tuple(m.strip() for m in models),
        agent=agent,
        model=model,
        tokens_estimate=tokens,
        affiliation=affiliation,
        builds_on=tuple(builds_on),
        claimed_score=claimed,
        claimed_score_text=claimed_text,
        idea=sections[SECTION_IDEA],
        dead_ends=sections[SECTION_DEAD_ENDS],
    )


SHOT_DIR_HINT = f"{PROBLEMS_DIR}/<题目目录>/shots/<login>/<shot-id>/"
SHOT_DIR_HINT_EN = f"{PROBLEMS_DIR}/<problem-dir>/shots/<login>/<shot-id>/"


def locate_shot_dir(shot_dir: str | os.PathLike, root: str | os.PathLike = REPO_ROOT) -> tuple[str, str, str]:
    """从 shot 目录的路径推出 (题目目录名, login, shot_id)；路径必须是 <root>/problems/<题目>/shots/<login>/<id>。"""
    shot_dir = Path(os.path.abspath(shot_dir))
    root = Path(os.path.abspath(root))
    try:
        parts = shot_dir.relative_to(root).parts
    except ValueError:
        parts = ()
    if len(parts) != 5 or parts[0] != PROBLEMS_DIR or parts[2] != "shots":
        raise ShotError([bi(f"shot 目录必须位于本仓库的 {SHOT_DIR_HINT}",
                              f"The shot directory must be {SHOT_DIR_HINT_EN} in this repository")])
    return parts[1], parts[3], parts[4]


def check_local_shot_dir(shot_dir: str | os.PathLike, problem: Problem) -> tuple[str, str, list]:
    """本地自检用的目录检查，规则与线上门禁一致。返回 (login, shot_id, 文件名列表)。
    shot_dir 的位置（题目目录、shots/）由 locate_shot_dir 先行检查。"""
    shot_dir = Path(shot_dir).absolute()
    errs: list = []
    shot_id, login = shot_dir.name, shot_dir.parent.name
    if not LOGIN_RE.fullmatch(login):
        errs.append(bi("目录中的 login 不是合法的 GitHub 登录名", "The login in the path is not a valid GitHub login"))
    if not SHOT_ID_RE.fullmatch(shot_id):
        errs.append(bi("shot-id 只能用小写字母、数字和连字符，不超过 64 个字符",
                          "shot-id may only use lowercase letters, digits and hyphens, at most 64 characters"))
    if shot_dir.is_symlink() or not shot_dir.is_dir():
        raise ShotError(errs + [bi("shot 目录不存在或是符号链接", "The shot directory does not exist or is a symlink")])
    entries = sorted(os.scandir(shot_dir), key=lambda e: e.name)
    if len(entries) > problem.max_files:
        errs.append(bi(f"文件数 {len(entries)} 超过上限 {problem.max_files}",
                          f"{len(entries)} files, more than the limit of {problem.max_files}"))
    names, total = [], 0
    for e in entries:
        shown = e.name if FILE_NAME_RE.fullmatch(e.name) else "（不合规的文件名 · invalid file name）"
        if e.is_symlink():
            errs.append(bi(f"{shown} 是符号链接，不允许", f"{shown} is a symlink, which is not allowed"))
        elif e.is_dir(follow_symlinks=False):
            errs.append(bi(f"{shown} 是子目录，不允许", f"{shown} is a subdirectory, which is not allowed"))
        elif not e.is_file(follow_symlinks=False):
            errs.append(bi(f"{shown} 不是普通文件", f"{shown} is not a regular file"))
        elif e.name.lower() in RESERVED_FILES:
            errs.append(bi(*RESERVED_TEXT))
        elif not FILE_NAME_RE.fullmatch(e.name):
            errs.append(bi("文件名不合规：只允许字母数字开头的 .md/.json/.txt/.csv，不允许隐藏文件",
                           "Invalid file name: only .md/.json/.txt/.csv names that start with a letter or digit; "
                           "no hidden files"))
        else:
            size = e.stat(follow_symlinks=False).st_size
            limit = problem.file_limit(e.name)
            if size > limit:
                errs.append(bi(f"{e.name} 有 {size} 字节，超过上限 {limit}",
                               f"{e.name} is {size} bytes, over the limit of {limit}"))
            total += size
            names.append(e.name)
    if total > problem.max_total_bytes:
        errs.append(bi(f"总大小 {total} 字节，超过上限 {problem.max_total_bytes}",
                       f"Total size is {total} bytes, over the limit of {problem.max_total_bytes}"))
    for req in REQUIRED_FILES:
        if req not in names:
            errs.append(bi(f"缺少 {req}", f"Missing {req}"))
    if errs:
        raise ShotError(errs)
    return login, shot_id, names


# ---------------------------------------------------------------- main 上的 shots（纪录链）


@dataclass(frozen=True)
class MergedShot:
    login: str
    shot_id: str
    commit: str | None
    shot: Shot | None
    error: str | None
    path: Path | None = None

    @property
    def ref(self) -> str:
        return f"{self.login}/{self.shot_id}"


def _git_env() -> dict:
    env = dict(os.environ)
    env.update({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", "GIT_PAGER": "cat"})
    return env


def git_head(root: str | os.PathLike, ref: str = "HEAD") -> str | None:
    """main checkout 当前提交的 SHA；读不到返回 None。"""
    try:
        proc = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", f"{ref}^{{commit}}"],
                              capture_output=True, timeout=30, env=_git_env())
    except (OSError, subprocess.SubprocessError):
        return None
    sha = proc.stdout.decode("ascii", "replace").strip()
    return sha if proc.returncode == 0 and COMMIT_SHA_RE.fullmatch(sha) else None


def git_merge_order(root: Path, pdir: str, ref: str = "HEAD") -> list | None:
    """按 main 的第一父链、按时间正序列出 problems/<pdir>/shots/<login>/<id>/SHOT.md 的新增顺序。
    返回 [("login/shot-id", 引入它的提交)]；不是 git 仓库或读取失败返回 None。"""
    prefix = f"{PROBLEMS_DIR}/{pdir}/shots"
    cmd = [
        "git", "-C", str(root), "log", ref,
        "--first-parent", "--diff-merges=first-parent", "--reverse", "--no-renames",
        "--diff-filter=A", "--name-only", "--format=@@%H", "--", prefix,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=60, env=_git_env())
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    order, seen, commit = [], set(), None
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith("@@"):
            commit = line[2:].strip()
            continue
        parts = line.strip().split("/")
        if len(parts) == 6 and "/".join(parts[:3]) == prefix and parts[5] == "SHOT.md":
            r = f"{parts[3]}/{parts[4]}"
            if r not in seen:
                seen.add(r)
                order.append((r, commit))
    return order


def list_merged_shots(root: str | os.PathLike, pdir: str, ref: str = "HEAD",
                      exclude: frozenset | set = frozenset()) -> tuple[list, str]:
    """problems/<pdir>/shots 下的 shot。返回 (按合并顺序排列的 MergedShot 列表, 顺序来源 "git"|"filesystem")。"""
    root = Path(root)
    found: dict = {}
    shots_dir = problem_path(root, pdir) / "shots"
    if shots_dir.is_dir() and not shots_dir.is_symlink():
        for ld in sorted(shots_dir.iterdir()):
            if ld.is_symlink() or not ld.is_dir() or not LOGIN_RE.fullmatch(ld.name):
                continue
            for sd in sorted(ld.iterdir()):
                if sd.is_symlink() or not sd.is_dir() or not SHOT_ID_RE.fullmatch(sd.name):
                    continue
                found[f"{ld.name}/{sd.name}"] = sd
    order = git_merge_order(root, pdir, ref)
    source = "git" if order is not None else "filesystem"
    ordered = [(r, c) for r, c in (order or []) if r in found]
    in_git = {r for r, _ in ordered}
    ordered += [(r, None) for r in sorted(found) if r not in in_git]
    out = []
    for r, commit in ordered:
        if r in exclude:
            continue
        login, sid = r.split("/")
        shot, err = None, None
        try:
            data = read_bytes_limited(found[r] / "SHOT.md", 1024 * 1024)
            shot = parse_shot_md(data.decode("utf-8"))
            if shot.login != login:
                shot, err = None, "SHOT.md 的 login 与目录不一致"
        except ShotError as e:
            err = "；".join(e.messages)[:300]
        except Exception:  # 单个 shot 出任何问题都只跳过它，不让整次运行失败
            err = "SHOT.md 无法读取或解析"
        out.append(MergedShot(login, sid, commit, shot, err, found[r]))
    return out, source


# ---------------------------------------------------------------- 纪录与判定

VERDICT_TEXT = {
    "below_baseline": "未达到预设基线 · Below the baseline",
    "not_record": "达到预设基线，但没有刷新当前纪录 · Reaches the baseline but does not beat the current record",
    "below_epsilon": "刷新了当前纪录，但改进幅度不足 ε · Beats the current record by less than ε",
    "breakthrough": "有效突破 · Valid breakthrough",
}


def better(direction: str, a: Fraction, b: Fraction) -> bool:
    return a > b if direction == "max" else a < b


def at_least(direction: str, a: Fraction, b: Fraction) -> bool:
    return a >= b if direction == "max" else a <= b


def required_score(problem: Problem, record: Fraction) -> Fraction:
    """成为有效突破至少要达到的分数：当前纪录再改进 ε（relative 时为 |纪录|×ε）。"""
    step = problem.epsilon if problem.epsilon_mode == "absolute" else abs(record) * problem.epsilon
    return record + step if problem.direction == "max" else record - step


@dataclass(frozen=True)
class Record:
    score: Fraction
    ref: str | None  # None 表示预设基线


@dataclass(frozen=True)
class ChainEntry:
    """main 上一个经验证器复算通过、且 claimed_score 与复算结果相等的 shot。"""
    ref: str
    score: Fraction
    canonical_hash: str


def verify_merged(merged: list, run_verifier: Callable[[Path], dict]) -> tuple[list, list]:
    """对 main 上每个 shot 重跑验证器，不直接相信 SHOT.md 里的 claimed_score。

    返回 (计入纪录的 ChainEntry 列表, 给裁判看的警告)。复算无效或与 claimed_score 不一致的
    shot 不计入纪录；复算超时或流水线出错时抛 VerifierError（宁可不打分，也不按不完整的纪录判定）。
    """
    entries: list = []
    warnings: list = []
    for m in merged:
        if m.shot is None or m.path is None:
            warnings.append(bi(f"main 上的 {m.ref} 的 SHOT.md 无法解析，未计入纪录",
                               f"{m.ref} on main: SHOT.md cannot be parsed; not counted toward the record"))
            continue
        try:
            v = run_verifier(m.path)
        except VerifierRejected:
            raise VerifierError(bi(f"复算 main 上的 {m.ref} 时超时或超出资源限制",
                                     f"Re-verifying {m.ref} on main timed out or exceeded resource limits")) from None
        if not v["valid"]:
            warnings.append(bi(f"main 上的 {m.ref} 复算无效，未计入纪录",
                               f"{m.ref} on main is invalid when re-verified; not counted toward the record"))
        elif v["score"] != m.shot.claimed_score:
            warnings.append(bi(f"main 上的 {m.ref} 的 claimed_score 与复算结果不一致，未计入纪录",
                               f"{m.ref} on main: claimed_score differs from the re-verified score; "
                               "not counted toward the record"))
        else:
            entries.append(ChainEntry(m.ref, v["score"], v["canonical_hash"]))
    return entries, warnings


def current_record(problem: Problem, chain: list) -> Record:
    """当前纪录 = 预设基线与 main 上复算通过的各 shot（按合并顺序）中的最好成绩。"""
    rec = Record(problem.baseline, None)
    for e in chain:
        if better(problem.direction, e.score, rec.score):
            rec = Record(e.score, e.ref)
    return rec


def judge(problem: Problem, record: Fraction, score: Fraction) -> str:
    if not at_least(problem.direction, score, problem.baseline):
        return "below_baseline"
    if not better(problem.direction, score, record):
        return "not_record"
    if not at_least(problem.direction, score, required_score(problem, record)):
        return "below_epsilon"
    return "breakthrough"


def _log2(x: Fraction) -> float:
    return math.log2(x.numerator) - math.log2(x.denominator)


def progress(problem: Problem, score: Fraction) -> str | None:
    """相对预设基线的进度，按 progress_transform 计算；仅用于展示。"""
    b = problem.baseline
    if problem.progress_transform == "linear":
        return fmt(score - b if problem.direction == "max" else b - score)
    if score <= 0 or b <= 0:
        return None
    num, den = (score, b) if problem.direction == "max" else (b, score)
    return f"{_log2(num) - _log2(den):.6f}"


# ---------------------------------------------------------------- 验证器输出

VERIFIER_KEYS = frozenset({"valid", "score", "canonical_hash", "message"})
MAX_VERIFIER_OUTPUT = 64 * 1024


class VerifierError(Exception):
    """流水线问题（不是参与者的问题）。"""


class VerifierRejected(Exception):
    """由输入导致的失败：超时、超内存被杀等，按证书无效处理。"""


def _reject_constant(_s):
    raise ValueError("constant")


def parse_verifier_output(raw: bytes) -> dict:
    if len(raw) > MAX_VERIFIER_OUTPUT:
        raise VerifierError(bi("验证器输出过长", "The verifier output is too long"))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise VerifierError(bi("验证器输出不是 UTF-8", "The verifier output is not UTF-8")) from None
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) != 1:
        raise VerifierError(bi("验证器必须且只能输出一行 JSON", "The verifier must print exactly one line of JSON"))
    try:
        obj = json.loads(lines[0], parse_constant=_reject_constant)
    except ValueError:
        raise VerifierError(bi("验证器输出不是合法 JSON", "The verifier output is not valid JSON")) from None
    if not isinstance(obj, dict) or set(obj) != VERIFIER_KEYS:
        raise VerifierError(bi("验证器输出的字段不符合契约", "The verifier output fields do not match the contract"))
    if type(obj["valid"]) is not bool or not isinstance(obj["message"], str):
        raise VerifierError(bi("验证器输出的字段类型不符合契约",
                                 "The verifier output field types do not match the contract"))
    message = obj["message"][:500]
    if not obj["valid"]:
        return {"valid": False, "score": None, "canonical_hash": None, "message": message}
    try:
        score = parse_rational(obj["score"])
    except ValueError:
        raise VerifierError(bi("验证器给出的 score 不是精确有理数字符串",
                                 "The verifier score is not an exact rational string")) from None
    h = obj["canonical_hash"]
    if not isinstance(h, str) or not HASH_RE.fullmatch(h):
        raise VerifierError(bi("验证器给出的 canonical_hash 不是 sha256", "The verifier canonical_hash is not a sha256"))
    return {"valid": True, "score": score, "canonical_hash": h, "message": message}


def run_verifier_local(shot_dir: str | os.PathLike, timeout: int, verify_py: str | os.PathLike) -> dict:
    """本地直接用 python 跑题目目录里的 verify/verify.py（本机自检用；线上用 scripts/score.py 的容器）。"""
    verify_py = Path(verify_py)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8",
           "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        proc = subprocess.run(
            [sys.executable, "-I", str(verify_py), str(Path(shot_dir).absolute())],
            capture_output=True, timeout=timeout, env=env, cwd=str(verify_py.parent),
        )
    except subprocess.TimeoutExpired:
        raise VerifierRejected(bi(f"验证超时（{timeout} 秒）", f"Verification timed out ({timeout} s)")) from None
    if proc.returncode != 0:
        raise VerifierError(bi(f"验证器退出码 {proc.returncode}", f"Verifier exit code {proc.returncode}"))
    return parse_verifier_output(proc.stdout)


# ---------------------------------------------------------------- 评估一个 shot


def evaluate_shot(problem: Problem, shot_dir: Path, expected_login: str,
                  run_verifier: Callable[[Path], dict], merged: list) -> dict:
    """解析 SHOT.md → 验证 → 复算 main 上的纪录链 → 核对 claimed_score → 去重 → 与基线、纪录 + ε 比较。"""
    out: dict = {"status": "error", "messages": []}
    try:
        text = read_bytes_limited(shot_dir / "SHOT.md", problem.max_shot_md_bytes).decode("utf-8")
        shot = parse_shot_md(text)
        if not shot.agent or not shot.model:
            raise ShotError([bi('要写明 agent（用的工具）和 model（这次突破主要出力的那一个模型）；没用就写 "none"',
                                'state agent (the tool you used) and model (the one model that did most of the work '
                                'for this breakthrough); write "none" if you used none')])
        if model_incomplete(shot.model):
            raise ShotError([bi('model 要写完整型号，带小版本号，如 "gpt-6-astra"、"gpt-6-sol"、"claude-opus-5-5"；只写 "gpt-6" 这样的系列名不行',
                                'model must be the full model name with its variant, such as "gpt-6-astra", "gpt-6-sol" or '
                                '"claude-opus-5-5"; a family name like "gpt-6" is not enough')])
        if agent_choice(shot.agent) is None:
            raise ShotError([bi('agent 要从 AGENTS.md 的工具列表里选一个 ID（如 "claude-code"）；列表里没有就写 "other: 工具名"，没用工具写 "none"',
                                'agent must be an ID from the tool list in AGENTS.md (such as "claude-code"); if yours is not '
                                'listed write "other: tool name", or "none"')])
    except ShotError as e:
        out.update(status="rejected", messages=[bi_prefix("SHOT.md：", "SHOT.md: ", m) for m in e.messages])
        return out
    except (OSError, ValueError):
        out.update(status="rejected", messages=[bi("无法读取 SHOT.md（必须是不超过上限的 UTF-8 文本）",
                                                     "Cannot read SHOT.md (it must be UTF-8 text within the size limit)")])
        return out
    out["claimed_score"] = fmt(shot.claimed_score)
    if shot.login != expected_login:
        out.update(status="rejected", messages=[bi("SHOT.md 中的 login 必须与目录名（PR 作者）一致",
                                                     "login in SHOT.md must match the directory name (the PR author)")])
        return out
    try:
        v = run_verifier(shot_dir)
    except VerifierRejected as e:
        out.update(status="invalid", messages=[str(e)])
        return out
    except VerifierError as e:
        out.update(status="error", messages=[bi_prefix("验证器运行失败：", "The verifier failed: ", e)])
        return out
    out["verifier_message"] = v["message"]
    if not v["valid"]:
        # 验证器的说明只占一行，前面标上中英文
        out.update(status="invalid", messages=["验证器 · Verifier：" + " ".join(v["message"].split())])
        return out
    score = v["score"]
    out.update(score=fmt(score), canonical_hash=v["canonical_hash"],
               progress=progress(problem, score))
    try:
        chain, warnings = verify_merged(merged, run_verifier)
    except VerifierError as e:
        out.update(status="error", messages=[bi_prefix("复算 main 上的纪录链失败：",
                                                        "Re-verifying the record chain on main failed: ", e)])
        return out
    out["chain_warnings"] = warnings
    rec = current_record(problem, chain)
    out.update(record=fmt(rec.score), record_ref=rec.ref,
               required=fmt(required_score(problem, rec.score)))
    if shot.claimed_score != score:
        out.update(status="claim_mismatch",
                   messages=[bi("claimed_score 与验证器结果不一致，请改成验证器给出的值",
                                 "claimed_score differs from the verifier's score; set it to the value the verifier gives")])
        return out
    same = next((e.ref for e in chain if e.canonical_hash == v["canonical_hash"]), None)
    if same is not None:
        out.update(status="duplicate", messages=[bi(f"证书与 main 上的 {same} 本质相同（canonical_hash 相同）",
                                                      f"The certificate is essentially the same as {same} on main "
                                                      "(same canonical_hash)")])
        return out
    out["status"] = judge(problem, rec.score, score)
    out["messages"] = [VERDICT_TEXT[out["status"]]]
    return out


# ---------------------------------------------------------------- 文本清洗

_DROP_RANGES = ((0xFE00, 0xFE0F), (0xE0100, 0xE01EF), (0xE0000, 0xE007F))  # 变体选择符、tag 字符
_DROP_CHARS = {"ᅟ", "ᅠ", "ㅤ", "ﾠ", "͏", "᠋", "᠌", "᠍", "᠎"}
_BAD_CATEGORIES = ("Cc", "Cf", "Co", "Cs", "Cn")


def strip_invisible(text: str, keep: str = "\n\t") -> str:
    """去掉控制字符、零宽字符、方向控制符、Unicode tag 字符、变体选择符等。"""
    out = []
    for ch in text:
        if ch in keep:
            out.append(ch)
            continue
        o = ord(ch)
        if ch in _DROP_CHARS or any(a <= o <= b for a, b in _DROP_RANGES):
            continue
        cat = unicodedata.category(ch)
        if cat in _BAD_CATEGORIES:
            continue
        if cat in ("Zl", "Zp"):
            out.append("\n")
        elif cat == "Zs":
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


_HTML_COMMENT_RE = re.compile(r"<!--.*?(?:-->|\Z)", re.S)
_FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})[^\n]*\n?.*?(?:^[ \t]*\1[`~]*[ \t]*$|\Z)", re.S | re.M)
_INLINE_CODE_RE = re.compile(r"(`+)(?:(?!\1).)+?\1", re.S)
_IMAGE_RE = re.compile(r"!\[[^\]\n]*\]\([^)\n]*\)")
_LINK_RE = re.compile(r"\[([^\]\n]*)\]\([^)\n]*\)")
_REF_LINK_RE = re.compile(r"\[([^\]\n]+)\]\[[^\]\n]*\]")
_REF_DEF_RE = re.compile(r"^[ \t]*\[[^\]\n]+\]:.*$", re.M)
_SCRIPT_RE = re.compile(r"<(script|style|iframe|object|template)\b.*?(?:</\1\s*>|\Z)", re.S | re.I)
_TAG_RE = re.compile(r"</?[A-Za-z!?/][^>\n]{0,500}>")
_URL_RE = re.compile(r"(?i)\b(?:[a-z][a-z0-9+.-]{1,20}://\S*|www\.\S+|(?:mailto|javascript|data|vbscript|file):\S+)")
_INDENTED_CODE_RE = re.compile(r"(?:^(?:\t| {4,})[^\n]*\n?)+", re.M)
_LEAD_MARK_RE = re.compile(r"^(?:[ \t]*(?:#{1,6}|>)[ \t]*)+")
_HR_RE = re.compile(r"^[ \t]*(?:[-*_][ \t]*){3,}$", re.M)


def sanitize_text(text: str, limit: int = 800, line_limit: int = 300) -> str:
    """把参与者写的 Markdown 洗成纯文本摘要：去 HTML/注释/链接/代码块/不可见字符，限长。"""
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = strip_invisible(text)
    text = _HTML_COMMENT_RE.sub("", text)
    text = _FENCE_RE.sub("[代码块已移除 · code block removed]\n", text)
    text = _INDENTED_CODE_RE.sub("[代码块已移除 · code block removed]\n", text)
    text = _INLINE_CODE_RE.sub("[代码已移除 · code removed]", text)
    text = _IMAGE_RE.sub("", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _REF_LINK_RE.sub(r"\1", text)
    text = _REF_DEF_RE.sub("", text)
    text = _SCRIPT_RE.sub("", text)
    text = _TAG_RE.sub("", text)
    text = _URL_RE.sub("[链接已移除 · link removed]", text)
    text = _HR_RE.sub("", text)
    lines = []
    for line in text.split("\n"):
        line = _LEAD_MARK_RE.sub("", line).strip()
        line = line.replace("<", "＜").replace(">", "＞").replace("`", "'")
        if len(line) > line_limit:
            line = line[:line_limit].rstrip() + "…"
        lines.append(line)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "…（已截断 · truncated）"
    return text


def sanitize_inline(text: object, limit: int = 80) -> str:
    """单行展示用：去不可见字符、合并空白、去掉 Markdown/HTML 结构字符与 @ 提及。"""
    s = strip_invisible(str(text), keep="")
    s = re.sub(r"\s+", " ", s).strip()
    for a, b in (("`", "'"), ("|", "¦"), ("<", "＜"), (">", "＞"), ("[", "［"), ("]", "］"),
                 ("@", "＠"), ("\\", "＼"), ("*", "＊")):
        s = s.replace(a, b)
    if len(s) > limit:
        s = s[:limit].rstrip() + "…"
    return s


_GH_REF_RE = re.compile(r"(?i)\bgh-(?=[0-9])")


def sanitize_message(text: object, limit: int = 300) -> str:
    """评论里的说明文字：在 sanitize_inline 之外再去掉链接，并让 #编号、owner/repo#N、GH-N 引用失效，
    转义 ~ 与 _，避免以机器人的名义发出可点击链接、交叉引用或格式。"""
    s = sanitize_inline(text, 4 * limit)
    s = _URL_RE.sub("[链接已移除 · link removed]", s)
    s = s.replace("#", "＃").replace("~", "～")
    s = _GH_REF_RE.sub("GH－", s)
    if len(s) > limit:
        s = s[:limit].rstrip() + "…"
    return s.replace("_", "\\_")


# ---------------------------------------------------------------- GitHub API（标准库实现）


class GitHubAPIError(Exception):
    def __init__(self, status: int, message: str = ""):
        self.status = status
        super().__init__(f"HTTP {status} {message}".strip())


class GitHubAPI:
    """最小的 GitHub REST 客户端。默认只读：非 GET 请求会被拒绝。"""

    def __init__(self, token: str | None, *, base_url: str = "https://api.github.com",
                 allow_write: bool = False, timeout: int = 30, max_bytes: int = 16 * 1024 * 1024):
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.allow_write = allow_write
        self.timeout = timeout
        self.max_bytes = max_bytes

    def request(self, method: str, path: str, params: dict | None = None, body=None):
        if method != "GET" and not self.allow_write:
            raise PermissionError("只读客户端不允许 " + method)
        if not path.startswith("/") or ".." in path or "?" in path:
            raise ValueError("非法 API 路径")
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "few-shots-pipeline",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read(self.max_bytes + 1)
        except urllib.error.HTTPError as e:
            raise GitHubAPIError(e.code) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise GitHubAPIError(0, type(e).__name__) from None
        if len(raw) > self.max_bytes:
            raise GitHubAPIError(0, "响应过大")
        return json.loads(raw) if raw else None

    def get(self, path: str, params: dict | None = None):
        return self.request("GET", path, params)


def q(part: str) -> str:
    """URL 路径分量转义。"""
    return urllib.parse.quote(part, safe="")
