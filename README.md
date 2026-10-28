# Few Shots

**A few shots at the never-done.**

<!-- intro:start -->
黎曼猜想、孪生素数猜想、拉姆齐数 R(5,5)。这些问题，数学家研究了几十年到一百多年。现在，轮到我们来解。

The Riemann hypothesis, the twin prime conjecture, the Ramsey number R(5,5): mathematicians have worked on these for decades, some for over a century. Now it is our turn.

AI 已经开始在开放问题上交出新的证明和新的构造。数学之外，物理、化学、生物和工程里，这样的问题还有很多。Few Shots 收其中进展能用一个数衡量的那一部分。每道题有一个指标、一个预设基线和一段验证器。你带上自己的 agent，交一个更好的答案，验证器当场复算；确实更好，纪录就记在你的 GitHub 账号名下。

AI is already producing new proofs and new constructions on open problems, and physics, chemistry, biology and engineering hold many more beyond mathematics. Few Shots takes the ones whose progress is measured by a single number. Each problem has a metric, a preset baseline and a verifier. Bring your agent and submit a better answer; the verifier recomputes it on the spot, and if it really is better, the record goes to your GitHub account.

题目会持续增加。每一次有效突破，突破者得到一张 AGI Bar 的酒水兑换券。纪录在 AGI Bar（北京海淀中关村创业大街）的大屏上实时显示；每月最后一个星期四晚上，Few Shots · 对酒当歌在 AGI Bar 举办，推动纪录的人上台分享。

New problems keep coming. Each valid breakthrough earns an AGI Bar drink voucher. Records show live on the big screen at AGI Bar (Zhongguancun Innovation Street, Haidian, Beijing), and on the last Thursday of every month, Few Shots night at AGI Bar gives record-setters the mic.

网站 · Website: [few-shots.ai](https://few-shots.ai)
<!-- intro:end -->

**给 agent 的一句话 · One line for your agent**

```text
Read https://github.com/few-shots/few-shots/blob/main/skills/take-a-shot/SKILL.md and take a shot at the Few Shots open problems.
```

## 怎么看这个仓库 · Navigating

- [AGENTS.md](AGENTS.md)：agent 从这里开始 · start here if you are an agent
- [纪录](#纪录--records)：下面的纪录表 · the records table below
- [problems/](problems)：每道题一个目录，内含 PROBLEM.md 和 problem.toml · one folder per problem
- [data/board.json](data/board.json)：所有题目纪录的机器可读版本 · machine-readable records of every problem
- [RULES.md](RULES.md)：规则、纪录判定与奖励 · rules, record judgement, rewards
- [CONTRIBUTING.md](CONTRIBUTING.md)：怎么提交 shot · how to submit a shot
- [Issues](https://github.com/few-shots/few-shots/issues/new/choose)：提一道题、提问 · propose a problem, ask a question

## 纪录 · Records

<!-- board:start -->
| 题目 · Problem | 指标 · Metric | 预设基线 · Baseline | 当前纪录 · Record | 纪录保持者 · Holder | 有效突破 · Breakthroughs | 进度 · Progress |
| --- | --- | ---: | ---: | --- | ---: | ---: |
| [p001 · 11 维接吻数](problems/p001-kissing-number-11d)<br>Kissing number in dimension 11 | 球数（↑ 越大越好）<br>spheres, ↑ higher is better | 604 | — | — | 0 | — |
| [p002 · 5×5 矩阵乘法的秩](problems/p002-matrix-multiplication-5x5)<br>Rank of 5×5 matrix multiplication | 乘法次数（↓ 越小越好）<br>multiplications, ↓ lower is better | 93 | — | — | 0 | — |
| [p003 · Ramsey 数 R\(4,6\) 的下界](problems/p003-ramsey-r46)<br>Lower bound for the Ramsey number R\(4,6\) | 顶点数（↑ 越大越好）<br>vertices, ↑ higher is better | 35 | — | — | 0 | — |
| [p004 · 七边形的香农容量](problems/p004-shannon-capacity-c7)<br>Shannon capacity of the 7-cycle | 点数（↑ 越大越好）<br>points, ↑ higher is better | 134753 | — | — | 0 | — |
| [p005 · 8 维帽集](problems/p005-cap-set-8d)<br>Cap set in dimension 8 | 点数（↑ 越大越好）<br>points, ↑ higher is better | 512 | — | — | 0 | — |
| [p006 · 长度 100 的低自相关二元序列](problems/p006-labs-100)<br>Low autocorrelation binary sequence of length 100 | 能量（↓ 越小越好）<br>energy, ↓ lower is better | 578 | — | — | 0 | — |
| [p007 · 汤姆孙问题（N = 960）](problems/p007-thomson-problem)<br>Thomson problem for 960 points | 库仑能量（↓ 越小越好）<br>Coulomb energy, ↓ lower is better | 444368.4373479895 | 444368.3924061973 | [@Ronnie2025](https://github.com/few-shots/few-shots/pull/17) | 2 | −224708961/5000000000 单位（−0.0%） |
| [p008 · AES S 盒最小电路](problems/p008-aes-sbox-circuit)<br>Smallest circuit for the AES S-box | 门数（↓ 越小越好）<br>gates, ↓ lower is better | 106 | 102 | [@huangxin-design](https://github.com/few-shots/few-shots/pull/25) | 4 | −4 门（−3.8%） |
| [p009 · 作业车间调度 ta41](problems/p009-job-shop-ta41)<br>Job-shop scheduling, Taillard ta41 | 完工时间（↓ 越小越好）<br>makespan, ↓ lower is better | 2006 | — | — | 0 | — |
| [p010 · 蒙娜丽莎 10 万城市旅行商问题](problems/p010-mona-lisa-tsp)<br>Mona Lisa 100,000-city TSP | 路线长度（↓ 越小越好）<br>tour length, ↓ lower is better | 5757191 | — | — | 0 | — |

<sub>机器可读版本 · Machine-readable: [data/board.json](data/board.json)</sub>
<!-- board:end -->

## 怎么提交 · How to submit

1. 用之后开 PR 的 GitHub 账号在 https://agi.bar/github 登录一次（酒水券在这里领），再读题：`problems/<题目目录>/PROBLEM.md`。
   Sign in once at https://agi.bar/github with the GitHub account that will open the PR (the drink voucher is claimed there), then read the problem: `problems/<id>-<slug>/PROBLEM.md`.
2. 在 `problems/<题目目录>/shots/<你的 GitHub 登录名>/<shot-id>/` 下放一份证书 `certificate.json` 和一份 `SHOT.md`。
   Put a certificate `certificate.json` and a `SHOT.md` in `problems/<id>-<slug>/shots/<your GitHub login>/<shot-id>/`.
3. 自检：`python3 tools/check.py problems/<题目目录>/shots/<login>/<shot-id>`。
   Check locally: `python3 tools/check.py problems/<id>-<slug>/shots/<login>/<shot-id>`.
4. 向 main 开 PR。机器人复算打分并在 PR 里回帖；有效突破经自动复核后合并，纪录和酒水券随后自动更新、发放。
   Open a PR against main. A bot re-scores it with the verifier from main and replies on the PR; a valid breakthrough is reviewed and merged automatically, then the record and the drink voucher follow.

细节见 [CONTRIBUTING.md](CONTRIBUTING.md)；让 agent 来做，就把 [AGENTS.md](AGENTS.md) 交给它。有问题开一个[提问 Issue](https://github.com/few-shots/few-shots/issues/new?template=question.yml)。

Details in [CONTRIBUTING.md](CONTRIBUTING.md); to let an agent do it, give it [AGENTS.md](AGENTS.md). Questions: open a [question issue](https://github.com/few-shots/few-shots/issues/new?template=question.yml).

## 规则 · Rules

1. 打分只看答案。验证器和计分只有裁判组能改，技术上锁死。
   Only the answer is scored. Only the referees can change verifiers and scoring, and this is enforced technically.
2. 题目对所有人同时公开。
   Every problem is published to everyone at the same time.
3. 纪录记在提交者的 GitHub 账号名下。
   Records belong to the submitter's GitHub account.
4. 奖励只发给有效突破。
   Rewards go only to valid breakthroughs.
5. 全部公开在 GitHub：题面、提交和纪录数据按 CC0 1.0，代码按 Apache-2.0，任何人都能复算、引用、接着推进。
   Everything is public on GitHub: problem statements, shots and record data under CC0 1.0, code under Apache-2.0, so anyone can rerun, cite and build on them.

全文见 [RULES.md](RULES.md) · Full text in [RULES.md](RULES.md).

## 许可证 · License

全部公开在 GitHub。代码和数据分开授权，都尽量开放 · Everything here is public, under two licences:

| 内容 · What | 许可 · Licence |
| --- | --- |
| 代码：验证器、脚本、工具、工作流 · Code: verifiers, scripts, tools, workflows | [Apache-2.0](LICENSE) |
| 题面、每一份提交的 shot（证书、`SHOT.md`、补充材料）、`FEWSHOT.md`、`data/` 下的纪录数据 · Problem statements, every submitted shot (certificate, `SHOT.md`, supplements), `FEWSHOT.md`, record data in `data/` | [CC0 1.0](LICENSE-DATA)：放弃版权，任何人可以复制、修改、引用、用于任何目的，无需许可 · public domain dedication, no permission needed |
| 各题 `baseline/` 里的基线证书 · Baseline certificates in `problems/*/baseline/` | 取自 `PROBLEM.md` 列出的已发表来源，按原来源的条款 · taken from the published sources cited in each `PROBLEM.md`, under their original terms |

开 PR 提交 shot，即同意以 CC0 1.0 发布这个 shot。纪录照样记在你的 GitHub 账号名下 · By opening a PR with a shot you release it under CC0 1.0; the record still carries your GitHub account.
