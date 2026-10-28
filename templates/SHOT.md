+++
# 复制到 problems/<题目目录>/shots/<你的 GitHub 登录名>/<shot-id>/SHOT.md 后填写。
# Copy to problems/<problem dir>/shots/<your GitHub login>/<shot-id>/SHOT.md and fill in.
login = "your-github-login"            # 纪录记在这个 GitHub 账号名下；必须与目录名、PR 作者完全一致 · the record goes to this account; must match the directory name and the PR author
builds_on = []                         # 参考了哪些前人的 shot，形如 ["login/shot-id"]；没有就写 [] · earlier shots you used, e.g. ["login/shot-id"]; [] if none
claimed_score = "0"                    # 必须与验证器输出的 score 完全一致 · must equal the verifier's score exactly
agent = ""                             # 必填：从 AGENTS.md 的工具列表里选一个 ID，如 "claude-code"；没有就写 "other: 工具名"；没用写 "none" · required: an ID from the tool list in AGENTS.md; "other: name" if not listed; "none" if none
model = ""                             # 必填：这次突破主要出力的模型，只写一个，排行按它算；没用就写 "none" · required: the one model that did most of the work, used for the leaderboard; "none" if none
+++

## 思路

这一次的关键想法：做了什么、为什么可能更好、参考了谁的哪一步。
合并后，这一节会被清洗、摘录进这道题的 FEWSHOT.md，交给后来的人和 agent 参考。

The key idea of this shot: what you did, why it may be better, whose step you built on. You may title this section `## Approach` instead. After merging, it is cleaned up and excerpted into the problem's FEWSHOT.md for the people and agents who come next.

## 哪里走不通

试过但没成功的方向、踩过的坑、为什么放弃。死路也会一起汇编进 FEWSHOT.md。

Directions you tried that did not work, pitfalls, and why you stopped. You may title this section `## Dead ends` instead. Dead ends are compiled into FEWSHOT.md too.
