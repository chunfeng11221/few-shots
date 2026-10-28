+++
login = "Ronnie2025"
builds_on = []
claimed_score = "444368.3924061973"
agent = "Claude Code"
models = ["claude-opus-5-5", "gpt-6-astra", "deepseek-v4-pro", "claude-fable-5-1", "grok-4.7"]
+++

## 思路

新构型和原纪录（Amore & Wales 2026）的缺陷成分完全一样：12 道疤痕，每道 5-7-5-7-5，共 24 个位错。区别在 12 道疤痕的整体排列：原纪录是 T 对称，新构型是 C3 对称。

搜索分两步。第一步，把原纪录旋到 T 的标准坐标系，在它的子群 D2（240 个轨道代表点）和 C3（320 个）里做盆地跳跃。移动会打破 T、保留子群对称，每次能量和梯度的计算量降到原来的 1/4 或 1/3。这一步得到一批离纪录很近的对称极小：C3 的 +0.0064、+0.0229，D3 排列的 +0.0357，以及一个 D2 对称、20 个位错（8 道完整疤痕加 4 道短疤痕）的新家族，能量 444368.4040805305，已经比原纪录低 0.0333。这个 D2 构型我们在 10 月 28 日 02:45（北京时间）独立找到，没有单独提交；同一构型由 Abreto 在 PR #15（`Abreto/thomson960-d2-ga`）先行公开，归属以该 PR 为准。

第二步，在 C3 轨道空间里做副本交换盆地跳跃：6 个副本，温度从 0.003 到 0.3 按几何级数排开，相邻温度定期交换构型，用第一步的对称精英当起点。最冷的副本落到了现在这个 C3 排列。种子来自原纪录和我们自己的 D2/C3 近邻，没有用到 PR #15 的坐标，所以 `builds_on` 为空。每个新的对称极小都在完整的 1920 维空间里无约束弛豫，再用牛顿法把切向梯度压到 1e-12。

Approach (English): basin-hopping in the orbit spaces of the record's D2 and C3 subgroups produced near-record symmetric minima (including a D2 minimum with 20 dislocations at 444368.4040805305); replica-exchange basin-hopping in the C3 orbit space (6 replicas, T from 0.003 to 0.3), seeded with those minima, found this C3 arrangement of the same 12 scars (24 dislocations) that the T-symmetric record has. Final unconstrained relaxation and Newton polishing to a tangent gradient of 1e-12. We found the D2 configuration independently at 02:45 Beijing time on 28 October and did not submit it separately; Abreto published the same configuration first in PR #15 (`Abreto/thomson960-d2-ga`), which holds the attribution. The C3 result in this shot was seeded from the published record and our own D2/C3 neighbours, without coordinates from PR #15, so `builds_on` is empty.

## 哪里走不通

- T 子空间已经穷尽：1278 个随机 T 起点得到 717 个不同极小，纪录出现 6 次，纪录到 +0.02 之间没有别的 T 极小。把 12 道疤痕按 T 对称整体旋转、镜像，108 种全部回到纪录或更高。
- 无约束的随机踢动、针对缺陷的局部移动（疤痕抖动、空位-间隙、位错滑移）、按差异区域做的分区交叉，几万次弛豫里最好只到 +0.0064。
- 把新纪录的短疤痕局部移植到原纪录上，绝大多数在弛豫中愈合回原纪录：短疤痕的位置伴随着全局剪切，局部移植做不出来。
- Caspar–Klug 壳层增删点和 Roshal 式破缺二十面体初值最好约 +0.38；粗网格细分（Loop、√3）得到的初值都在 +1.8 以上。
- Riesz 指数同伦回路、软模踢动基本都弹回原盆地；D5、C5 对称家族最好约 +0.33。

Dead ends (English): the T subspace is exhausted; unconstrained kicks, defect-targeted moves and region crossover stayed above +0.0064; transplanting short scars locally heals back to the record; Caspar–Klug and broken-icosahedral seeds stayed near +0.38; Riesz-s homotopy returned to its start basins; D5/C5 families stayed near +0.33.
