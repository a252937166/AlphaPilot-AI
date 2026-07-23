# AlphaPilot AI 二期开发详细设计

> 版本 v1.1 · 2026-07-21 · 依据 `docs/AlphaPilot-AI-UI-16x9/` 九张设计稿逐功能对照
> 前置状态：v0.2 已交付（SQLite 持久化、auto 数据链、巨潮公告、板块抽样引擎、8 页霓虹风前端、提案审计流）
>
> **⚠ 实现者入口：本文件是架构背景。动手实现请进入 [`docs/phase2/`](phase2/) 目录——
> 先读 [`00_EXECUTION_GUIDE.md`](phase2/00_EXECUTION_GUIDE.md)（执行守则/质量门/安全不变量/一期坑清单），
> 再按 `01_P2.1_data_foundation.md` → `02_P2.2_engines.md` / `03_P2.3_paper_trading.md` → `04_P2.4_llm_and_frontend.md`
> 的编号步骤逐步执行，每步含表结构代码、接口契约与验证命令，做完一步验证一步。**

---

## 0. 设计原则（延续一期，不可破坏）

1. 数值模型出概率与区间，LLM 只做理解与解释，绝不直接产出价格预测；
2. 所有结论带 `as_of` / 模型版本 / 置信度 / 失效条件；
3. 页面上的每一个数字必须有真实数据来源——宁可显示"待接入"也不摆假数；
4. 实盘执行保持硬禁用；二期只开放**富途模拟账户**执行闭环；
5. 数据源必须有主备路由与降级显示（东财类接口已两次被验证不稳定）。

---

## 1. 设计稿功能盘点与差距清单

| 稿 | 功能点 | 现状 | 二期动作 |
|---|---|---|---|
| 01 总览 | 市场状态仪表 | ✅ | 增强：情绪综合指数替代单一置信度 |
| 01 | 今日机会数/高置信/风险预警 + 较昨日 | 部分（仅自选口径，无环比） | 全市场口径 + 每日快照表支撑环比 |
| 01 | 市场风格概率堆叠图（成长/价值/防御） | ❌ | 风格引擎（§4.2） |
| 01 | 申万一级行业热力图 | 部分（富途10板块抽样） | 全行业热力（§3.4） |
| 01 | 通知铃铛/市场切换/日期回看 | ❌ | 通知中心（§6.9）；市场切换与日期回看列为 P2 可选 |
| 02 登录页 | 账号体系 | ❌ | 可选项（本地单用户工具价值低，见 §8 开放问题） |
| 03 选股 | 筛选器（市场/风格/风险/周期/行业） | ❌ | 全市场选股 + 筛选（§4.1、§6.2） |
| 03 | 胜率列、AI信心强度、状态（新入选/持有中） | ❌ | 因子引擎 + 每日选股快照 diff |
| 03 | 因子权重条（盈利动量/估值/成长…） | ❌（现为占位三因子） | 多因子 v1 权重可视化 |
| 03 | 组合风格暴露 donut / 导出 / 分页 | ❌ | §6.2 |
| 04 个股 | 完整行情头（开高低/换手/PE/市值） | 部分 | 富途快照字段直通（§3.2） |
| 04 | 多周期K线 + B/S 信号标注 + 全屏 | 部分（仅日K） | §6.3；B/S 用历史提醒记录回放 |
| 04 | 五维评分 + 雷达图 + AI评级 x.x/10 | ❌ | 个股综合评分引擎（§4.5） |
| 04 | 投资逻辑/催化剂/风险 + AI解读（利多利空） | ❌ | LLM 事件与解读层（§5） |
| 04 | 事件日历分类（业绩/解禁/分红/调研） | 部分（仅公告） | 事件日历数据源（§3.5） |
| 04 | 目标价区间 / 建议买入金额 | ❌ | 由分位数收益 → 价格区间；仓位建议公式（§4.6） |
| 05 自选 | 自动逻辑状态（强化/不变/转弱）+ 摘要卡 | ❌（手工字段） | Thesis 漂移引擎（§4.4） |
| 05 | 行内最新事件图标 / 事件流 tabs | ❌ | 事件总线（§3.6） |
| 05 | 持仓配置 donut（总市值） | 部分（有字段无聚合） | 组合服务（§4.7） |
| 05 | 批量管理/新建分组/设置列 | ❌ | §6.4 |
| 06 板块 | 预测周期 5/10/20日 + 胜率/预期收益 | ❌ | 板块预测模型 v1 + 滚动评分（§4.3） |
| 06 | 资金流列与资金流分布 | ❌ | 板块资金流（§3.4，富途聚合为主） |
| 06 | 生命周期轮动图 | ❌ | 规则生命周期状态机（§4.3） |
| 06 | 超买预警（RSI）/反转潜力 | ❌ | 板块技术指标包 |
| 06 | 龙头扩散链 | ❌ | 简化版：板块内联动榜（完整产业链图谱留三期） |
| 07 大盘 | 全市场宽度/涨停跌停炸板/成交额环比 | ❌（抽样宽度） | 全市场快照轮询（§3.3） |
| 07 | 分时走势 | ❌ | 富途分时（§3.3） |
| 07 | 市场情绪 xx/100、赚钱效应、资金面 | ❌ | 情绪综合指数（§4.8） |
| 07 | AI实时监测 feed / AI策略提示 | ❌ | 盘中规则解读任务（§5.4） |
| 07 | 跨市场（汇率/商品/美期/北向） | ❌ | §3.7（北向标记高风险） |
| 08 提醒 | 目标价位变化/建议金额 | ❌ | §4.6 |
| 08 | 确认执行 → 已执行流水 | 部分（批准即止） | **模拟交易执行闭环**（§4.9） |
| 09 复盘 | 收益归因 vs 沪深300 / 风险控制 | ❌ | 组合归因（§4.7） |
| 09 | 错误复盘 / 机会回顾（贡献收益） | 部分 | 信号级归因（§4.10） |
| 09 | AI 改进建议 / 重要事件时间线 | ❌ | §5.5 |

---

## 2. 总体架构增量

```text
┌─ 调度器 Scheduler（新，§3.1）────────────────────────────────┐
│ 夜间: 全市场日线增量/主档/行业/财务/日历  盘中: 快照轮询/宽度/情绪 │
└──────────────┬──────────────────────────────────────────────┘
               ▼
 数据底座（新表 §3）: universe / market_snapshot_agg / sector_flows /
   style_daily / factor_values / calendar_events / notifications ...
               ▼
 引擎层（§4）: 多因子评分 · 风格引擎 · 板块预测 · Thesis漂移 ·
   个股五维分 · 情绪指数 · 组合归因 · 仓位建议
               ▼
 LLM 层（§5）: 事件抽取(JSON+来源) · 个股解读 · 盘中监测 · 复盘增强
               ▼
 API 增量（§6.10） → 前端二期（§6） → 模拟交易闭环（§4.9, Futu SIMULATE）
```

技术栈不变：FastAPI + SQLAlchemy(SQLite→可切PG) + Vue3/ECharts。新增进程内 APScheduler（后续可平迁 Celery）。

---

## 3. 数据底座扩展

### 3.1 调度器与任务框架

- 引入 `apscheduler`（进程内，随 API 启动；`ALPHAPILOT_SCHEDULER_ENABLED` 开关，默认 dev 关）。
- 新表 `job_runs(job_name, started_at, finished_at, status, stats_json, error)`——每次任务落审计，复盘页"近期活动"直接消费。
- 任务清单：

| 任务 | 频率 | 内容 |
|---|---|---|
| `sync_universe` | 每日 08:30 | 全A清单+行业分类+ST标记 → `securities` 扩展 |
| `sync_daily_bars` | 交易日 17:30 | BaoStock 全市场日线增量（~5300只，约40-60分钟，分批+断点续传） |
| `snapshot_market` | 盘中每 60s | 富途全市场快照轮询 → 聚合统计（§3.3） |
| `compute_factors` | 交易日 18:30 | 因子库计算（§4.1） |
| `compute_style_daily` | 交易日 19:00 | 风格占比聚合（§4.2） |
| `sector_forecast` | 交易日 19:15 | 板块预测评分与滚动验证 |
| `refresh_theses` | 盘中 10:00/14:00 + 收盘 | 自选预测重算 + 漂移判定 + 提醒 |
| `sync_calendar` | 每日 07:30 | 业绩/解禁/分红日历（§3.5） |
| `daily_review` | 交易日 19:45 | 自动生成复盘（含归因） |

### 3.2 证券主档与行情扩展

- `securities` 扩展列：`industry_csrc`（证监会分类，BaoStock 已验证 5534 条）、`industry_em`（东财/富途行业名）、`is_st`、`list_status`、`market_cap`、`float_cap`、`turnover_rate`、`pe_ttm`、`pb`（后四项从富途快照回填，快照字段本就含 `pe_ttm/turnover_rate/total_market_val/circular_market_val`——个股页行情头直接补齐，**零新增额度成本**）。
- 全A清单主源：AKShare `stock_info_a_code_name`（已验证 5529 只）↔ 备源 BaoStock `query_all_stock`（注意必须传交易日）。

### 3.3 全市场快照轮询与大盘统计（07 稿核心）

- 富途配额实测口径：快照 400 只/请求、60 请求/30s。全A ≈ 5300 只 → **14 请求/轮，约 5-8 秒完成**，60s 一轮远低于配额上限；OpenD 无压力。
- 新表 `market_snapshot_agg(ts, advancers, decliners, unchanged, limit_up, limit_down, broken_boards, total_amount, avg_change_pct, median_change_pct, up_4pct, down_4pct)`：
  - 涨停判定主源：AKShare 涨停池 `stock_zt_pool_em`（**已验证可用**，今日 46 只）交叉富途快照涨跌幅阈值（主板±10%/创业科创±20%/北证±30%/ST±5%，按 `securities` 元数据判）；炸板 = 涨停池 `炸板` 字段。
  - 成交额环比：当日累计 vs 昨日同时刻（`market_snapshot_agg` 历史查询）→ 07 稿"量能变化 +6.64%"。
- 分时走势：富途 `get_rt_data`（订阅制，指数 5 只 × RT 订阅在 100 订阅额度内）→ `GET /v1/market/intraday`。
- 保留现有板块抽样宽度作为降级路径（富途不可用时）。

### 3.4 行业体系与板块资金流（01/06 稿）

- 行业热力全量化：以富途 `get_plate_list(INDUSTRY)` 全部行业板块（~90个）为主体系（一期只取了前10）；成份用 `get_plate_stock` 全量拉取后**落库缓存**（`sector_constituents`，每周刷新一次，绕开 10 次/30s 限制——一次性冷启动分批 5 分钟完成）。申万一级映射列为可选增强（AKShare 申万接口东财依赖，标记不稳定）。
- 板块资金流：**主源 = 富途快照聚合**。快照含 `net_inflow`? （若快照无资金字段，则用 `get_capital_flow` 对板块 TOP30 成份限频轮转，每板块日更 1 次）；备源 = AKShare `stock_sector_fund_flow_rank`（**已验证当前被东财断连**，仅作机会性数据）。新表 `sector_flow_daily(plate_code, date, net_inflow, main_inflow, source)`。
- 热力图 UI 支持 强度/涨跌幅/资金流 三种着色模式切换。

### 3.5 事件日历（04 稿事件tabs）

新表 `calendar_events(symbol, event_type, event_date, title, payload, source, available_time)`，type ∈ {earnings_report(披露), earnings_preview(预告), dividend(分红), unlock(解禁), meeting(调研/股东会)}：

- 分红/披露日期：BaoStock `query_dividend_data` / cninfo webapi（`p_stock2109` 系列探测，验证后接入）；
- 解禁：AKShare `stock_restricted_release_queue_em`（已验证接口存在，参数签名按当前版本适配）；
- 业绩预告：cninfo 公告流关键词分类（LLM 事件抽取兜底，§5.2）。
- 均带 `available_time`，为三期点时回测预留。

### 3.6 事件总线（05 稿事件流）

- 新表 `events(id, symbol, event_type, direction, strength, title, summary, source_ref, occurred_at, ingested_at)`。
- 生产者：公告同步（巨潮）、资金异动检测（快照轮询中 单笔波动>阈值）、预测漂移（§4.4）、日历触发。
- 消费者：自选页事件流 tabs、行内事件图标、通知中心、复盘时间线。

### 3.7 跨市场信号（07 稿底栏）

| 信号 | 来源 | 可行性 |
|---|---|---|
| 人民币汇率 USDCNY | BaoStock 宏观/AKShare 央行中间价 | 高 |
| 美股期指 | 富途 `US.` 期货快照（美期账户行情已验证连通） | 高 |
| 大宗商品 | AKShare 期货主力（东财依赖，标记不稳）→ 备选新浪源 | 中 |
| 北向资金 | 官方已停发盘中数据，仅日度余额 | **低，UI 显示"日度/停发说明"** |

---

## 4. 计算引擎

### 4.1 多因子评分 v1（03 稿因子权重条）

- 新表 `factor_values(symbol, date, factor_name, value, zscore)`、`factor_weights(profile, factor_name, weight, updated_at)`。
- 因子集（全部可从现有数据算出，不买数据）：

| 因子 | 输入 | 对应稿上名称 |
|---|---|---|
| momentum_20/60 | 日线 | 技术趋势 |
| volatility_20 | 日线 | 风险 |
| turnover_change | 快照/日线 | 市场情绪 |
| net_inflow_5d | §3.4 资金流 | 资金流向 |
| roe / profit_growth | BaoStock 季频 `query_profit_data` | 盈利动量/成长性 |
| ocf_ratio / debt_ratio | BaoStock `query_cash_flow_data`/`query_balance_data` | 财务质量 |
| pe_pb_percentile | 快照 PE/PB vs 自身3年分位 | 估值优势 |
| sector_strength | §4.3 | 行业景气 |

- 综合分 = Σ zscore×weight → 0-100；胜率列 = 同分位历史样本 20 日上涨频率（滚动统计表 `score_outcome_stats`）。**权重初始静态（配置文件），页面如实标注"静态权重 v1"**，机器学习动态权重留三期。
- 全市场选股：`POST /v1/screens/run` 支持 `universe: "all"` + 筛选参数（行业/市值/风格/风险等级）；每日定时跑一次落 `screening_runs`，"较昨日 +14""新入选/持有中" 由相邻两次快照 diff 得出。

### 4.2 风格引擎（01 稿风格概率堆叠图）

- 规则分类（透明可审计）：成长 = 营收/利润增速前 40% 且 PE>行业中位；价值 = PB/PE 后 40% 且股息>0；防御 = beta<0.8 或 行业∈{公用/银行/食品}；其余=均衡。
- 日频聚合成交额加权占比 → `style_daily(date, growth_pct, value_pct, defensive_pct, balanced_pct)` → 总览堆叠面积图（ECharts 渐变面积，风格与稿一致）。
- 组合风格暴露（03 稿 donut）：对选股结果/自选按同一标签聚合。

### 4.3 板块预测 v1 + 生命周期（06 稿）

- 特征：板块 5/10/20 日动量、宽度、资金流、换手变化、相对强度 RS。
- 输出 `sector_forecasts(plate_code, date, horizon, score, p_top20pct, expected_excess, model_version)`；预测=横截面排序打分（LightGBM 可选，首版线性加权），**胜率列来自滚动验证**：过去 60 个交易日该模型 TopN 板块未来 h 日跑赢中位数的频率——真实回测数据，不是拍的。
- 生命周期状态机（规则）：strength 趋势↑且资金流入=上涨期；高位滞涨+超买=繁荣期；强度回落=回落期；低位企稳=筑底期；低位强度回升=复苏期。RSI(14) 按板块等权指数算 → 超买预警表。
- 反转潜力 = 低强度分位 + 资金流转正 + RSI<35 组合评分。
- 龙头扩散链（简化）：板块内与龙头 20 日收益相关性 TOP5 联动表（真数据）；产业链图谱留三期。

### 4.4 Thesis 漂移引擎（05 稿逻辑状态自动化）

- 输入：该股预测历史（`forecast_snapshots` 已有）、事件流、板块强度变化。
- 规则：20日 p_up 较 5 日前变动 > +0.08 → strengthened；< -0.08 或 触发失效条件关键词事件 → weakened；否则 unchanged。
- 写 `thesis_transitions(symbol, from_state, to_state, reason, at)`；自选页摘要卡（强化 n ↑x / 不变 n / 转弱 n ↓x）+ mini 走势直接查此表。状态变更即产生 event + notification。

### 4.5 个股五维评分与雷达（04 稿）

- 五维：技术(动量/趋势)、资金(净流入/换手)、基本面(ROE/增长)、估值(分位反向)、情绪(振幅/热度)——全部来自 §4.1 因子的子集聚合，0-10 分。
- 综合 AI 评级 = 五维加权 → 卡片 `8.3/10` + ECharts 雷达图。落 `stock_scores(symbol, date, tech, capital, fundamental, valuation, sentiment, composite, model_version)`。

### 4.6 目标区间与仓位建议（04/08 稿）

- 目标价区间 = 现价 × (1+q10) ~ 现价 × (1+q90)（20日 horizon，标注"分位数区间非目标价"）。
- 建议金额 = 组合权益 × 建议仓位变化；建议仓位 = base(信号类型) × conf 调整 × min(1, 目标波动/个股波动)（波动率倒数缩放），上限受风控 max_single_position 约束。提醒对象增加 `target_range`、`suggested_notional` 字段。

### 4.7 组合服务与收益归因（05/09 稿）

- 新表 `portfolio_snapshots(date, total_value, cash, positions_json, daily_return, benchmark_return, excess_return, max_drawdown)`；数据来源：模拟账户持仓（§4.9）∪ 手工 watchlist 持仓（cost×qty）。
- 每日收盘任务：组合收益 vs 沪深300（SH.000300 已有日线）→ 09 稿"收益归因 +1.27% 超额"、"风险控制 -0.68% 回撤对比"；05 稿持仓配置 donut（按行业聚合市值）。

### 4.8 市场情绪综合指数（07 稿 66/100）

- `sentiment = 0.3×宽度分 + 0.25×涨停生态分(涨停数/炸板率) + 0.25×量能分(额环比) + 0.2×(1-波动分)`，各子分按 250 日历史分位归一 → 0-100，落 `market_sentiment_daily` + 盘中即时值。赚钱效应 = 涨停生态分档位文案；资金面 = 资金流子分档位。权重进配置文件，页面可溯源（点开显示子分）。

### 4.9 模拟交易执行闭环（08 稿"确认执行"，二期核心交付）

- 配置：`ALPHAPILOT_FUTU_ENABLE_TRADE_QUERY=true`（只读）+ `ALPHAPILOT_FUTU_ENABLE_TRADE=true`（仅 SIMULATE 生效；REAL 仍被 live 开关+确认字符串双重拦死，不动一期安全边界）。
- 提案状态机扩展：`pending → approved → executing → executed / failed`（原 approved_no_execution 保留为网关关闭时的终态）。
- 执行器：审批通过且 `mode=paper_auto|confirm_to_trade` → 调 `place_order(trd_env=SIMULATE)` → 轮询 `order_list_query` 回填 `orders(order_id, proposal_id, status, filled_qty, avg_price, updated_at)` → 持仓同步进组合服务。
- 下单前复跑风控 guardrails（数据时效/重复单/仓位），不通过则 `failed(risk)`。
- 幂等：proposal_id 唯一 + 富途订单号绑定；Kill Switch：`trading_halted` 配置项，一键停止执行器。

### 4.10 信号级归因（09 稿机会/错误复盘）

- 每条提醒到期时（expires_at 或 horizon 结束）评分：`alert_outcomes(alert_id, realized_return, hit, contribution)`，contribution = 建议仓位 × 实际收益。
- 复盘页：命中率环比（较昨日 +x pp，需 `daily_reports` 存 hit_rate 历史——已具备）、Top 机会（hit 且 contribution 最高）、Top 错误（miss 且损失最大）。

---

## 5. LLM 层（在 §4 数值结果之上做解释）

- 5.1 接入：沿用 `llm_*` 配置（DeepSeek anthropic 兼容端点可用）；新增 `llm/client.py` 统一封装：JSON-mode 输出、超时降级、prompt 模板表 `llm_prompts(name, version, template)`、调用审计 `llm_calls(purpose, tokens, latency, ok)`。
- 5.2 公告事件抽取：输入公告标题+正文摘录 → 输出 `{event_type, direction(-1..1), strength, horizon_days, entities, source_quote}`（JSON Schema 校验，来源必须引用原文片段）→ 写 `events`。失败降级为关键词规则分类。
- 5.3 个股 AI 解读（04 稿）：输入 = 五维分 + 最近事件 + 板块状态 + 预测 → 输出核心观点(≤120字) + 驱动因素列表（每条带 利多/利空/中性 tag 和来源 id）。缓存 24h 或事件触发重算。
- 5.4 盘中 AI 监测（07 稿 feed）：**规则优先**生成事实条目（量能环比、宽度突变、板块轮动、涨停数变化——全部来自 §3.3 聚合表），LLM 仅做措辞润色（可关）。保证无 LLM 时页面仍有真实 feed。
- 5.5 复盘增强（09 稿）：AI 改进建议 = 输入信号级归因统计（哪类信号/板块/波动区间命中率低）→ 输出建议列表；无 LLM 时显示统计表本身。

---

## 6. 前端二期（逐页）

通用：新增 `NotificationBell`（通知中心抽屉，§6.9）、`RadarChart`、`StackedArea`、`IntradayChart`、`LifecycleWheel`、`CalendarStrip` 组件；全部走既有霓虹设计系统。

- **6.1 总览**：风格概率堆叠图（tab 与指数走势并列，对齐稿）；热力图切换 强度/涨跌/资金流 + 行业全量化 + 底部强弱色带；统计卡接全市场口径 + 环比小箭头。
- **6.2 AI选股**：筛选条 6 控件（市场/风格/风险/周期/行业/排序，映射 §4.1 参数）；表格加 胜率/板块/AI结论/状态 列 + 分页 + CSV 导出（前端生成）；右栏因子权重（真实 v1 权重）+ 风格暴露 donut + AI信心 gauge（=平均置信）。
- **6.3 个股分析**：行情头补 开/高/低/换手/PE/总市值/流通值（快照直通）；周期切换 日/周/月（BaoStock frequency=w/m，分时走富途RT）；K线上 B/S 标记 = 该股历史提醒（buy类↑绿 / reduce类↓红，点击弹提醒详情）；五维评分卡 + 雷达；AI解读卡（利多利空 tags）；事件日历 tabs（公告/业绩/解禁/分红）；底部三按钮 买入/持有/减仓 → 直通提案创建（带目标区间与建议金额）。
- **6.4 自选追踪**：顶部追踪摘要三卡（自动漂移状态 + 7日 mini 趋势）；行内事件图标（公告/财报/资金，来自事件总线，悬停预览）；新建分组/重命名；批量选择（移组/删除/重算）；持仓配置 donut 切到真实市值口径。
- **6.5 板块预测**：周期 tabs（5/10/20日）驱动 `sector_forecasts`；排行榜补 热度(资金流)/未来h日胜率/预期超额 列；生命周期轮盘组件 + 板块归类列表；超买预警/反转潜力/最强看多 三窄卡；资金流向 donut（净流入/流出 tabs）；龙头扩散 = 联动榜表格。
- **6.6 大盘监控**：市场状态区 = 情绪 66/100 仪表 + 赚钱效应/资金面/风险 四子项；分时图（指数 RT，5日/日K tabs）；宽度区换全市场真实数（涨/跌/涨停/跌停/炸板/成交额+环比）；AI实时监测 feed（§5.4）；跨市场信号条（可用项实数，北向显示停发说明）。
- **6.7 交易提醒**：详情卡加 目标价位区间/建议买入金额；"确认执行"接通模拟下单（执行中 spinner → 已执行含成交价）；执行 tab 三态流水；批量操作（批量已读/批量拒绝）。
- **6.8 AI复盘**：四统计卡接真实归因（超额收益/命中率环比/板块判断/回撤对比）；预测vs实际 曲线 = 组合净值 vs 沪深300 归一化；机会/错误复盘表（含贡献收益列）；重要事件时间线（事件总线过滤当日）；AI改进建议卡。
- **6.9 通知中心**：顶栏铃铛 + badge（未读 events/alerts 计数）；抽屉列表按类型过滤；`notifications(user_scope, ref_type, ref_id, read_at)`。
- **6.10 API 增量**（前缀 /v1）：`market/intraday`、`market/sentiment`、`market/breadth-full`、`sectors/forecast?horizon=`、`sectors/flows`、`sectors/lifecycle`、`stocks/{s}/score`、`stocks/{s}/insight`、`stocks/{s}/events`、`stocks/{s}/signals`(B/S标注)、`screens/run(全市场+筛选)`、`screens/diff`、`style/daily`、`watchlist/groups*`、`watchlist/summary`、`portfolio/overview`、`portfolio/attribution`、`trades/proposals/{id}/execute`、`orders*`、`notifications*`、`events*`、`jobs/runs`。

---

## 7. 里程碑与工作量（单人 + AI 结对）

| 阶段 | 周期 | 交付 | 验收门槛 |
|---|---|---|---|
| **P2.1 数据底座** | ~2.5 周 | 调度器、全市场日线/主档/行业、全市场快照轮询、事件日历、事件总线 | 5300 只日线覆盖>99%；盘中聚合每分钟落库；job_runs 可审计 |
| **P2.2 引擎层** | ~2.5 周 | 因子/风格/板块预测/漂移/五维/情绪/仓位建议 | 板块胜率来自真实滚动验证；所有分数带 model_version；单测覆盖各引擎核心函数 |
| **P2.3 模拟交易闭环** | ~1.5 周 | 模拟下单/订单回填/组合归因 | SIMULATE 全流程 E2E 通过；REAL 路径回归测试证明仍被拦截；Kill Switch 生效 |
| **P2.4 LLM+前端二期** | ~2.5 周 | 事件抽取/AI解读/8页升级/通知中心 | 无 LLM 配置时全部页面可降级；逐页对照稿走查 |

总计 ~9 周；P2.1 是硬前置，P2.2/P2.3 可并行，P2.4 依赖前三者出数。

---

## 8. 风险与开放问题

1. **东财依赖**（板块资金流/申万/北向）：已两次实测被封锁，所有东财数据一律"机会性备源"，主路径不依赖；
2. **富途配额**：全市场快照 14req/轮 安全；`get_plate_stock` 冷启动限频（10/30s）→ 成份周更缓存；历史K线 7 日额度决定**全市场历史只能走 BaoStock**；
3. **北向资金**盘中已停发：UI 如实标注，不做伪实时；
4. **SQLite 容量**：全市场日线 ~500万行/年 + 分钟聚合，SQLite 可承载但建议 P2.1 起提供 `docker compose up postgres` 一键切换脚本与迁移文档；
5. **登录页（02稿）**：本地单用户工具做账号体系收益低——建议降级为"启动锁屏 + 演示模式"或放弃，待用户拍板；
6. **合规红线不变**：二期一切执行仅限 SIMULATE；REAL 通路的三重门禁（enable_trade + live_trading + 每单确认串）保持并加回归测试。

---

## 9. 验收清单（对照稿逐页）

- [x] 01 总览：风格堆叠图/全行业热力/环比箭头/通知铃铛
- [x] 03 选股：全市场筛选、胜率列、真实因子权重、状态 diff、导出
- [x] 04 个股：完整行情头、多周期K线+B/S、五维雷达、AI解读、事件tabs、目标区间
- [x] 05 自选：自动漂移摘要、事件图标与事件流、分组管理、市值 donut
- [x] 06 板块：周期预测+胜率、生命周期轮、超买/反转、资金流 donut、联动榜
- [x] 07 大盘：情绪指数、全市场宽度/涨跌停/量能环比、分时、AI监测、跨市场
- [x] 08 提醒：目标区间/建议金额、模拟执行三态流水
- [x] 09 复盘：组合归因四卡、净值对比、机会/错误表、事件时间线、AI建议
- [x] 系统：通知中心、job 审计、无 LLM/无富途双降级、REAL 拦截回归

验收证据见
[`docs/phase2/screenshots/p2.4-s15/README.md`](phase2/screenshots/p2.4-s15/README.md) 与
[`docs/phase2/P2.4-S15_ACCEPTANCE_CHECKLIST.md`](phase2/P2.4-S15_ACCEPTANCE_CHECKLIST.md)。
02 登录欢迎稿按 §8.5 的开放范围项诚实处置，未伪造账号或鉴权能力。
