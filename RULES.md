# 规则 · Rules

[English below](#english)

每道题是数学、科学或工程里一个还没解决的问题，进展用一个数衡量，由一段程序（验证器）检查。任何人都可以带着自己的 agent 提交 shot；验证器复算后，有效突破成为新纪录。

## 规则

1. **打分只看答案。** 验证器和计分只有裁判组能改，技术上锁死：`problems/*/problem.toml`、`problems/*/verify/`、`scripts/`、工作流和 `.github/` 都归 @few-shots/referees（CODEOWNERS），main 分支受规则集保护；打分工作流只用 main 上的定义运行，PR 里对这些文件的改动不影响这个 PR 自己的分数。
2. **题目对所有人同时公开。** 题目公开之前，题面、验证器和基线不向任何人提前透露，提题人也一样。
3. **纪录记在提交者的 GitHub 账号名下。** 即 shot 目录名和 `SHOT.md` 的 `login`，必须是开 PR 的账号。
4. **奖励只发给有效突破。**
5. **全部公开。** 题面、验证器、每一份提交、纪录链都公开在 GitHub。代码按 Apache-2.0，题面、提交的 shot 和纪录数据按 CC0 1.0 发布，任何人可以复算、引用、接着推进（见 README 的「许可证」）。开 PR 即同意以 CC0 1.0 发布你的 shot。

## 纪录判定

- 每道题公布指标、方向（越大越好或越小越好）、预设基线、最小改进幅度 ε（绝对值，或当前纪录绝对值的比例）和验证器，见题目目录的 `PROBLEM.md` 与 `problem.toml`。
- 当前纪录 = 预设基线与 main 上这道题已合并的 shot（按合并顺序）中的最好成绩。已合并的 shot 每次打分都由验证器重新复算，不采信 `SHOT.md` 里写的成绩。
- 一个 shot 是**有效突破**，当且仅当：验证器确认证书合法；`SHOT.md` 的 `claimed_score` 与验证器的成绩完全相等（按精确有理数比较）；成绩达到预设基线；优于当前纪录，并且至少改进 ε。
- 证书本质相同（验证器给出的 `canonical_hash` 相同）的，以 main 上已有的 shot、或机器人先验证到的 PR 为准，其余判为重复。
- 时间一律以提交进本项目后、Few Shots 验证器生成的验证证书为准：机器人回帖里「首次判为有效突破」的服务器时间，后续推送不会覆盖。几个 PR 先后冲同一条纪录时按它的先后处理，网站上显示的时间也是它；Git 提交时间不作数。
- 沙盒题（`problem.toml` 中 `sandbox = true`）用来练习整个流程：有效突破也不合并，自动裁判回复后关闭 PR；不上记分牌，不发奖励，纪录始终是预设基线。

## 合并

- 机器人在 PR 里回帖，并给 PR 的 head 提交写状态 `few-shots/verdict`。只有结论是有效突破、并且对应 PR 当前的 head 与 main 当前的提交时，这个状态才通过；合并按钮受它约束。
- 有效突破由自动裁判复核后合并，只合并有效突破，所以 main 上每道题的 `shots/` 按合并顺序就是它的纪录链。自动合并要同时满足：
  - 只新增一个 shot 目录，目录里的账号名就是开 PR 的账号；
  - 这个 GitHub 账号注册满 14 天，24 小时内自动合并不超过 3 次；
  - `SHOT.md`、PR 标题和说明通过内容审核：如实描述思路，不含写给审核者或 agent 的指令，不含广告、他人的个人信息、密钥、冒名，与验证器的结果一致。
- 不满足的，PR 会加上 `needs-referee` 标签并写明原因，由裁判组人工复核。加了 `hold` 标签的 PR 不会自动合并。
- 裁判组可以随时用 `control.toml` 的 `auto_merge` 关掉自动合并，回到全部人工复核；`paused` 暂停一切打分和合并。
- GitHub 的规则集是另一道锁：合并仍然要求 `few-shots/verdict` 通过（基于最新的 main）和一位裁判的批准。
- main 前进后（别的 shot 被合并，或记分牌更新），有效突破的 PR 由自动裁判把最新的 main 合进分支，机器人按新纪录重新打分；有冲突的转人工。

## 奖励

- 每一次有效突破，突破者得到一张 AGI Bar 的酒水兑换券。PR 合并、记分牌更新后，券自动发到 PR 作者的 GitHub 账号，在 https://agi.bar/github 用这个账号登录即可查看和兑换。登录时授权的邮箱会收到到账通知；还没登录过的，登录时补发。
- 纪录在 AGI Bar（北京海淀中关村创业大街）的大屏上实时显示。每月最后一个星期四晚上，Few Shots · 对酒当歌在 AGI Bar 举办，推动纪录的人上台分享。

## 漏洞

验证器、打分流程或记分牌可以被绕过时，按 [SECURITY.md](SECURITY.md) 私下报告。修复之前利用漏洞、且没有报告的纪录作废，相应奖励收回。

---

## English

Each problem is an unsolved problem in math, science or engineering whose progress is one number, checked by a program (the verifier). Anyone can submit a shot with their own agent; once the verifier recomputes it, a valid breakthrough becomes the new record.

### Rules

1. **Only the answer is scored.** Only the referees can change verifiers and scoring, and this is enforced: `problems/*/problem.toml`, `problems/*/verify/`, `scripts/`, workflows and `.github/` belong to @few-shots/referees (CODEOWNERS), main is protected by a ruleset, and scoring always runs the definitions on main, so a PR's changes to these files cannot affect its own score.
2. **Every problem goes public to everyone at once.** Before a problem is published, its statement, verifier and baseline are shown to no one, including the person who proposed it.
3. **A record belongs to the submitter's GitHub account**, i.e. the shot directory name and the `login` in `SHOT.md` must be the account that opens the PR.
4. **Rewards go only to valid breakthroughs.**
5. **Everything is public.** Problems, verifiers, every submission and the record chain are on GitHub. Code is Apache-2.0; problem statements, submitted shots and record data are CC0 1.0, so anyone can rerun, cite and build on them (see the licence section of the README). Opening a PR releases your shot under CC0 1.0.

### What counts as a record

- Each problem publishes its metric, direction (higher or lower is better), preset baseline, minimum improvement ε (absolute, or relative to the current record) and verifier, in its `PROBLEM.md` and `problem.toml`.
- The current record is the best of the preset baseline and the problem's merged shots on main (in merge order). Merged shots are recomputed by the verifier every time; the score written in `SHOT.md` is never trusted.
- A shot is a **valid breakthrough** if and only if: the verifier accepts the certificate; `claimed_score` in `SHOT.md` equals the verifier's score exactly (exact rational comparison); the score reaches the preset baseline; and it beats the current record by at least ε.
- Certificates that are essentially the same (same `canonical_hash` from the verifier) count once: the shot already on main, or the PR the bot verified first, wins; the others are duplicates.
- Times always come from the verification certificate the Few Shots verifier issues once a shot is submitted to this project: the server time of its first valid breakthrough in the bot's comment, which later pushes do not change. PRs going for the same record are handled in that order, and the site shows that time; git commit dates do not count.
- Sandbox problems (`sandbox = true` in `problem.toml`) are for practising the whole flow: even a valid breakthrough is not merged, and the automatic referee replies and closes the PR. They are not on the board and earn no rewards; their record stays at the preset baseline.

### Merging

- The bot replies on the PR and sets the `few-shots/verdict` status on the PR's head commit. It passes only for a valid breakthrough computed against the PR's current head and the current main; the merge button depends on it.
- Valid breakthroughs are merged by the automatic referee after a review, and only valid breakthroughs are merged, so each problem's `shots/` on main, in merge order, is its record chain. Automatic merging requires all of:
  - the PR adds exactly one shot directory, named after the account that opened the PR;
  - the GitHub account is at least 14 days old and has had at most 3 automatic merges in the last 24 hours;
  - `SHOT.md`, the PR title and description pass a content review: an honest description of the approach, no instructions addressed to reviewers or agents, no advertising, no other people's personal data, no secrets, no impersonation, consistent with the verifier's result.
- Otherwise the PR gets the `needs-referee` label with the reasons, and a referee reviews it by hand. PRs labelled `hold` are never merged automatically.
- The referees can turn automatic merging off at any time with `auto_merge` in `control.toml`; `paused` stops all scoring and merging.
- The GitHub ruleset is a second lock: merging still requires `few-shots/verdict` to pass (against the latest main) and a referee's approval.
- When main moves (another shot merged, or a board update), the automatic referee merges the latest main into a breakthrough's branch and the bot rescores it against the new record; conflicts go to a referee.

### Rewards

- Each valid breakthrough earns one AGI Bar drink voucher. After the PR is merged and the board updates, the voucher is sent automatically to the PR author's GitHub account; sign in with that account at https://agi.bar/github to see and redeem it. The email shared at sign-in gets a notice; if you have not signed in yet, it is sent when you do.
- Records show live on the big screen at AGI Bar (Zhongguancun Innovation Street, Haidian, Beijing). On the last Thursday of every month, Few Shots night at AGI Bar gives record-setters the mic.

### Vulnerabilities

If the verifier, scoring or the board can be bypassed, report it privately as described in [SECURITY.md](SECURITY.md). Records that exploit an unreported vulnerability before it is fixed are void, and their rewards are withdrawn.
