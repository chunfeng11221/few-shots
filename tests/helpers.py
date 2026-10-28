"""测试共用的小工具：临时仓库（带 problems/p000-sandbox）、git、Golomb 尺样例。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import fslib as F  # noqa: E402

SANDBOX = "p000-sandbox"
BASELINE_12 = [2**k - 1 for k in range(12)]                     # 长度 2047
ERDOS_TURAN_12 = [26 * k + (k * k % 13) for k in range(12)]      # 长度 290
OPTIMAL_12 = [0, 2, 6, 24, 29, 40, 43, 55, 68, 75, 76, 85]       # 长度 85


def ruler_with_last(last: int) -> list:
    """前 11 个刻度取 2^k−1，最后一个刻度为 last。调用方用 is_golomb 确认合法。"""
    return [2**k - 1 for k in range(11)] + [last]


def is_golomb(marks: list) -> bool:
    xs = sorted(marks)
    diffs = [b - a for i, a in enumerate(xs) for b in xs[i + 1:]]
    return len(set(xs)) == len(xs) and len(set(diffs)) == len(diffs)


def cert(marks: list) -> str:
    return '{"marks": [' + ", ".join(str(m) for m in marks) + "]}\n"


def shot_md(login: str, claimed: str, idea: str = "贪心构造。", dead: str = "暂无。",
            builds_on: tuple = (), models: tuple = ("model-x",), affiliation: str = "个人") -> str:
    b = ", ".join(f'"{x}"' for x in builds_on)
    m = ", ".join(f'"{x}"' for x in models)
    return (
        "+++\n"
        f'login = "{login}"\n'
        f"builds_on = [{b}]\n"
        f'claimed_score = "{claimed}"\n'
        'agent = "claude-code"\n'
        'model = "model-main"\n'
        f"models = [{m}]\n"
        "tokens_estimate = 1000\n"
        f'affiliation = "{affiliation}"\n'
        "+++\n\n"
        f"## 思路\n\n{idea}\n\n## 哪里走不通\n\n{dead}\n"
    )


def git_env() -> dict:
    env = dict(os.environ)
    env.update({
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    })
    return env


def git(root: Path, *args: str, env: dict | None = None) -> str:
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, env={**git_env(), **(env or {})},
                         check=True)
    return out.stdout.decode()


def copy_problem(src_root: Path, dst_root: Path, name: str = SANDBOX, new_name: str | None = None,
                 toml_replacements: dict | None = None) -> Path:
    """复制一道题的 problem.toml、verify/、baseline/、.dockerignore；可改目录名与 problem.toml。"""
    src = src_root / "problems" / name
    new_name = new_name or name
    dst = dst_root / "problems" / new_name
    dst.mkdir(parents=True)
    text = (src / "problem.toml").read_text(encoding="utf-8")
    if new_name != name:
        pid, slug = new_name.split("-", 1)
        text = text.replace('id = "p000"', f'id = "{pid}"').replace('slug = "sandbox"', f'slug = "{slug}"')
    for old, new in (toml_replacements or {}).items():
        assert old in text, old
        text = text.replace(old, new)
    (dst / "problem.toml").write_text(text, encoding="utf-8")
    for d in ("verify", "baseline"):
        shutil.copytree(src / d, dst / d, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(src / ".dockerignore", dst / ".dockerignore")
    return dst


class TempRepo:
    """临时仓库：scripts/、tools/、templates/ 与 problems/p000-sandbox（可以替换 problem.toml 里的文字）。"""

    def __init__(self, toml_replacements: dict | None = None, with_git: bool = False, extra_problems=()):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "repo"
        self.root.mkdir()
        copy_problem(ROOT, self.root, toml_replacements=toml_replacements)
        for name in extra_problems:
            copy_problem(ROOT, self.root, new_name=name)
        for d in ("scripts", "tools", "templates"):
            shutil.copytree(ROOT / d, self.root / d, ignore=shutil.ignore_patterns("__pycache__"))
        if with_git:
            git(self.root, "init", "-q", "-b", "main")
            git(self.root, "add", "-A")
            git(self.root, "commit", "-q", "-m", "init")

    @property
    def problem(self) -> F.Problem:
        return F.load_problem_dir(self.root, SANDBOX)

    def shot_path(self, login: str, shot_id: str, problem: str = SANDBOX) -> Path:
        return self.root / "problems" / problem / "shots" / login / shot_id

    def add_shot(self, login: str, shot_id: str, marks: list, claimed: str | None = None,
                 commit: bool = False, problem: str = SANDBOX, **md) -> Path:
        d = self.shot_path(login, shot_id, problem)
        d.mkdir(parents=True)
        (d / "certificate.json").write_text(cert(marks), encoding="utf-8")
        score = str(max(marks) - min(marks)) if claimed is None else claimed
        (d / "SHOT.md").write_text(shot_md(login, score, **md), encoding="utf-8")
        if commit:
            git(self.root, "add", "-A")
            git(self.root, "commit", "-q", "-m", f"shot {login}/{shot_id}")
        return d

    def cleanup(self) -> None:
        self._tmp.cleanup()


def write_shot_dir(base: Path, login: str, shot_id: str, marks: list, claimed: str | None = None, **md) -> Path:
    d = base / "shots" / login / shot_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "certificate.json").write_text(cert(marks), encoding="utf-8")
    score = str(max(marks) - min(marks)) if claimed is None else claimed
    (d / "SHOT.md").write_text(shot_md(login, score, **md), encoding="utf-8")
    return d
