# AlphaPilot P3.3-S6 独立 AI 架构复核报告

- 审核时间：`2026-08-01T03:29:35-04:00`
- 最终裁定：**APPROVE_AS_INDEPENDENT_AI_REVIEWER**
- 审核 JSON：`AlphaPilot-P3.3-S6-Claude-Code-independent-ai-review-20260801.json`
  （SHA-256 `19466729872aedef21d6f530c0ac0bd7866b521910b640eb63901210f3c6b07f`，附 `.sha256` sidecar）
- 唯一审查输入：`AlphaPilot-P3.3-S6-pairing-v3-remediation-review-v4-20260801.zip`（v4 包；未引用 v1/v2/v3 包、旧候选或旧审核结论）

## 0. AI 身份声明（治理变更下的如实登记）

```
reviewer_type    = "ai"
reviewer_role    = "independent_ai_architect_claude_code"
reviewer_product = "Claude Code"
reviewer_model   = "claude-fable-5"
```

- 本审核依据项目负责人的明确治理决定执行：原"独立人工架构师复核"改由 Claude Code 执行独立 AI 架构复核。
- 本审核**不是**人工签名，未填写候选的 `approved / reviewer_role / reviewed_at`，未使用
  `independent_human_approved` 等人工语义状态，未利用现行校验器对角色字符串检查的语义空隙。
- 现行冻结合同与仓库校验器仍为 human-only；本证明在 release gate 完成合同修订
  （`requires_release_gate_amendment=true`）之前**不能**用于解锁 S6。

**独立性披露**：同一 Claude Code 会话于本日早些时候修复过仓库门/校验器代码的缺陷
（machine-validation 哈希锚定、输出覆盖防护、canonical 严格比较）。为使本复核独立于该代码，
以下全部 P0 结论均由本次复核专门编写的 Python 标准库工具重新推导（解压、逐层校验和、GB18030
表格解析、canonical 哈希均为独立实现），未调用仓库校验器。

## 1. 实际执行的校验

1. 外层：`.sha256` sidecar 与 ZIP 实算 SHA-256 一致；ZIP CRC 全通过（319 entries）；
   无绝对路径 / `..`、无重复路径、无大小写冲突、无符号链接；280 文件 / 39 目录。
2. 全新临时目录解压（未复用任何既有解压产物），解压后 280 文件、0 符号链接。
3. 顶层 + 6 组嵌套 `SHA256SUMS` 独立重算（见 §2），并做全局覆盖检查：
   除根清单自身外全部 279 个文件均被根清单覆盖，无游离文件（uncovered=0）、无幽灵条目（phantom=0）。
4. P0-1：入口页请求元数据、入口 body 链接唯一性（自写 HTML 解析器）、GB18030 原表独立解析
   （表头动态定位，非硬编码列序）、目标行全表唯一性、官方 Ex-Price 字符串、倍率复算、
   证据工件哈希绑定、HALF_UP 依赖检查。
5. P0-2：candidate 文件哈希、canonical unsigned 哈希（独立实现的规范化 + 紧凑排序 dumps）、
   machine-validation 哈希、presign 报告哈希与包内 sidecar、时间先后、candidate/PIT manifest/
   15 business keys 绑定（`business_keys_sha256` 独立重算比对）、DB 只读证明、15 精确键差异。
6. P0-3：三项计数器、四类安全布尔、四个 scope 的结构化逐文件 baseline/current SHA 比对
   （非仅阅读交接说明）、`operational_safety_differences / safety_differences /
   contract_difference_keys` 全空。
7. 候选完整性：verdict 计数、summary 一致性、business keys 与包内冻结 final trial 键集全等、
   候选三顶层字段仍未签、仓库 production trust anchor 仍未设置。

## 2. 各层 SHA256SUMS 独立核验结果

| 清单 | 条目 | missing | mismatch | duplicate | extra/uncovered |
|---|---:|---:|---:|---:|---:|
| `SHA256SUMS`（根，全局覆盖） | 279 | 0 | 0 | 0 | 0 |
| `candidate/raw-source/SHA256SUMS` | 205 | 0 | 0 | 0 | — |
| `…/complete-unfiltered-announcement-inventory/SHA256SUMS` | 64 | 0 | 0 | 0 | — |
| `…/reference-evidence/001260/SHA256SUMS` | 8 | 0 | 0 | 0 | — |
| `…/rounding-evidence/600782/SHA256SUMS` | 17 | 0 | 0 | 0 | — |
| `…/git-chain/SHA256SUMS` | 23 | 0 | 0 | 0 | — |
| `…/price-tick-rules/SHA256SUMS` | 29 | 0 | 0 | 0 | — |

（对照 2026-07-31 拒签结论中的 `raw-source 85 通过/25 缺失`、`price-tick-rules 18 通过/11 缺失`：
v4 包已实证闭合。）

## 3. P0-1 独立结论：001260 官方除息参考价 — 通过

- 入口请求：`GET https://www.szse.cn/market/periodical/month/t20260605_620906.html`（无查询参数），
  冻结 body 哈希在清单内闭合。
- 入口 body 中锚文本"分红派息配股"指向
  `docs.static.szse.cn/www/market/periodical/month/W020260605534753848014.html` 的链接
  **恰好 1 条**；全页指向该路径的链接总数也为 1（唯一绑定成立；body 内锚 scheme 为 http，
  记录抓取 URL 为 https，host+path 一致，见 §7 观察项）。
- GB18030 原表独立解析：14 列表头动态定位；**全表**代码为 `001260` 的行仅 1 行：
  坤泰股份 / DPS `0.215` / 登记日 `2026/05/26` / 除息日 `2026/05/27` /
  **Ex-Price `20.290`（官方原始记录直接给出）** / Pre-Closing `20.500`。
- 候选事件：`official_reference_price_ratio_v1`、
  `rounding_provenance=exchange_published_reference_price_local_rounding_none`、
  倍率 `1.0103499260719566` 与独立复算 `20.50 / 20.29` **逐位一致**。
- 参考价证据工件哈希与清单一致，`candidate/artifacts/` 副本与 `raw-source` 原件**字节一致**。
- 001260 未使用 `cash_share_price_grid_v1`，无任何 `ROUND_HALF_UP` 依赖；600782 同为官方参考价路径。

## 4. P0-2 独立结论：候选后只读门 — 通过

- candidate 文件 SHA-256 = `094c21eb7921a0c57c35ba97247a7b69faeada0421ff1929ca5d4701c388fd0c` ✔
- candidate canonical unsigned SHA-256（独立实现复算）=
  `45358d1508e7ac6e71a7df25990aba1a908fc7c5108dd4253cc1e05698d520ae` ✔
- machine-validation SHA-256 = `57f70704905ba84b371fc7f3432ce28967e9e67a5ae9b6517fc188da6109ef3d` ✔
- presign 报告 SHA-256 = `1fee14d59bff82f951a620f67324540c6ad954baeef43c90cbd3c6385de12d89`，
  与包内 sidecar 一致 ✔
- 时间先后：machine validation `2026-08-01T14:08:00+08:00`（=06:08Z）→ presign 报告
  `2026-08-01T06:23:06.691565+00:00`，**晚 15 分钟**，顺序正确 ✔
- 绑定：presign 报告内 candidate 文件/canonical/machine-validation 三哈希与本次实算全等；
  PIT manifest = `fb9c888e…cbfb0bd` 三方一致（candidate、presign、包内冻结 preflight）；
  `business_key_count=15`，`business_keys_sha256` 独立重算一致 ✔
- DB：`open_mode=ro`、`query_only=true`、`data_version 2→2`、running JobRun 0/0 ✔
- 固定 15 精确键：`difference_count=0`、`difference_sections=[]`、`sample_count=15` ✔

## 5. P0-3 独立结论：安全不变量 — 通过

- `test_window_access_count=0 / factor_diff_count=0 / weight_diff_count=0` ✔
- `research=true / paper_auto=false / live=false / trading_safety_gates_unchanged=true` ✔
- 结构化逐文件核验（非交接说明）：factor 7 文件、weight 5 文件、trading_safety_gate 9 文件、
  test_window_guard 4 文件，全部 `baseline_sha256 == current_sha256`、状态
  `unchanged`（含 `config/factor_weights_v3.yaml` 的 `absent_unchanged`：基线与当前均不存在，
  两侧一致缺席，不构成差异）✔
- `operational_safety_differences=[] / safety_differences=[] / contract_difference_keys=[]` ✔

## 6. 候选完整性 — 通过

- 固定样本 15 个；verdict 实数 `numeric_match=5 / formula_match=8 / expected_unavailable=2`，
  与 summary 一致 ✔
- business keys 与包内冻结 final trial（SHA `40f310a4…e424099`）键集**全等**——无删样、换样、重抽 ✔
- 候选保持 `approved=false / reviewer_role="pending" / reviewed_at=null` ✔
- 仓库 `FROZEN_PAIRING_V3_SIGNED_EVIDENCE_SHA256 = None`（production trust anchor 仍未设置）✔

## 7. 非阻断观察项（如实披露）

1. `reference-evidence/001260/` 下的 `*.headers.json` 为重建脚本本地合成的元数据
   （断言 status 200、固定 etag），标注为 sanitizer 产物，与真实捕获头在形式上不可区分。
   本报告 P0-1 结论**仅依据哈希锚定的 body 独立解析**，不依赖这些头记录；建议未来重建时
   对合成头记录做诚实标注。
2. 入口 body 内锚链接为 `http://` scheme，记录抓取 URL 为 `https://`（host+path 一致）。
3. 现行仓库校验器对 reviewer_role 的字符串检查在机械上可接受 AI 命名角色；本审核未利用该
   空隙，也未修改 human-only 校验器。

## 8. 未执行的动作（按硬约束保持不变）

- 未修改原 ZIP、unsigned candidate、生产数据库、因子、权重、交易配置。
- **未写入 production trust anchor**（仍为 `None`）。
- **未标 `S6 done`，未启动 S7**，test window 继续封存，未访问。
- 未执行任何交易操作；未提交 Git；未修改现行 human-only 校验器。

## 9. 关键哈希汇总

| 对象 | SHA-256 |
|---|---|
| 输入 ZIP | `2041448a87ef8548b0fa9d5aedf7e33aad4b549b8cc58a2705edc0fffe8bce01` |
| candidate 文件 | `094c21eb7921a0c57c35ba97247a7b69faeada0421ff1929ca5d4701c388fd0c` |
| candidate canonical unsigned | `45358d1508e7ac6e71a7df25990aba1a908fc7c5108dd4253cc1e05698d520ae` |
| machine validation | `57f70704905ba84b371fc7f3432ce28967e9e67a5ae9b6517fc188da6109ef3d` |
| presign 报告 | `1fee14d59bff82f951a620f67324540c6ad954baeef43c90cbd3c6385de12d89` |
| 冻结 PIT manifest | `fb9c888e7a10f7b1ef28e7a447b0e2b53df739f6acdbedb1ba95d1d41cbfb0bd` |
| 冻结 final trial | `40f310a4c94e550cac33616fbe2d8ffc189cb48cd259a2913fe3379bae424099` |
| 冻结 preflight | `2064586a9321c7ab8b321cd82ec5c3edcd5449984dc3a2822556a3f65eb620e3` |
| 本审核 JSON | `19466729872aedef21d6f530c0ac0bd7866b521910b640eb63901210f3c6b07f` |
