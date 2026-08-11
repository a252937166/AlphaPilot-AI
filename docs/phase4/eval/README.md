# P4.2a 离线事件抽取评测区

本目录只保存 P4.2a 的离线试跑、盲标样本和评测证据。它不是生产数据目录，不得把任何文件
回写 `news_items`，也不得从这里触发推荐、提案、委托或交易。

## 当前门控

- 盲标基线合同：`config/p4_event_extract_eval_v1.yaml`；评测设计修订：
  `config/p4_event_evaluation_v1_1.yaml`。v1.1 只加严评测设计，不改写 v1 taxonomy、
  JSON Schema、60 条冻结样本或验收阈值。
- 生产数据库只能以 SQLite `mode=ro` + `PRAGMA query_only=ON` 打开。
- held-out 生效模型由冻结 outcome 选择为 `qwen3.7-flash`；运行时仍须与 v1.7 合同、
  v1.6 evaluation design 和 freeze receipt 逐哈希对拍。密钥、Authorization header、
  原始异常响应不得进入本目录。
- 现库存全量试跑只覆盖冻结快照 `news_items id<=423`。
- 60 条现库存是 dev 集，prompt 迭代只能看这一组；40 条测试集必须在
  2026-08-06 00:10 CST 后，从 8/4–8/5 全批次中由最终冻结模型预测为
  `materiality>=2` 的正类池确定性抽取。
- 40 条 owner 盲样本不得包含模型预测、预测状态、正类池信息、选择理由、选择排名或其他
  抽样依据。待标盲样本、owner 完成文件、combined100 和 completion manifest 使用不同的
  create-only 路径。
- 巨潮样本的公告正文只允许从其落库的 `https://static.cninfo.com.cn/` PDF 在 eval 路径临时
  获取并提取；记录 PDF/正文哈希，不保存 PDF，不回写生产库。获取或提取失败即阻断，不把
  标题伪装成正文，也不静默换样本。
- 最终 prompt 必须先对 dev60 生成 create-only 最终预测与 manifest，再冻结 active contract
  回执；held-out 推理和评测分别只有一次 started 机会，失败后不得复用该测试集。
- 候选全集大于单批 2,000 条时，只能在同一个 one-shot started 状态下按冻结 ID 顺序做
  确定性连续分批；每条最多一次调用、零重试。任一中间批未闭合即整轮作废，不能续跑。

## Owner 标注规则

Owner 仅填写每条记录的 `gold`：

- `symbols`：去重、升序的 6 位 A 股代码数组；无法唯一映射时为 `[]`；
- `event_type`：taxonomy v1 九类之一；
- `direction`：`-1 / 0 / 1`；
- `materiality`：`0 / 1 / 2 / 3`；
- `evidence_span`：必须逐字来自该条 `original_text` 的连续片段；
- `notes`：可选人工说明。

不得查看或复制离线模型预测、正类池状态或抽样排名来填 gold。Owner 的 dev60 与 heldout40
完成文件会先分别对拍冻结盲样本，再由 `combine-owner` 生成 1–60 / 61–100 的最终文件及
completion manifest。评测器会重算固定 ID、原文哈希、输入哈希、盲性与计数：

- `materiality>=2` precision 只在 heldout40 判门，阈值仍为 `>=0.80`；
- symbol exact-set 在 all100 判门，阈值仍为 `>=0.95`，并分报 dev/test；
- prompt 迭代只允许依据 dev60；heldout40 只评一次，不达标必须登记新设计并换测试集。

heldout40 使用 AI 起草 + owner 人工裁定时，起草 AI 看到的必须仍是同一份盲文件，且其
`gold.notes` 固定为 `null`。先生成裁定页：

```bash
PYTHONPATH=. .venv/bin/python scripts/build_p4_2a_adjudication_ui.py \
  --evaluation-design config/p4_event_evaluation_v1_6.yaml \
  --sample docs/phase4/eval/P4.2a-gold-heldout40-blind-sample-v1.6.jsonl \
  --draft docs/phase4/eval/P4.2a-gold-heldout40-ai-draft-v1.6.jsonl \
  --output docs/phase4/eval/P4.2a-gold-heldout40-adjudication-v1.6.html
```

owner 导出的 `.adjudicated.jsonl` 必须保存在 `docs/phase4/eval/`。`combine-owner` 同时接收
它和原 AI draft；最终 evaluator 也必须显式接收二者，重新对拍逐条 audit 与 canonical gold：

```bash
PYTHONPATH=. .venv/bin/python scripts/build_p4_2a_gold_sample.py \
  --mode combine-owner \
  --evaluation-design config/p4_event_evaluation_v1_6.yaml \
  --dev-owner-export docs/phase4/eval/P4.2a-gold-inventory60-v1.labels-ai-drafted.jsonl \
  --heldout-owner-export docs/phase4/eval/P4.2a-gold-heldout40-blind-sample-v1.6.adjudicated.jsonl \
  --heldout-ai-draft docs/phase4/eval/P4.2a-gold-heldout40-ai-draft-v1.6.jsonl

PYTHONPATH=. .venv/bin/python scripts/evaluate_p4_2a_gold.py \
  --scope heldout-final-v1.6 \
  --evaluation-design config/p4_event_evaluation_v1_6.yaml \
  --heldout-adjudicated-export docs/phase4/eval/P4.2a-gold-heldout40-blind-sample-v1.6.adjudicated.jsonl \
  --heldout-ai-draft docs/phase4/eval/P4.2a-gold-heldout40-ai-draft-v1.6.jsonl \
  --output docs/phase4/eval/reports/v1.6/P4.2a-heldout-final.json
```

## 未完成条件

截至 2026-08-03，只允许冻结合同、执行现库存离线试跑、固定并标注 dev60，以及准备未来
运行工具。严禁读取或生成 8/4–8/5 heldout 产物。Owner 未完成两组标签、one-shot 评测未执行、
两项硬指标未通过前，P4.2a 不标 done，P4.2b 继续锁定。

## 2026-08-03 已冻结证据

- `P4.2a-offline-extract-qwen3.6-flash-v1.jsonl`：423 条，406 成功、17 失败，SHA-256
  `b1baca41a2d4cbbfa62ec921bc77ea760f2b80f7a075e1625c68fb74acac06a4`。失败均保留且未
  重试；16 条为严格后置校验拒绝，1 条为 HTTP 400。
- `P4.2a-offline-trial-v1.report.json`：终态 create-only 汇总，SHA-256
  `794daa7b6f2152194575e3bf764139fdd046eb4aa31f4d310b57f92446b37e07`。
- `P4.2a-offline-trial-v1.report.pre-audit-fix.json`：首次终态汇总尝试，SHA-256
  `9ced3ac77187af30cf9aae94cb49398ef3da5e6b9397301520414e9fdd1cad89`。该文件把
  “部分成功”误显示为 `table_check=0/1`，仅保留为修正链证据；`ae39689` 修复展示逻辑后
  重新以 create-only 方式生成上面的权威终态报告，底层 423 条 JSONL 未改。
- `P4.2a-gold-inventory60-v1.jsonl`：60 条 owner 待标注样本，SHA-256
  `81b3c0b27cd344fe4c2a735261e501dd2f60a0927c14b2c37e5b2a4879b4a2ba`。分层为
  `cninfo/bound/body=24`、同花顺 bound/null 各 9、新浪 bound/null 各 9；24 条巨潮样本均
  来自官方公告 PDF 正文，正文最长按合同截至 14,000 字符并保留全文哈希。
- 离线试跑成功记录中 `materiality>=2` 为 81/406（20.0%）；冻结 dev60 与失败集交集为
  `news_item_id=190`。最终报告必须同时披露这两项，并将 active final-prompt 的失败另列，
  不得混写或隐藏。
- `post_validation_failed` 的“违规字段 + 约束类型、无原始 payload”结构化错误码属于
  P4.2b 的生产接线硬门；P4.1 验收前不得提前建表、注册 job 或改 scheduler。
