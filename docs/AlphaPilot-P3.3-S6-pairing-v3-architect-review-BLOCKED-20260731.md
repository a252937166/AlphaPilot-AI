# AlphaPilot P3.3-S6 pairing-v3 架构复核结论

- 审阅时间：`2026-07-31T10:18:15Z`
- 决定：**NOT APPROVED / 不签认**
- `candidate/pairing-v3-candidate.json`：**未修改**
- S6：`blocked`
- S7：`not_started`
- test 窗：`sealed`
- 因子、权重、交易安全开关：无修改

## 1. 已通过的校验

- ZIP SHA-256：`b6907a92be3f9a6c27df0eaf6dfa356c228e130fc4c8c523828051d2886b1971`，与预期一致。
- 原始机器合同 SHA-256：`a16995e6545ddba7fc03d917ae6cc8d9ab19b7903f1d21c5694b1c0167b1b951`，已按可信外部文件补回且精确一致。
- 顶层 `SHA256SUMS`：160/160 通过。
- 固定样本：15 个 business key 不变；分布为 5 `numeric_match`、8 `formula_match`、2 `expected_unavailable`。
- 9 份公司行动 PDF 中列示的分红金额、日期及无送转事实与候选一致。
- 5 份财务报告的原始行项目与候选公式复算一致；两个 Q1 `revenue_yoy` 样本确无可唯一映射的主营业务收入精确行项目。
- 4 份交易规则 PDF/DOCX 均确认 A 股申报价格最小变动单位为 0.01 元。
- 5 份估值原始 JSON 的日期及 PE/PB/PS 数值与候选一致。
- 本机 S6 报告显示：数据库 `ro`、`query_only=true`、自动检查通过，但 gate 仍为 `blocked`、`ready_for_s7=false`、外部 pairing 尚未接受。

## 2. 阻断项

### P0：公司行动事件窗不满足“全部枚举”合同

合同要求逐个固定窗口枚举 **all official corporate actions**。但 000831、001205、600648、600782 的公告 inventory 在请求阶段已按“权益分派实施公告”过滤；验证器也只识别标题含该短语的事件。该做法无法排除配股、拆并股、股改或其他标题形式的除权事件。001260 虽有通用公告列表，分类器仍只识别同一标题模式。

因此，候选把五个 `event_window.complete` 标为 `true` 的证据不足，五个日线 `formula_match` 不能全部按原合同接受。

### P0：内层来源校验链不闭合

- `candidate/raw-source/SHA256SUMS`：85 项通过、25 项缺失。
- `candidate/raw-source/price-tick-rules/SHA256SUMS`：18 项通过、11 项缺失。
- 缺失文件仍被 `SOURCE-MANIFEST.json` 和内层 checksum 绑定。

README 对脱敏排除已有披露，但披露不能替代内容寻址校验。原合同要求 `schema_hash_integrity_errors=0`，当前外部包无法证明这一条件。

### P0：缺少独立人工架构师身份

合同及交接说明要求可识别的 **独立人工架构师** 签名。本次是 AI 证据复核，不能冒充人工身份，因此不得填写 `reviewer_role`，也不得设置 `approved=true`。

### P1：600782 价格网格舍入规则的权威来源不足

交易规则只证明报价最小变动单位为 0.01 元，并未单独证明除息参考价采用 `ROUND_HALF_UP` 落入价格网格。该舍入对 600782 的倍率结论具有决定性。需要补充权威交易所/供应商算法依据，或以 `⚠ DEVIATION` 明确冻结算法并重建合同及依赖哈希。

### P1：SOURCE-MANIFEST 时间戳不合规

`generated_at="2026-07-31T14:58:56:z"` 不是合法的带时区 ISO-8601 时间。需修正并重建关联哈希。

## 3. 必须保持的候选状态

```json
{
  "approved": false,
  "reviewer_role": "pending",
  "reviewed_at": null
}
```

未返回签认 JSON，也未生成签认文件 SHA-256。

## 4. 最小补正范围

1. 保持原 15 个 business key、本地值、容差、因子、权重和安全开关不变。
2. 对同一 5 个日线窗口抓取完整、无关键词过滤、分页闭合的官方公告清单，并冻结完整公司行动分类合同。
3. 补齐经过脱敏且内容稳定的 25 个 header 文件，重建所有 checksum/manifest/candidate 依赖；不得仅删除清单项。
4. 补齐 600782 舍入规则的权威依据，或登记新的 `⚠ DEVIATION`。
5. 修正 SOURCE-MANIFEST 时间戳并重新机器验证。
6. 技术阻断全部关闭后，由可识别的独立人工架构师完成三个顶层字段签认。

## 5. 关键哈希

- 未签候选文件：`ce66411d2698afc44b73dca99686c5a5e0ea5288193eeff533204f58b0cdabde`
- 未签候选 canonical：`d45d8a90dcce43e4ec273842bebea44ebcc083e99a1e82ef54fe840c1a31a9c5`
- machine-validation：`1f9cf8ae0694ed42d7dafe41a7a83a4bbe9461ed19d749024ebdfb85e045170f`
- 本机 S6 报告：`bd26c5364289e3f8c8c105c3fe605cb8c2af2bb2bbd8667ea4ab9332e9b2a398`
- SOURCE-MANIFEST：`c56144b3d3bb8609439ca3558695bd69b548b21b7c2c9decdf4af754d9b2abf0`
