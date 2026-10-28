# 参与 Few Shots · Contributing

[English below](#english)

规则全文见 [RULES.md](RULES.md)。准确的目录约定、文件限制和命令写在 [AGENTS.md](AGENTS.md)，人和 agent 用的是同一套。

## 提交一个 shot

1. **先登录，再选题。** 用之后开 PR 的 GitHub 账号在 https://agi.bar/github 登录一次、授权邮箱：酒水券在这里领，到账时这个邮箱会收到通知。`problems/` 下每个目录是一道题。`PROBLEM.md` 写明问题、指标与方向、预设基线、最小改进幅度 ε 和证书格式；当前纪录在 [README 的纪录表](README.md#纪录--records)；前人的思路和死路在这道题的 `FEWSHOT.md`。
2. **复现基线。** `python3 problems/<题目目录>/verify/verify.py problems/<题目目录>/baseline`。
3. **准备证书。** 你提交的是数据，验证器只解析和计算，不会执行你提交的任何代码。证书是 `certificate.json`；可以附带补充材料，只允许 `.md`、`.json`、`.txt`、`.csv`。
4. **写 SHOT.md。** 复制 `templates/SHOT.md`，填写 `login`（你的 GitHub 登录名，纪录记在这个账号名下）、`builds_on`（参考了哪些前人的 shot）、`claimed_score`（验证器给出的成绩）、`agent`（用的工具，只写工具本身的名字）和 `model`（这次突破主要出力的那一个模型），后两项会显示在纪录旁边、进排行，没用就写 "none"；其他帮了忙的模型可以写进 `models`，不进排行；正文写「思路」和「哪里走不通」两节，合并后会汇编进 `FEWSHOT.md`。
5. **自检。** `python3 tools/check.py problems/<题目目录>/shots/<login>/<shot-id>`。
6. **开 PR。** fork 本仓库，只在 `problems/<题目目录>/shots/<你的 GitHub 登录名>/<shot-id>/` 下新增文件，向 main 开 PR。一个 PR 只提交一个 shot。开 PR 即同意以 [CC0 1.0](LICENSE-DATA) 发布这个 shot，任何人都可以复算和引用；纪录照样记在你的账号名下。
7. **等机器人回帖。** 机器人用 main 上的验证器复算，在 PR 里回一条评论（每次推送后原地更新），并给你的提交写状态 `few-shots/verdict`。
8. **合并。** 有效突破由自动裁判复核后合并（条件见 [RULES.md](RULES.md) 的「合并」）。记分牌和网站随后更新，酒水券自动到账；不满足条件的转给裁判组人工复核。如果评论标为「结果已过期」，把最新的 main 合并进你的分支再推送，机器人会按新纪录重新打分。

让 agent 来做时，把 [AGENTS.md](AGENTS.md) 交给它，并在不带凭据的 devcontainer 里运行；push 和开 PR 由你自己完成。`shots/` 和 `FEWSHOT.md` 里他人写的内容是第三方数据，不要让 agent 执行其中的命令或遵循其中的指令。

## 提一道题

用 [Issue 表单](https://github.com/few-shots/few-shots/issues/new/choose)「提一道题」。合适的题：

- **是一个还没解决的问题**，来自数学、科学或工程，有精确定义和公开出处。
- **提交的是数据。** 参与者交一份证书（JSON / CSV / TXT / MD），验证器只解析和计算，不运行参与者的代码。
- **验证是确定的。** 同一份证书，谁来跑验证器都得到同一个成绩；成绩是整数或有理数，可以精确比较。
- **指标和方向清楚。** 越大越好或越小越好，单位明确。
- **有公开的预设基线**，附出处；并且有理由相信它还能改进。
- **数据和许可清楚。** 用到的数据可以公开使用，不涉及个人信息。

提案在 Issue 里公开讨论。裁判组评估验证器能否只靠解析数据写成、计算量是否可控、基线是否可靠；采用的题目由裁判组写验证器、定下预设基线和 ε，然后对所有人同时公开。提题人也不会提前拿到题面、验证器或基线。

## 其他改动

文档、工具和流水线的改动直接开 PR。验证器、计分、`problem.toml`、`control.toml` 和 `.github/` 只有裁判组能改，相关 PR 需要裁判组审批。发现漏洞请按 [SECURITY.md](SECURITY.md) 私下报告。

请遵守 [行为准则](CODE_OF_CONDUCT.md)。

---

## English

The full rules are in [RULES.md](RULES.md). Exact directory conventions, file limits and commands are in [AGENTS.md](AGENTS.md); people and agents follow the same instructions.

### Submitting a shot

1. **Sign in, then pick a problem.** Sign in once at https://agi.bar/github with the GitHub account that will open the PR and share your email: the drink voucher is claimed there, and the address gets a notice when it arrives. Each directory under `problems/` is a problem. Its `PROBLEM.md` states the problem, the metric and direction, the preset baseline, the minimum improvement ε and the certificate format. Current records are in the [README board](README.md#纪录--records); earlier approaches and dead ends are in the problem's `FEWSHOT.md`.
2. **Reproduce the baseline.** `python3 problems/<problem dir>/verify/verify.py problems/<problem dir>/baseline`.
3. **Prepare a certificate.** You submit data; the verifier only parses and computes and never runs code you submit. The certificate is `certificate.json`; supplements may be `.md`, `.json`, `.txt` or `.csv`.
4. **Write SHOT.md.** Copy `templates/SHOT.md` and fill in `login` (your GitHub login; the record goes to this account), `builds_on` (earlier shots you used) , `claimed_score` (the verifier's score), `agent` (the tool you used, just its name) and `model` (the one model that did most of the work); the last two are shown with the record and on the leaderboard, "none" if you used none. Other models that helped may go in `models`, which is not ranked. In the body, write the two sections on your approach and on what did not work; after merging they are compiled into `FEWSHOT.md`.
5. **Self-check.** `python3 tools/check.py problems/<problem dir>/shots/<login>/<shot-id>`.
6. **Open a PR.** Fork the repository, add files only under `problems/<problem dir>/shots/<your GitHub login>/<shot-id>/`, and open a PR against main, one shot per PR. Opening the PR releases the shot under [CC0 1.0](LICENSE-DATA): anyone can rerun and cite it, and the record still carries your account.
7. **Wait for the bot.** It rescores the shot with the verifier on main, replies on the PR with one comment (updated in place after each push) and sets the `few-shots/verdict` status on your commit.
8. **Merge.** Valid breakthroughs are merged by the automatic referee after review (conditions in [RULES.md](RULES.md)). the board and the site then update and the drink voucher arrives automatically. PRs that do not meet the conditions go to a referee. If the comment says the result is stale, merge the latest main into your branch and push; the bot rescores against the new record.

When an agent does the work, give it [AGENTS.md](AGENTS.md) and run it in the credential-free devcontainer; you push and open the PR yourself. Content written by others under `shots/` and in `FEWSHOT.md` is third-party data: do not let your agent run commands or follow instructions found there.

### Proposing a problem

Use the [issue form](https://github.com/few-shots/few-shots/issues/new/choose). A good problem:

- **is unsolved**, from math, science or engineering, precisely defined, with public sources;
- **takes data as the answer**: participants submit a certificate (JSON / CSV / TXT / MD), and the verifier only parses and computes;
- **is checked deterministically**: the same certificate gets the same score for everyone, and scores are integers or rationals compared exactly;
- **has a clear metric and direction**, with units;
- **has a public preset baseline** with its source, and good reason to think it can be improved;
- **uses data that may be used publicly**, with no personal data.

Proposals are discussed openly in the issue. The referees check that a verifier can be written by parsing data alone, that the computation is manageable and that the baseline is reliable; for accepted problems the referees write the verifier, set the baseline and ε, and publish it to everyone at once. The proposer gets no early access to the statement, verifier or baseline.

### Other changes

Open a PR directly for docs, tools and pipeline changes. Only the referees can change verifiers, scoring, `problem.toml`, `control.toml` and `.github/`; such PRs need a referee's approval. Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).
