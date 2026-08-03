# P4.2a 离线事件抽取评测区

本目录只保存 P4.2a 的离线试跑、盲标样本和评测证据。它不是生产数据目录，不得把任何文件
回写 `news_items`，也不得从这里触发推荐、提案、委托或交易。

## 当前门控

- 合同基线：`config/p4_event_extract_eval_v1.yaml`。
- 生产数据库只能以 SQLite `mode=ro` + `PRAGMA query_only=ON` 打开。
- LLM 固定为 `.env` 中为 purpose 解析出的 `qwen3.6-flash`；密钥、Authorization header、
  原始异常响应不得进入本目录。
- 现库存全量试跑只覆盖冻结快照 `news_items id<=423`。
- 100 条 owner 金标准由 60 条现库存和 40 条 2026-08-04/05 自然新增组成；标注文件不包含
  模型预测，所有 `gold` 字段初始为 `null`。
- 巨潮样本的公告正文只允许从其落库的 `https://static.cninfo.com.cn/` PDF 在 eval 路径临时
  获取并提取；记录 PDF/正文哈希，不保存 PDF，不回写生产库。获取或提取失败即阻断，不把
  标题伪装成正文，也不静默换样本。

## Owner 标注规则

Owner 仅填写每条记录的 `gold`：

- `symbols`：去重、升序的 6 位 A 股代码数组；无法唯一映射时为 `[]`；
- `event_type`：taxonomy v1 九类之一；
- `direction`：`-1 / 0 / 1`；
- `materiality`：`0 / 1 / 2 / 3`；
- `evidence_span`：必须逐字来自该条 `original_text` 的连续片段；
- `notes`：可选人工说明。

不得查看或复制离线模型预测来填 gold。Owner 返回后，评测器会重新校验固定 ID、原文哈希、
标签完整性和阈值；不达标只能发布 prompt/model 新版本后在同一固定样本上重评。

## 未完成条件

截至 2026-08-03，只允许冻结合同、执行现库存离线试跑并固定首批 60 条。8/4–8/5 的 40 条
必须等自然数据完整落库后追加；Owner 未返回完整标签、两项硬指标未通过前，P4.2a 不标 done，
P4.2b 继续锁定。
