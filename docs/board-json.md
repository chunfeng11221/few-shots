# data/board.json 格式

所有正式题目的当前纪录与纪录链，README 的纪录表和 AGI Bar 大屏都读这份数据。由记分牌工作流（`scripts/build_board.py`）生成，不要手改。

地址：`https://raw.githubusercontent.com/few-shots/few-shots/main/data/board.json`

## 顶层

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `updated_at` | string | 内容最后一次变化的时间，UTC，`YYYY-MM-DDTHH:MM:SSZ`。内容没变时保持原值。 |
| `problems` | array | 题目列表，按题目目录名排序；沙盒题（`sandbox = true`）不在里面。没有题目时为空数组。 |

## `problems[]`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 题号，形如 `p001` |
| `slug` | string | 短名 |
| `dir` | string | 题目目录，`problems/<id>-<slug>` |
| `title_zh` / `title_en` | string | 题目名 |
| `repo` | string | 本仓库，`few-shots/few-shots` |
| `url` | string | 题目目录在 GitHub 上的地址 |
| `metric_zh` / `metric_en` | string | 指标名称 |
| `direction` | `"max"` 或 `"min"` | max 越大越好，min 越小越好 |
| `unit` | string | 进度单位（`problem.toml` 的 `progress_unit`） |
| `baseline` | string | 预设基线，精确有理数文本 |
| `epsilon` | string | 最小改进幅度 ε，精确有理数文本 |
| `epsilon_mode` | `"absolute"` 或 `"relative"` | relative 时，有效突破至少要改进 \|当前纪录\| × ε |
| `progress_transform` | `"linear"` 或 `"log2"` | 进度的换算方式 |
| `current` | object 或 `null` | 当前纪录，即 `records` 的最后一条（不含 `shot` 字段）；还没有纪录时为 `null` |
| `records` | array | 纪录链，按合并进 main 的先后排列 |
| `progress` | string 或 `null` | 当前纪录相对预设基线的进度，单位为 `unit`，与机器人回帖中的"相对基线的进度"一致；没有纪录时为 `null` |
| `progress_pct` | number 或 `null` | 进度百分比，两位小数；没有纪录时为 `0`；无法定义时为 `null`（见下） |

## `current` 与 `records[]`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `score` | string | 成绩，精确有理数文本（SHOT.md 的 `claimed_score`，打分时已核对与验证器结果完全相等） |
| `login` | string | 纪录保持者的 GitHub 登录名，即 `shots/<login>/` 目录名，打分时校验为 PR 作者 |
| `agent` | string | SHOT.md 的 `agent` 字段（用的 agent 或工具），没写就是空字符串。第三方文本：只当纯文本显示 |
| `models` | string[] | SHOT.md 的 `models` 字段，没写就是空数组。第三方文本：只当纯文本显示，不要当 HTML 或 Markdown 渲染 |
| `at` | string | 合并进 main 的时间，UTC（关联 PR 的 `merged_at`；没有 PR 时取提交时间） |
| `verified_at` | string \| null | 验证器首次判定这个 PR 为有效突破的时间（服务器时间，UTC，打分机器人回帖里的验证证书）。纪录的先后和显示的时间以它为准；读不到时为 null |
| `pr` | integer | 对应的已合并 PR 号 |
| `shot` | string | 仅 `records[]` 有：shot-id，即 `shots/<login>/<shot-id>/` |

## 精确数

成绩、基线和 ε 一律是精确有理数文本：整数 `"42"`、最简分数 `"355/113"`、负数 `"-7/3"`。比较和计算请用大整数有理数（Python 用 `fractions.Fraction`），不要先转成浮点数。

## 纪录链怎么算

1. 按 main 第一父链上的合并顺序，列出这道题 `shots/<login>/<shot-id>/SHOT.md`（只认普通文件）。
2. 解析每个 SHOT.md 的 front matter；解析失败、或 `login` 与目录不一致的跳过。
3. 核对打分机器人的判定：引入该 SHOT.md 的提交对应一个已合并 PR，它的 head 提交上最新的 `few-shots/verdict` 由 `github-actions[bot]` 写成"有效突破"，并且 head 上的 SHOT.md 与 main 上的完全相同。核对不通过的跳过。
4. 从预设基线出发逐个判定（`scripts/fslib.py` 的 `judge`）：达到预设基线、优于当时的纪录、并且至少改进 ε 的，成为新纪录。

跳过和不计入的 shot 会在工作流日志里告警。

## 进度怎么算

设 T 为换算函数：`linear` 时 T(x) = x，`log2` 时 T(x) = log2 x。

- `progress` = T(当前纪录) − T(预设基线)（`max` 题）或 T(预设基线) − T(当前纪录)（`min` 题）。`linear` 时是精确有理数文本，`log2` 时保留 6 位小数。
- `progress_pct` = `progress` / \|T(预设基线)\| × 100，保留两位小数，可以超过 100。T(预设基线) 为 0 时为 `null`。

## 读取失败时

某一道题本次读取失败时，这道题沿用上一次 `board.json` 里的数据（上一次也没有就不出现），并在工作流日志里告警。
