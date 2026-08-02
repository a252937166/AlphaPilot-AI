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

**Spike ✅ done 2026-08-02**：实测并出具
`docs/phase4/reports/P4.1-source-spike-<date>.json`，逐源记录
可用性/延迟/字段/频控/封锁情况：
- 巨潮公告增量轮询（已有 `src/alphapilot/cninfo/client.py`，两步查询免凭据，最可靠）；
- 新浪财经个股资讯（本机对 sina 直连可用，见 `data/baostock_provider.py` 的 hfq 先例）；
- AKShare 中**非东财上游**的新闻接口（东财源已封，逐一验证再用）；
- 富途快照/推送中的辅助信号（涨跌异动，无新闻正文）。

### P4.1 source spike 证据记录（2026-08-02）

- 最终预注册契约：commit `9504530`，config
  `config/p4_source_spike_v2.yaml`，SHA-256
  `0b8f28ba7136fcda7372a8389faa659ee80ea5240ea2799df9576d52eea4d5fd`。
- 最终机器报告：`docs/phase4/reports/P4.1-source-spike-20260802.json`，SHA-256
  `58a28066c3c6489cd687fb217de6df78cec0a7d9f7078bccf17637081a216316`；
  JobRun `45488` 为 `ok`，config/执行输入运行前后哈希一致。
- 人工逐样本复核：`docs/phase4/reports/P4.1-source-spike-20260802.review.json`，
  SHA-256 `b2e9fb3b35c4fa69716fb0e4e9dc8237240d960e52183c9cd72ca4cf470cbc4d`。
- PIT：23/23 样本的 `available_time` 均为分源证据写入 JobRun 的 UTC 时刻，
  `available_time == published_at` 为 0；本 spike 不建 `news_items`，正式 P4.1 仍须按同一
  落库语义实现并做重放测试。
- 安全：执行前后 `trade_proposals=1/1`、`broker_orders=1/1` 且身份哈希不变，
  非 SIMULATE 委托为 0；`research`、`live=false`、`paper_auto=false`、
  `futu_enable_trade=false`、账户 mutation=false、`unlock_trade` 永久封锁。

逐源结论：

- 巨潮：主来源候选；8/8 受限请求成功、无重试/限流。样本中发现 3 个重复 URL，正式实现
  必须执行 `url+content_hash` 幂等去重。当前为兼容既有客户端关闭 TLS 证书校验，进入关键
  路径前须单独处置证书链风险。
- 新浪：标题/URL 主来源候选；`datelist` 容器能排除行情页与导航假阳性。但 9 条样本中仅
  4 条标题明确命中受测公司，页面上下文不得单独证明个股归属；未明确命中证券代码/名称时
  必须写 `symbol=NULL`。当前 `published_at` 未规范抽取，保持 nullable。
- AKShare 非东财：降级候选；同花顺上游字段完整可作主来源候选，财联社上游单次 HTTP 404
  如实 unavailable，财新只有摘要/URL、无标题，只作辅助。
- 富途辅助：在冻结字段门下 unavailable；`get_market_snapshot` 实际无 `change_rate` 字段，
  本轮未事后放宽门槛。仅调用 `get_market_snapshot`，交易方法调用为 0；周日无法验证推送
  延迟与交易时段新鲜度。
- 东财：按 owner 本轮明确范围未探测、未进入请求路径、不得据规格中的历史恢复描述晋级。

⚠ DEVIATION：首次 v1 运行 JobRun `45453` 将 `realstock/company` 行情页误判为新浪新闻。
原始报告按 SHA
`d73673c0b70cab57270cc08f646598bcbaf247f3adfcd3db5b6757c0a46bf5cb`
原样保存在 `P4.1-source-spike-20260802-invalid-v1.json`，并由配套 invalidation JSON 明确禁止
用于来源晋级；未删除、未改写历史 JobRun。修复经新版本配置先提交、后重测，未用 v1 结果
调策略或评估参数。

**边界**：这里只标 source spike done。P4.1 表/迁移/`news_poll`/三交易日验收均未开始，
P4.2 继续锁定，等待独立复核。

> **✅ 独立 AI 架构复核通过（2026-08-02，Claude Code / claude-fable-5）**：
> 四工件哈希逐一实核精确一致（report `58a28066…`、review `b2e9fb3b…`、config `0b8f28ba…`、
> invalid-v1 `d73673c0…`）；提交链 94d6770→fef39ca→9504530→1c7f4ae 顺序正确，报告
> `execution_commit=9504530`、基线 `e288be6` 绑定无误；JobRun 45488 的 config 前后哈希
> == 预注册值；PIT 审计 23/23 UTC、零回填、6 条诚实 symbol=NULL；safety before==after、
> 非 SIMULATE 委托 0；gate 标志如实（仅 spike done、P4.2 锁定）；v1 误判按"留档→判无效→
> 新配置先提交→重测"处置，零静默覆盖；复核方独立重跑质量门 1033 passed / 1 skip、
> ruff、strict mypy 全绿；`cninfo/client.py:51 verify=False` 实核属实。
> **裁定：解锁 P4.1 全量实现**，随行条件：① cninfo 修复 TLS 证书校验为 P4.1 全量验收项，
> 修复前不得进关键路径；② `symbol=NULL` 纪律与 `published_at` nullable 进 schema 语义
> 与测试；③ `url+content_hash` 去重必做（spike 已见 3 重复 URL）；④ 富途辅助信号须在
> 交易日重测推送延迟后才可启用；⑤ 财联社按 unavailable 处置，不得静默重试入池。

**实现**：
- 表 `news_items(id, source, symbol NULLABLE, title, url UNIQUE, published_at NULLABLE,
  available_time NOT NULL, content_hash, raw_payload)`；迁移走 `db/migrate.py::MIGRATIONS`。
- Job `news_poll`（`jobs/registry.py` 注册）：交易时段全市场每 10 分钟、盘后每 30 分钟
  （watchlist 提频见 P4.4）；断点续抓、`url+content_hash` 双去重、每源独立频控与失败计数。
- 来源白名单加入 `data/provenance.py`（新增 `AUDITED_NEWS_SOURCES`）。

### P4.1 全量实现预注册记录（2026-08-02，三交易日验收前）

- 当前实现基线为 `f9641e5`（包含 `7379e3d` 的 Top50 披露徽标与 `5f3cc57` 的 CI
  环境修复）；本步直接在最新 `main` 上追加验收合同，没有 rebase 或覆盖 screening/域模型/前端改动。
- 冻结配置：`config/p4_news_poll_v1.yaml`，SHA-256
  `d0dcd665472b50092a1b4fa7f65f7115778e1b89ac11aca0ed49dc70beaa790b`；观察窗固定为
  2026-08-03、2026-08-04、2026-08-05，上海时区每日 64 个槽位（交易时段 10 分钟、
  其余时段 30 分钟）。交易日依据为上交所 2026 年休市安排（上证公告〔2025〕45号）。
- Owner 于 2026-08-02 23:40 CST、正式观察窗开始前追加硬门：验收报告必须逐日列出
  `cninfo_inserted_by_trading_date`，且上述三个交易日的巨潮实际插入数必须**每天 > 0**；
  任一天为 0 即整体验收失败，并排查查询窗口/字段映射，禁止用“无新公告”解释。此次修订仅
  加固验收配置、只读验收器与测试，未改变来源、抓取窗口、映射、频率或请求预算。
- 审计白名单固定为 `cninfo / akshare_ths / sina_company_news`。巨潮只允许 HTTPS 且
  `verify=True`；`symbol` 与 `published_at` 均允许 `NULL`，页面上下文不能单独绑定股票；
  `available_time` 仅在取得 SQLite 写锁后、紧邻唯一 INSERT flush 前取当前 UTC 时刻，
  flush/commit 完成时刻另存 JobRun 审计，不从 `published_at` 回填。
- URL 与内容哈希为两个独立唯一键；重放保持首个 `available_time` 不变。财联社固定
  `unavailable` 且零请求/零重试；财新固定排除；富途辅助固定
  `pending_trading_day_latency_retest`，本版本不调用任何行情或交易方法。
- 代码完成质量门：Ruff、strict mypy 与全量 pytest（1053 passed / 1 skipped）均通过。
  本记录只表示实现与参数已在看数据前冻结；三交易日机器证据尚未产生，**P4.1 不标 done，
  P4.2 继续锁定**。

### P4.1 全量实现预验收记录（2026-08-02，非三交易日正式样本）

- 预注册实现已独立提交：`8d68094`。迁移前在线备份
  `data/backups/alphapilot-pre-p4.1-20260802T034014-CST.db`，SHA-256
  `12eabd0c652722ab5b7115d990e7e6cdc3e36721533335326bed3d6e1829b807`；迁移后
  `news_items` schema/双唯一键/PIT 索引存在，`PRAGMA quick_check=ok`。
- 真实 JobRun `46094 status=ok`：巨潮严格 TLS 请求 2、失败/重试 0；同花顺抓取/插入
  20/20；新浪抓取 117、插入 115、URL 重复 2；合计插入 135。财联社、财新、富途辅助
  均 0 请求，富途行情/交易方法调用列表为空，safety before/after 一致。
- 立即重放 JobRun `46096 status=ok`：抓取 137、插入 0、URL 去重 137；原 135 行
  `available_time` 摘要前后相同。库内 URL 重复组 0、内容哈希重复组 0、
  `published_at==available_time` 为 0；逐行
  `fetch≤write-lock≤available≤flush≤commit≤poll_completed` 无异常。
- `com.alphapilot.scheduler` 已由版本化模板安全重启；安装 plist 与 `launchctl` 环境均为
  `ALPHAPILOT_NEWS_POLL_ENABLED=true`，启动日志列出 `news_poll`（共 20 个任务）。API/OpenD
  健康；交易表仍为 proposal/order `1/1`，非 SIMULATE 委托 0。
- 以上只证明迁移、真实抓取、幂等和部署门；不替代 2026-08-03/04/05 三交易日正式验收，
  因此 P4.1 仍不标 done，P4.2 仍锁定。

**验收**：连续 3 个交易日运行，零重复、失败如实记录；`available_time` 100% 为抓取时刻；
巨潮三个交易日实际插入数逐日列出且每天 > 0；spike 报告哈希写入本文档。

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
- **"重磅消息"的机器定义（owner 疑问的正式答案）**：`materiality>=2` 且通过评测门的事件
  即"重磅"——判定权在已评测的抽取器，不靠人工盯盘；重磅事件到达即触发 P4.3 盘中增量
  推荐与 P4.4 的插队复看/状态机评估。

## P4.3 事件 + 因子融合的每日候选推荐

- 触发：盘前汇总夜间事件 + 盘中每次 news_poll 后增量。
- 候选 = 近 24h `direction=+1 且 materiality>=2` 事件标的 ∩ 流动性门
  （20 日均成交额 ≥ 5,000 万元、非 ST、非停牌、上市满 60 日）→ 按 composite 分位辅助排序
  → 每日最多 K=5 只（config 预注册）。
- 表 `event_recommendations`：symbol、依据事件 id 列表、composite 分位、`as_of`、
  建议仓位档、**失效条件**（如"公告被澄清/更正则立即失效"）。
- API `GET /v1/recommendations/today` + 前端卡片（复用现有页面骨架）。
- **AI 研判注解层（owner 需求登记 2026-08-02）**：推荐卡与既有 Top50 视图每股附 LLM
  研判注解——输入为近 7 天 `news_items` 命中 + 因子画像 + 披露标注，输出摘要 / 红旗事件 /
  置信度 / 失效条件，记录 `model_version` 与 `as_of`；**注解不改排序**。负向
  materiality≥2 红旗（监管处罚 / 减持 / 问询函等）须醒目标注。LLM 对选股的实际影响只经由
  已过金标准评测门的事件信号（本节候选规则），**禁止自由文本重排**——这是可复现性与
  可评测性的边界。前置铺垫已于 2026-08-02 先行交付：Top50 薄流动 / 高波动披露徽标
  （commit `7379e3d`，仅展示不过滤）。
- **验收**：每条推荐可完整回溯到事件行与分数行；重放测试证明决策时刻可见性成立；
  注解层验收 = 注解不参与排序的回归测试 + 红旗标注与 news_events 行的可回溯绑定。
- **过渡期 advisory 简报（2026-08-02 部署，P4.3 上线后退役）**：`scripts/run_daily_ai_brief.py`
  经 launchd（`com.alphapilot.daily-ai-brief`，交易日 21:07）只读取 `news_items` 与市场状态、
  调项目 LLM 生成当日研判写入 owner Obsidian（`AI/每日研判/`）。零 DB 写入、不入 jobs
  registry、不产生 news_events 或推荐，输出显式标注 advisory/未评测；与 P4.2/P4.3 正式
  管道无共享构件，P4.3 验收时一并卸载该 launchd 任务。

## P4.4 高频监控 + 调仓策略状态机（预注册 `config/p4_policy_v1.yaml`）

- watchlist = 当前 SIMULATE 持仓 ∪ 当日推荐；富途行情 push 订阅（复用 `/v1/futu/stream`
  链路与订阅上限管理）；watchlist 内个股新闻轮询提频至 5 分钟。
- 状态机 v1（简单可审计，全部参数进 config）：
  - 入场：推荐生成后下一交易时段，限价 = 现价 ±0.5% 带；单股初始仓位 ≤ 总资金 8%；
  - 加仓：持仓期间出现**新增**正向 materiality≥2 事件，且未触发止损、单股仓位 < 15% 上限；
  - 减仓/清仓：负向 materiality≥2 事件 → 减半；-8% 止损清仓；持有满 20 交易日无新事件 → 清仓；
  - 全局风控：总仓位 ≤ 80%、日下单次数 ≤ 20、同股冷却 1 交易日、交易时段外禁止动作。
- **持仓级小时 AI 复看（owner 需求登记 2026-08-03）**：交易时段每小时对 watchlist 每只
  标的执行一次 LLM 持仓复看——输入为该股近 24h 全部事件与资讯、当日价格走势、持仓成本
  与浮盈亏、状态机当前状态；输出 `hold/reduce/exit` 建议 + 理由 + 置信度，逐条落库为
  **影子记录**（shadow advice，先不触发任何动作）。`materiality>=2` 重磅事件到达时立即
  插队一次复看，不等整点。运行满 20 个交易日后按预注册准则评测影子建议命中率（如
  `exit` 建议后 5 日该股相对超额为负的比例 ≥ 0.55），达标后经 policy 新版本把该建议接入
  状态机触发源；未达标前，动作仍完全由规则状态机决定。这是"AI 直接决定卖不卖"的唯一
  合规升级路径：先影子、再评测、后执权。
- **验收**：状态机全迁移路径单测；历史事件重放测试；参数文件 SHA 记录在案；影子复看
  记录表就位且零动作副作用（回归测试锚定）。

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
  推荐次日/5 日超额、事件类型归因表、成本占比；**自选股处置段**（当日淘汰/减仓标的及其
  事件依据、次日新进候选与理由——对应 owner"每日复盘找新机会 + 淘汰旧自选"需求）。
- **预注册期末准则**（先于任何结果固定）：40 交易日模拟盘扣费超额对双基准均 > 0，且
  事件信号(次日超额)的 t ≥ 2 → 判"事件驱动假设初步成立"，进入下一期滚动验证；
  任一不满足 → 如实判"未成立"，写明归因，不改准则重跑。
- **红线**：无论结果如何，P4 不解锁实盘。实盘是独立里程碑（券商合规/资金/审批/风控演练），
  且前置条件至少包含两期连续评估通过。

## 执行顺序

P4.1 spike → 复核 → P4.1 全量 → P4.2 → P4.3 → P4.4 → P4.5 → P4.6。
每步独立提交；涉及 `trade/` 与 `risk/` 的改动必须保持既有安全测试全绿并新增对应回归。
