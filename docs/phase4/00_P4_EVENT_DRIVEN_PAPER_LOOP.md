# P4（M4）：事件驱动资讯引擎 + 富途模拟盘自动闭环

> 基线 commit：`e288be6`（P3.3-S9 收官）。本文是交给 Codex 的实现级步骤书：按编号顺序做，
> 每步跑完"验收"才进下一步，做完标 `✅ done` 并独立提交；架构复核延续既有 AI 审核制。
>
> **参数冻结记录（2026-08-01）**：本文全部预注册参数由 owner 明确委托 Claude Code 代定并
> 冻结。任何后续修改 = `p4_policy` 新版本 + 新评估期，不得用旧期间成绩。

**目标**：系统每个交易日实时监听市场资讯 → LLM 抽取关键事件 → 结合既有因子分生成少量
候选推荐 → 对推荐与持仓做高频监控 → 按预注册策略状态机在**富途 SIMULATE 模拟盘**自动
建仓/加仓/减仓 → 每日诚实评估。

**明确边界**：P4 交付的是"可审计的事件驱动假设检验闭环"，不是"稳赚选股器"。
推荐输出一律带 `as_of`、事件依据、置信度与失效条件；模拟盘亏了如实报亏。

## 0. 铁律（承接 P2/P3 全部纪律，违者整步作废）

1. **只动 SIMULATE**。`live_trading_enabled=false` 三闸不许改；`unlock_trade` 永久封锁不许碰；
   每笔下单必须显式 `environment="SIMULATE"`；`broker_orders` 中禁止出现非 SIMULATE 行。
2. **资讯 PIT**：`available_time` = 抓取落库时刻（UTC），绝不回填；任何决策只允许使用
   `available_time < 决策时刻` 的事件。重放测试必须证明无未来函数。
3. **预注册**：策略参数、推荐口径、评估准则先写死进版本化 config，再看数据；改参数 = 新版本
   + 新评估期，不许滚动调参后用旧期间成绩。
4. **M3 结论继承**：composite-v3 未转正（`docs/phase3/reports/P3.3-S9-…json`）。因子分只作
   候选池的**辅助排序**，不得单独作为推荐依据；事件驱动是待检验的新假设。
5. **来源纪律**：每个外部源固定频控、有界退避、来源入 `provenance` 白名单。东财 push 端点
   2026-07 曾对本机封锁（TLS 指纹 + 疑似 IP 黑名单，见 phase2 记录），2026-08-01 实测已恢复
   （Python httpx 可取真数据）——**允许作为机会性备源，但禁止进入关键路径**：spike 须记录
   其当前状态，运行时必须假设它随时再被封、降级路径常备。
6. 失败如实入 JobRun；quality gates（ruff / mypy strict / 全量 pytest）每步全绿。

## P4.1 资讯底座（先做 1 天可行性 spike，产出报告后停下等复核）

**Spike**：实测并出具 `docs/phase4/reports/P4.1-source-spike-<date>.json`，逐源记录
可用性/延迟/字段/频控/封锁情况：
- 巨潮公告增量轮询（已有 `src/alphapilot/cninfo/client.py`，两步查询免凭据，最可靠）；
- 新浪财经个股资讯（本机对 sina 直连可用，见 `data/baostock_provider.py` 的 hfq 先例）；
- AKShare 中**非东财上游**的新闻接口（东财源已封，逐一验证再用）；
- 富途快照/推送中的辅助信号（涨跌异动，无新闻正文）。

**实现**：
- 表 `news_items(id, source, symbol NULLABLE, title, url UNIQUE, published_at NULLABLE,
  available_time NOT NULL, content_hash, raw_payload)`；迁移走 `db/migrate.py::MIGRATIONS`。
- Job `news_poll`（`jobs/registry.py` 注册）：交易时段全市场每 10 分钟、盘后每 30 分钟
  （watchlist 提频见 P4.4）；断点续抓、`url+content_hash` 双去重、每源独立频控与失败计数。
- 来源白名单加入 `data/provenance.py`（新增 `AUDITED_NEWS_SOURCES`）。

**验收**：连续 3 个交易日运行，零重复、失败如实记录；`available_time` 100% 为抓取时刻；
spike 报告哈希写入本文档。

## P4.2 LLM 事件抽取

- 事件 taxonomy v1（版本化常量）：`earnings_preannounce / major_contract / buyback_or_holder_change /
  regulatory_action / halt_resume / ma_restructure / policy_sector / dividend / other`。
- 每条新闻 → 严格 JSON（走 `src/alphapilot/llm` 现有层，purpose model 配置）：
  `{symbols[], event_type, direction(-1/0/+1), materiality(0-3), summary, confidence(0-1),
  evidence_span}`；解析失败/超预算如实落 `extract_failed`，禁止编造。
- 成本与延迟预算（config 固定）：走本地 LLM 配置；单条输出 ≤ 2k tokens、端到端 ≤ 20s、
  日抽取上限 2,000 条；超限降级为仅入库不抽取并计数告警。
- 表 `news_events`（FK news_items，幂等 upsert，含 `model_version`）。
- **金标准评测**：owner 参与人工标注 100 条固定样本；预注册验收门：
  `materiality>=2` 事件的 precision ≥ 0.80、symbol 映射准确率 ≥ 0.95。达不到就修 prompt/换模型
  再评，不放水阈值。

## P4.3 事件 + 因子融合的每日候选推荐

- 触发：盘前汇总夜间事件 + 盘中每次 news_poll 后增量。
- 候选 = 近 24h `direction=+1 且 materiality>=2` 事件标的 ∩ 流动性门
  （20 日均成交额 ≥ 5,000 万元、非 ST、非停牌、上市满 60 日）→ 按 composite 分位辅助排序
  → 每日最多 K=5 只（config 预注册）。
- 表 `event_recommendations`：symbol、依据事件 id 列表、composite 分位、`as_of`、
  建议仓位档、**失效条件**（如"公告被澄清/更正则立即失效"）。
- API `GET /v1/recommendations/today` + 前端卡片（复用现有页面骨架）。
- **验收**：每条推荐可完整回溯到事件行与分数行；重放测试证明决策时刻可见性成立。

## P4.4 高频监控 + 调仓策略状态机（预注册 `config/p4_policy_v1.yaml`）

- watchlist = 当前 SIMULATE 持仓 ∪ 当日推荐；富途行情 push 订阅（复用 `/v1/futu/stream`
  链路与订阅上限管理）；watchlist 内个股新闻轮询提频至 5 分钟。
- 状态机 v1（简单可审计，全部参数进 config）：
  - 入场：推荐生成后下一交易时段，限价 = 现价 ±0.5% 带；单股初始仓位 ≤ 总资金 8%；
  - 加仓：持仓期间出现**新增**正向 materiality≥2 事件，且未触发止损、单股仓位 < 15% 上限；
  - 减仓/清仓：负向 materiality≥2 事件 → 减半；-8% 止损清仓；持有满 20 交易日无新事件 → 清仓；
  - 全局风控：总仓位 ≤ 80%、日下单次数 ≤ 20、同股冷却 1 交易日、交易时段外禁止动作。
- **验收**：状态机全迁移路径单测；历史事件重放测试；参数文件 SHA 记录在案。

## P4.5 SIMULATE 自动执行（沿既有守护链，绝不绕行）

- 路径固定：策略 → `trade_proposals` → `risk/guardrails.py` → `services/executor.py` →
  `trade/futu_gateway.py`（SIMULATE）。禁止新开直连下单路径。
- `paper_auto` 开关仅对 P4 策略 scope 生效，另加：环境变量 + API 双通道 kill switch、
  交易时段门、日成交额与日亏损熔断（触发即全停并告警）。
- 审计：所有提案/成交/拒单/熔断入库；每日核对 `broker_orders` 环境字段 100% SIMULATE。
- **验收**：连续 5 个交易日无人工干预自动运转；kill switch 与两类熔断各演练一次并自动恢复；
  live 三闸复核仍 false；`unlock_trade` 仍在 `PERMANENTLY_BLOCKED_METHODS`。

## P4.6 诚实评估（预注册，运行满 40 个交易日后裁定）

- 日报（复用复盘报告框架）：模拟盘扣费 NAV vs 沪深300 vs 等权、当日事件与动作流水、
  推荐次日/5 日超额、事件类型归因表、成本占比。
- **预注册期末准则**（先于任何结果固定）：40 交易日模拟盘扣费超额对双基准均 > 0，且
  事件信号(次日超额)的 t ≥ 2 → 判"事件驱动假设初步成立"，进入下一期滚动验证；
  任一不满足 → 如实判"未成立"，写明归因，不改准则重跑。
- **红线**：无论结果如何，P4 不解锁实盘。实盘是独立里程碑（券商合规/资金/审批/风控演练），
  且前置条件至少包含两期连续评估通过。

## 执行顺序

P4.1 spike → 复核 → P4.1 全量 → P4.2 → P4.3 → P4.4 → P4.5 → P4.6。
每步独立提交；涉及 `trade/` 与 `risk/` 的改动必须保持既有安全测试全绿并新增对应回归。
