# P2.4-S15 无 LLM 降级证据

- 验收日期：2026-07-23
- 方式：只对受管 API 进程临时清空 `ALPHAPILOT_LLM_API_KEY` 与
  `ALPHAPILOT_LLM_BASE_URL`，未编辑 `.env`，未输出密钥。
- 安全边界：只读页面与 GET API；未点击生成、刷新、已读、提案或执行操作。

## 结果

| 检查 | 结果 |
|---|---|
| 正常基线 | `llm_calls=99`，其中 `ok=98`；总览 `ai_summary.source=llm` |
| 无配置重启 | API/DB/Futu 仍健康，`live_trading_enabled=false`、`unlock_trade_endpoint_exposed=false` |
| 市场总览 | 新摘要使用 `source=template`，页面明确显示“规则” |
| 个股分析 | 数值与行情不受影响；已有且仍在有效期内的真实 `source=llm` 解读可继续展示，不伪造成新调用 |
| 大盘监控 | 事实 feed 使用规则生成，事实、排序、时间和级别均不由模型编造 |
| AI 复盘 | 历史已落库的真实模型摘要保留；改进建议显示 `source=statistics` |
| 审计 | 缺配置调用写入一条 `ok=false` 审计记录；没有把失败内容写成 AI 结论 |
| 恢复 | 清除进程级覆盖并重启后，总览恢复 `source=llm`；恢复时 `llm_calls=101`、`ok=99` |

四页均无 console error、页面横向溢出、`undefined`、`NaN`、无限加载或假 0：

- `degrade_no_llm_overview.png`
- `degrade_no_llm_stock.png`
- `degrade_no_llm_market.png`
- `degrade_no_llm_review.png`

> 说明：降级测试允许展示此前已经审计并持久化的真实 LLM 产物；关键判据是无配置时不发出成功
> 的新模型调用，且新内容必须走模板/规则/统计并如实标源。
