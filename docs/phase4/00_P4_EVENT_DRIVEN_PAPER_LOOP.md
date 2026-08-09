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

### ⚠ P4.1 观察窗 Day 1 覆盖缺口与部署时区风险（2026-08-03，独立复核实测）

**结论：这是 P4 全程最高优先级的风险，高于任何已知代码缺陷。**

- **实测事实**（CST 交易日边界，非 UTC 边界）：8/3 全日 `news_poll` 44 次运行、38 成功、
  6 失败；6 次失败**全部落在交易时段**（09:50/10:20/10:30 等）。更严重的是
  **08:30→10:00 CST 之间零成功入库**（09:00/09:30/09:40 三个槽位未触发 + 09:50 失败），
  该窗口覆盖 09:15 集合竞价与 09:30 开盘——事件驱动价值最高的时刻。
- **根因（pmset 日志实证，非推断）**：`2026-08-02 20:46:02 EDT Entering Sleep state due to
  'Clamshell Sleep'` → `21:42:43 Wake`，即 **08:46–09:42 CST 笔记本合盖睡眠**。
  排除项：机器未因空闲睡眠（`sleep 0` 已设）、任务未卡死（各次运行 12–17 秒）、
  scheduler 未重启（最后一次启动早于观察窗开始）。`PreventUserIdleSystemSleep` 断言
  （FutuOpenD/UURemote）**不拦合盖睡眠**。
- **结构性风险**：本机时区为 EDT，A股 09:30–15:00 CST 恒等于 **21:30–03:00 EDT**，
  即 owner 的睡眠时段。当前部署下，**每个交易日的整场交易时段都依赖笔记本保持不合盖**。
  这对 P4.3 盘中增量推荐、P4.4 小时复看、P4.5 SIMULATE 自动执行是**致命**的：
  没有全天候运行的宿主，事件驱动闭环在架构上不成立。
- **时区口径纠正**：`job_runs.started_at` 与 `news_items.available_time` 均为 **UTC**，
  而观察窗与 64 槽位合同以**上海时区**定义。任何按 UTC 日边界统计的日报都会切错窗口
  （复核初查即因此误判失败发生在"深夜"，实为早盘）。验收器必须以 CST 日边界统计，
  并在报告中同时给出双时区时刻。

**处置**（不改动观察窗冻结配置）：

1. **立即（保护 8/4、8/5 两个交易日）**：owner 在 CST 交易时段保持宿主不休眠——
   `sudo pmset -a disablesleep 1`（可事后 `0` 还原），或交易时段保持接电且不合盖。
2. **验收报告必须新增覆盖率章节**：逐交易日给出槽位应执行/实执行/成功数、交易时段
   （09:25–15:05 CST）失败与空档明细、以及每段空档的根因归类
   （宿主休眠 / 上游不可用 / 重试预算缺陷），不得以"失败如实记录"笼统带过。
   Day 1 的宿主休眠缺口须原样披露，不得补抓、不得回填。
3. **P4.1 验收判据不变**（数据完整性：去重、PIT、巨潮逐日 >0），本缺口不追溯改判。

#### 宿主决议：不迁移 + 周一调度调整 + 断网后补（owner 决定 2026-08-03）

**实测证据（poll_market_snapshot 历史，09:20–10:00 CST 开盘窗口）**：周一平均 13.5 次运行
（07-27 为 9 次、08-03 为 18 次且全失败），其它交易日 40.9 次（7 个交易日均满覆盖）。
owner 确认该时段为**人为断网、周期性复发**，非代码或上游故障。已评估迁移至阿里云北京实例
（`47.93.234.51`，7×24、时区 Asia/Shanghai、当前零负载）；**owner 决定暂不迁移**，
故按产品限制处理，不再作为 P4.3 前置门。同时记录：`pmset disablesleep` 一类保持唤醒的
手段对断网无效，不采用。

**由此产生的强制处理要求（下游各步必须实现，不得默认忽略）**：

1. **覆盖状态必须是一等数据**：`news_poll` 每轮记录覆盖判定；系统须能回答"某时刻资讯管道
   是否在线"。P4.3/P4.4/P4.6 一律不得把"无事件"与"无覆盖"混为一谈。
2. **P4.3**：覆盖缺口期间**不产生推荐**，并在恢复后的首轮显式标注
   `preceded_by_coverage_gap`，其推荐须按迟到事件处理（时效性折扣），不得当作实时信号。
3. **P4.4（风险要害）**：持仓在盲区内**无法获得退出信号**。状态机须对跨盲区持仓采取保守
   处置：盲区期间标记 `unmonitored`，恢复后**优先**执行一次插队复看再允许任何加仓；
   盲区内不得因"无负向事件"而判定持仓安全。owner 提出的"盘中每小时复看、发现利空马上卖"
   在周一开盘时段**存在结构性空洞**，须在产品说明中明示。
4. **P4.6（评估公正性）**：40 交易日评估必须**分别报告**"全窗口"与"剔除覆盖缺口窗口"
   两组结果，并披露缺口占比。禁止只报有利的一组；亦禁止把盲区期间的市场变动计入
   策略业绩或归因。事件驱动假设的成立与否，以剔除缺口组为准、全窗口组为诚实补充。
5. **每日 advisory 简报**：如实标注当日是否存在覆盖缺口及其时段。

#### 后补（catch-up）机制现状与 v2 工作项

**已内建、且已实证工作**：`news_poll` 采用 watermark 断点续传，
`_last_successful_watermark()` 只读取 **status=ok** 的历史 JobRun，因此断网期间失败的轮询
不推进水位；恢复后首次成功运行自动从 `watermark − watermark_overlap_minutes` 重查。
8/3 实证：12 条 11:00 CST 发布的巨潮公告于 12:00 CST 补抓入库。owner 提出的"断网期间做
后补"在 cninfo 上无需新增开发。

**逐源后补能力（受 `max_requests_per_run` 制约）**：

| 源 | 每轮请求预算 | 后补能力 |
|---|---:|---|
| `cninfo`（官方公告，主源） | 60 | 天然成立，已验证 |
| `sina_company_news` | 20（10 symbol） | 部分，长缺口下覆盖不足 |
| `akshare_ths`（滚动快讯流） | **1** | 最弱：单次请求 + 滚动窗口，缺口期快讯大概率永久丢失 |

**v2 工作项（观察窗结束、8/6 验收后执行；窗口内一律不改冻结配置）**：

1. **修复 `akshare_ths` 预算不相容**：全局 `max_attempts_per_request: 2` 与该源
   `max_requests_per_run: 1` 冲突——首次请求失败后的重试即为第 2 次请求，撞破预算并被
   **误报**为 `request_budget_exhausted`（实际非限流、非封禁、非超发）。同一个配置数字
   同时制造了"失败"与"错误归因"两个症状。修复须同时覆盖：预算语义（重试是否计入）、
   错误码归因、以及提高该源预算使断网后能翻页补抓。
2. **周一调度调整**：按 owner 的已知断网时段调整周一早盘槽位（避免在确定不可达的窗口内
   空耗请求预算），并在预期恢复点后立即安排一次补抓轮询。调整以 `p4_news_poll_v2.yaml`
   新版本登记，不改 v1。
3. **补抓可观测性**：JobRun 须区分"常规增量"与"缺口后补"，记录补抓覆盖的时间跨度与条数，
   供验收与日报引用。

**⚠ 后补的边界（不可逾越，与铁律 2 一致）**：后补恢复的是**数据完整性**，不是**决策时效性**。
补抓行的 `available_time` 必须是真实的抓取落库时刻，**绝不回填为发布时刻**。因此：
研究、回测与复盘可视为数据完整；而 P4.3/P4.4 对补抓事件仍按**迟到事件**处理
（见上文第 2、3 条），P4.6 仍须分别报告"全窗口"与"剔除缺口窗口"两组结果。
换言之，周一 09:30 发生、10:00 才被系统发现的事件，对决策而言就是 10:00 的事件——
这不是缺陷，这是诚实。

**保留的 P4.3 前置门（仅剩一项）**：上述 v2 第 1 项修复完成并验证。
若 owner 日后改变宿主决定，本节按新版本重新登记。

**验收**：连续 3 个交易日运行，零重复、失败如实记录；`available_time` 100% 为抓取时刻；
巨潮三个交易日实际插入数逐日列出且每天 > 0；spike 报告哈希写入本文档。

### ⚠ P4.1 观察窗 Day 2 发现：cninfo 翻页水位死锁（2026-08-04，独立复核实测；缺陷真实、数据未损）

- **现象**：8/4 CST 全天 57 次运行仅 16 次 ok，41 次 failed 全部为 cninfo
  `pagination_incomplete`，且持续中。同日 cninfo 插入 973+ 条（半年报季公告洪峰）。
- **机制（死锁螺旋）**：水位仅由 status=ok 的运行推进；公告洪峰使翻页需求超出每轮请求
  预算（实测每轮固定 25 请求）→ 翻页不完整 → 源判失败 → 整个 JobRun failed → 水位不推进
  → 下一轮从同一水位面对更大积压 → 永久失败。实测水位冻结于 `2026-08-03T13:37 UTC`
  （=21:37 CST），最后一次 ok 为 8/4 07:30 CST。**子疑点**：07:30 的 ok 运行为何未把水位
  推进到当时——`_last_successful_watermark` 的读取/写入语义需在 v2 单独调查。
- **数据完整性验证（关键结论）**：按发布时段（CST）核查水位冻结点之后的全部公告——
  发布 18:xx–21:xx（昨晚）、00:xx（凌晨批 815 条）、07:xx–20:xx（今日各时段）**覆盖连续、
  无洞**，冻结点后发布且已入库共 991 条。新页优先 + `url/content_hash` 幂等使重叠重抓
  事实上保住了完整性；代价是运行状态全红、请求大量重复、凌晨批入库拖尾 12+ 小时
  （00:xx 发布批从 08:00 入至 20:30）。**若洪峰更大，25 请求/轮的翻页地平线可能真正开始
  漏条**——这是实测出的容量边界。
- **观察窗纪律维持**：不改冻结配置、不补抓、不人工干预。洪峰日暴露容量缺陷正是三交易日
  观察的目的；Day 2 的失败状态如实计入验收报告覆盖率章节，根因归类新增
  「翻页容量/水位死锁」。
- **8/6 验收影响**：cninfo 逐日插入 >0 硬门无碍（8/4 已 973+）；去重/PIT 无碍。
- **v2 工作项追加（优先级置顶，高于 ths 预算项）**：
  ① 水位推进语义修复——部分成功时推进至"已完整翻页的时间边界"，或失败运行亦持久化
  已验证水位（同时查清 07:30 ok 未推水位的子缺陷）；② 翻页预算按洪峰日容量重估
  （参考实测：单日 973+ 条、凌晨单批 815 条）；③ JobRun 状态语义分级——"数据完整但
  翻页超限"应可与"抓取真失败"区分，避免状态全红掩盖真实故障。

### P4.1 三交易日验收正式裁定：不通过（2026-08-06，独立复核）

- **判定：维持冻结报告 `P4.1-news-acceptance-20260805.json` 的 `all_pass=false`，验收不通过。**
  不重跑观察窗"洗绿"，不放宽任何门。判定为终局，报告字节不得改写。
- **绿的部分（已独立复验）**：数据完整性门全绿——逐源逐日插入门、来源核算
  `source_accounting_ok=true`、插入数对账 `inserted_counts_reconcile=true`、水位冻结点后
  发布连续性本地严格计数 991/991 且与外部见证一致（`external_attestation_reproduced_by_local_count=true`）、
  外部见证不越权（`does_not_override_gate=true`）、三日 `news_items` 共 5164 行、PIT/去重无破坏、
  SIMULATE 三闸全程 false。**数据没丢，丢的是运行健康。**
- **红的部分**：覆盖率/运行健康门失败，根因归类完备（`root_cause_accounting_complete=true`，
  无未归类残差）：翻页容量/水位死锁 112、主机不可达（周一人为断网时段）3、ths 重试预算
  语义缺陷 6、上游不可用 6。死锁与预算属确定性设计缺陷，非偶发，故判红成立且必要。
- **07:30 子疑点结案**：Day 2 遗留的"07:30 ok 未推水位"已定位为第三个时区缺陷——cninfo
  查询窗口终日取 `now.date()` 而运行时钟为 UTC，07:30 CST（=UTC 前一日 23:30）的轮次
  只查询到前一 CST 日，属查询窗口语义缺陷，已记入冻结报告 `watermark_semantics_0730`。
- **v2 整改单（固定优先序，先预注册后实施）**：① 水位推进语义——部分成功持久化已验证
  水位边界；② 翻页预算按洪峰容量重估（实测参考：单日 973+ 条、凌晨单批 815 条）；
  ③ JobRun 状态分级——"数据完整但翻页超限"与"抓取真失败"分离；④ cninfo 查询窗口
  改为 CST 交易日语义（修 UTC 终日缺陷）；⑤ ths 重试预算语义修复；⑥ 周一 09:30 人为
  断网时段调度让位 + 恢复后后补（owner 已确认断网为人为固定操作）。
- **复验规则**：v2 全部落地后以冻结配置重开三交易日观察窗，验收门与本轮完全一致。
  在 P4.1 复验通过前 **P4.2b（生产接线）与 P4.3 维持锁定**；P4.2a 评测线为离线只读
  冻结数据，独立推进，不受本裁定阻塞。

### P4.1 v2 整改预注册记录（2026-08-06，尚未实施）

- 版本化配置为 `config/p4_news_poll_v2.yaml`，SHA-256
  `a76a1cd9f1afd021de4d343a6550a1eb05ddad1b14d8d39cbaae2659574a5834`；独立预注册收据
  `config/p4_news_poll_v2.preregistration.json`，SHA-256
  `701555b89e65c830ebf9b6f00557e815654c766452ca2dcf9f16759626a55587`。收据绑定 v1 冻结
  配置 `d0dcd665…aa790b` 与终局红报告 `268854fb…ede6`；禁止覆盖或重跑 v1 结果“洗绿”。
- 六项整改顺序不可交换：① 巨潮逐 column 已验证水位；② 洪峰容量预算；③ JobRun
  `ok/degraded/failed` 分级；④ 巨潮查询窗口使用 `Asia/Shanghai` 日期；⑤ THS 逻辑请求与
  物理重试预算分离并保留原始错误；⑥ 周一宿主缺口让位与恢复点后补。参数或顺序变化必须
  另发配置版本。
- 水位按 `sse/szse` 独立读取和提交；只有 `checkpoint_committed=true` 的 `ok/degraded`
  column 可供下一轮读取。完整 column 推进至本轮最新已观测时刻；不完整 column 不推进，
  但已抓行仍按双键幂等落库，且不得阻止另一 column 完成与推进。顶层 `failed` 永不提供水位。
- 巨潮冻结为 30 行/页、每 column 40 页、每轮最多 80 个逻辑请求与 160 次物理尝试；容量
  为每 column 1,200 行、两 column 合计 2,400 行，高于实测单批 815 与单日 973。页限仍未
  闭合时须标 `degraded`，不得伪报 `empty/ok`；`degraded` 仅表示关键覆盖未闭合，是终态但
  不计作复验运行健康通过。非关键源失败只在 source stats 独立披露，不将顶层 JobRun 无谓
  改红；顶层 `ok` 的含义固定为“关键覆盖完整且安全态不变”。
- 巨潮 `seDate` 起止均由 UTC 时刻先转换为 `Asia/Shanghai` 后取日期，明确禁止
  `UTC now.date()`。THS 冻结 3 个逻辑页请求、最多 6 次物理尝试；重试不重复占逻辑预算，
  但占物理预算，物理预算不足只抑制重试并保留原 transport/HTTP 错误，不得改写成限流或封禁。
  `degraded` 的数据库 `error` 列固定为 `NULL`，`failed` 只保留有界异常摘要；二者的可机读
  定位信息统一写入 `stats.terminal_diagnostics`，至少含 `code/source/constraint`，禁止原始
  payload 与秘密进入诊断字段。
- 下一观察窗冻结为 2026-08-10/11/12（CST）；已于 `2026-08-06T09:27:21Z`
  对上交所《2026 年部分节假日休市安排》（上证公告〔2025〕45 号）重新核对，三日均为连续开市日。
  周一预期让位 09:00/09:30/09:40，09:50
  以 `coverage_gap_catchup` 运行，因此三日预期槽为 `61/64/64`（合计 189）；后补行的
  `available_time` 仍为真实写锁内落库时刻，只恢复完整性、不恢复时效性。复验继续要求全部
  预期槽 JobRun 为 `ok`、逐日巨潮插入 >0、PIT/去重/TLS/来源核算/根因核算与交易安全全绿。
- 当前只完成**预注册**，未修改 v1、未接入 scheduler、未运行 v2 抓取。P4.2a 仅离线评测
  可继续；**P4.2b 与 P4.3 仍锁定**，直至 v2 实现和三交易日复验通过独立复核。

> ⚠ DEVIATION（P4.1-v2-⑤，2026-08-06）：预注册只写明 THS `catchup_floor =
> last_successful_item_published_at_minus_overlap`，但没有冻结可执行的 overlap 数值。实现未擅自
> 复用巨潮的 30 分钟重叠或新增事后参数：v2 仍严格使用 3 个逻辑页、最多 6 次物理尝试，只有
> 读到空页才判本轮闭合；第 3 页仍非空（包括发布时间缺失、无法证明已越过 floor 的记录）统一
> 标记 source-level `degraded / catchup_incomplete`。瞬态网络、HTTP 与 schema 错误不进入该
> 降级口径，继续保留原始错误并 fail-closed。待 owner 另发版本化数值合同前，不宣称实现了
> `older_than_catchup_floor` 提前闭合。

- **P4.1-v2-⑥ code-complete（2026-08-06）**：v1 trigger 保持原样；v2 trigger 在周一
  明确让位 `09:00/09:30/09:40`，仅保留 `09:50` 恢复轮，枚举结果为
  `2026-08-10/11/12 = 61/64/64`。周一 `[09:50,10:00)` 的 JobRun 记为
  `coverage_gap_catchup`，其余为 `regular_incremental`；恢复轮按 column 记录
  `verified_watermark_floor_utc → actual poll_started_at` 区间及逐源抓取/插入/去重/标记计数。
  v2 新落库行写入 `preceded_by_coverage_gap`，但 `available_time` 仍只取真实写锁内 UTC
  时刻；重放不会覆盖首次时刻或标记。恢复轮若巨潮页限未闭合，诊断为
  `recovery_catchup_incomplete`，不得伪报 `ok`。

> ⚠ DEVIATION（P4.1-v2-⑥，2026-08-06）：预注册固定了三个让位槽与 `09:50` 恢复点，
> 但未冻结连续缺口的结构化字段名及精确 span 起点。实现未改配置字节，统一以“首个让位槽
> `09:00 CST` 到真实恢复轮 `poll_started_at`”作为派生跨度，并以
> `span_basis=first_suppressed_slot_to_actual_poll_started_at` 明示。该跨度用于覆盖审计，不会
> 回拨事件 `available_time`，也不宣称已恢复缺口期间的决策时效性。

### ⚠ DEVIATION：P4.1 v2.1 受控探测推翻双 column 容量假设（2026-08-06，code-ready 未激活）

- 约 `2026-08-06 19:12 CST` 的只读受控探测使用严格 TLS、零自动重试、零数据库写入，
  共执行 2 次聚合查询和 8 次逐日查询。逐日 `sse/szse` 的总数与首条公告 ID 完全一致：
  `08-03=477/1225456370`、`08-04=1533/1225458599`、`08-05=1129/1225460865`、
  `08-06=1097/1225461533`；`08-03~08-06` 聚合查询仅差 1 行（`4236/4235`），且两列
  返回相同的前五条深市/创业板公告。该证据否定了旧 v2 将 `sse/szse` 当成互补数据域、
  各 1,200 行且合计 2,400 行的容量假设。
- 旧 `config/p4_news_poll_v2.yaml`（SHA-256
  `a76a1cd9f1afd021de4d343a6550a1eb05ddad1b14d8d39cbaae2659574a5834`）在激活前即被
  标记 `superseded_before_activation`，禁止启动。替代合同为
  `config/p4_news_poll_v2_1.yaml`（SHA-256
  `9d56e137baf10bd0858723a93aff02c57bf7b35f8705f1817b16a89ec615183f`），预注册收据
  SHA-256 `485f710398698c1692d8afd8de6cd06c71ddf2fbdf42713c6bd4defc4bdfd84b`，受控探测证据
  SHA-256 `5267be93ebcf9dd68bc8d4644ca34da15f834f5e7c6a49e76faa65034b825bdc`。
- v2.1 只查询 canonical `szse`，按 `Asia/Shanghai` 自然日切片。闭合历史日从第一页完整
  对账；当日增量以最近已提交的真实观测时刻减 30 分钟为 floor，强制请求 `DESC` 并逐页验证
  页内和跨页时间单调性，直到页最早时刻越过 floor。闭合日 checkpoint 与当日 observed high
  分离：当日轮只推进真实观测高点，不把未闭合日期伪装成闭合 checkpoint；跨日每轮最多处理
  “前一闭合日 + 当前日”两个切片。非空 `published_at` 必须换算后属于请求的 CST 日期，否则
  `cninfo_slice_date_contract_violated` 且不提交 checkpoint；空 `published_at` 仍可落库，但不参与
  排序/floor 证明，只有分页完整闭合才能推进日期 checkpoint。
- 洪峰合同改为 canonical 单列每自然日最多 80 页（30 行/页，即 2,400 行），每轮最多
  2 个日期、160 个逻辑请求、320 次物理尝试。页限、瞬态网络、schema 或排序异常均
  fail-closed；后一个切片发生 transport/TLS/schema 失败时，前序完整切片证据仍写入失败
  JobRun，但 `failed` 行不作为下轮 checkpoint，安全代价是重抓。若后续切片仅命中页限，顶层
  `degraded` 可保留前序已完整闭合的 checkpoint；候选始终遵守 URL/content-hash 双去重且重放
  不覆盖首次 `available_time`。
- 激活语义拆为两个独立门：`code_ready=true` 只表示离线实现与 fixture 可复核，
  `scheduler_activated=false` 仍阻止任何定时运行。首次积压迁移只能经
  `run_news_poll_v2_1_initial_migration` 加载与本配置 SHA 绑定的人工授权收据；迁移完成后，
  `run_news_poll_v2_1_incremental_validation` 还要求收据明确
  `initial_backlog_migration_complete=true`。不得通过临时改常量或用手工收据激活 scheduler。
  手工入口的 `execution_mode` 单独写入 JobRun 审计；持久化 `run_mode` 仍严格限定为
  `regular_incremental/coverage_gap_catchup`，初始积压迁移使用后者并写真实缺口标记，避免把执行
  控制面枚举误传给落库协议。
- 本记录不表示 v2.1 done，也未执行网络迁移、生产数据库写入或调度切换；
  `initial_backlog_migration_complete=false`、`standard_incremental_validation_complete=false`，
  `P4.2b/P4.3` 继续锁定。运行前安全门新增硬性要求
  `paper_trading_enabled=false` 与 `futu_enable_trade=false`；不满足时须在任何抓取前失败。

### P4.1 v2.1 初始积压迁移第 1 轮实证（2026-08-09，待独立复核）

- Claude Code 独立 AI 架构审阅者提交的 create-only 授权收据仅授权一次
  `initial_backlog_migration`。执行前发现旧 v1 scheduler 仍在线，且本机 `.env` 的
  `paper_trading_enabled/futu_enable_trade` 为 `true`；因此先将旧 scheduler 可逆 bootout，
  再以进程级显式安全覆盖把七项交易不变量全部钉死。旧 plist 保留，v2.1 scheduler 未激活。
- JobRun `76931` 于 `2026-08-08T18:47:02.733273Z` 启动、
  `2026-08-08T18:48:13.724960Z` 以 `ok` 终态结束。canonical `szse` 对账完整闭合
  `2026-08-03/04` 两个 CST 自然日：分别抓取 `477/1533`、分页 `16/52`，共 68 个逻辑请求、
  68 次物理尝试、0 重试、0 失败、严格 TLS；checkpoint 从 legacy v1 lineage 推进到
  `2026-08-04`。
- 本轮巨潮新增 `646` 行（按公告 CST 日期为 `108/538`），生产 `news_items` 从 `8216`
  增至 `8862`。646 行均绑定 JobRun 76931、均带 `coverage_gap_catchup` 与
  `preceded_by_coverage_gap=true`，`available_time` 为本轮真实写锁时刻；全库 URL/hash
  重复组仍为 0，SQLite `quick_check=ok`。交易表前后维持 1 个提案/1 个 SIMULATE 订单，
  非 SIMULATE 订单为 0。
- 冻结证据：
  `docs/phase4/reports/P4.1-v2.1-initial-migration-evidence-20260809.json`，SHA-256
  `da60601d2a12a1f6d6c93d47cf47a4a01f1228ed4ef3d7d62ba477d7a2838685`；其中绑定原始
  JobRun stats SHA-256 `687ae47c…add690` 与 canonical JSON SHA-256
  `0156ce31…faee8`。
- ⚠ **DEVIATION（操作入口初始化）**：第一次直接调用手工入口时，因尚未执行
  `register_builtin_jobs()` 而在 JobRun、网络和数据库写入之前以 `KeyError('news_poll')`
  退出。完成仅注册 JobSpec 的初始化后才执行上述唯一真实轮次；失败前置调用未消耗授权网络轮次。
- 本轮成功**不等于积压迁移完成**：不可变的每轮 2 日上限只推进到 08-04；
  `initial_backlog_migration_complete=false`、`standard_incremental_validation_complete=false`，
  scheduler/P4.2b/P4.3 继续锁定。任何下一轮迁移都须先由独立复核者验签 JobRun 76931，
  再签发新的窄范围授权收据；禁止复用本轮收据。

### P4.1 v2.1 初始积压迁移第 2 轮实证（2026-08-09，待独立复核）

- 第 1 轮复核提出的 F-2 无法由旧 JobRun 统计事后补证，因此在联网前先以纯观测改动
  `859067d` 增加逐切片 disposition 统计。候选顺序、URL 优先/content-hash 次之的既有去重、
  SQLite 写锁与 PIT 时间、请求预算及 checkpoint 语义均未改变；切片计数与源级汇总不相等时
  fail-closed。定向测试 26 项、全仓 Ruff、strict mypy 157 files 与全量 pytest 均通过。
- 收据 `P4.1-v2.1-initial-migration-authorization-round2-20260809.json`（SHA-256
  `9d704e4b523d1c52cc5c567ff66c4c661b8324d5034130292f629853d359cfa3`）仅使用一次。
  JobRun `76932` 于 `2026-08-08T19:48:50.162735Z` 启动、
  `2026-08-08T19:50:11.168202Z` 以 `ok` 终态结束，严格闭合 `2026-08-05/06`：分别抓取
  `1129/1193`，分页 `38/40`，共 78 个逻辑请求与 78 次物理尝试，0 重试、0 失败、严格 TLS；
  checkpoint 从 `2026-08-04` 推进到 `2026-08-06`。
- F-2 已机器闭合：08-05 为 `1129 = 135 inserted + 992 duplicate_url + 2
  duplicate_content_hash + 0 filtered`；08-06 为 `1193 = 190 + 1002 + 1 + 0`。源级汇总与
  两切片合计逐字段相等，`disposition_identity_valid=true`。
- F-1 已完整披露且本轮无差异：owner 于运行前把 `.env` 六项持久化安全配置改为
  `research/false`（mtime `2026-08-08T19:37:01.175468Z`，早于运行）；进程前后七项有效值
  均为安全态，`unlock_trade` 继续由代码永久屏蔽。交易表维持 1 个提案/1 个 SIMULATE 订单，
  非 SIMULATE 订单 0；scheduler 仍未加载。
- 本轮新增巨潮 `325` 行，生产 `news_items` 从 `8862` 增至 `9187`。325 行均绑定 JobRun
  76932、`coverage_gap_catchup` 与真实本轮 `available_time`；PIT 异常 0、全库 URL/hash 重复组
  0、SQLite `quick_check=ok`。create-only 证据为
  `docs/phase4/reports/P4.1-v2.1-initial-migration-round2-evidence-20260809.json`，SHA-256
  `bd5d5c731f61efb1bf8818a5dc7d188ad0e7fd0a136aa6a64153006d76a7616a`。
- 本轮仍**不等于积压迁移完成**：08-07/08 尚未迁移；
  `initial_backlog_migration_complete=false`、`standard_incremental_validation_complete=false`，
  scheduler/P4.2b/P4.3 继续锁定。必须先独立复核 JobRun 76932 与上述证据，再签发第 3 轮单次
  收据；禁止连跑或复用本轮收据。

### P4.1 v2.1 初始积压迁移第 3 轮实证（2026-08-09，待独立复核）

- 联网前先以纯观测提交 `476455c` 加固证据冻结器：合法零公告切片允许
  `newest_observed_at_utc=null`，但非空值仍严格解析；证据 CLI 的 checkpoint/切片/入口/单轮
  范围必须与授权收据逐字段相等；JobRun 全来源 inserted 与带 run marker 的落库行按 source
  精确对账，PIT flush/commit 时序也按来源闭合。该提交未改抓取、预算、去重、PIT 或
  checkpoint 决策路径；13 项定向测试、全仓 Ruff、strict mypy 157 files 与全量 pytest 全绿，
  并经独立只读复核 `GO / zero blocker`。
- 第 3 轮收据
  `P4.1-v2.1-initial-migration-authorization-round3-20260809.json`（SHA-256
  `a46dc4abf9ca67340cd40314aa9f5d8f771e740be78cbbf9eb2e06750f24219b`）仅使用一次。
  JobRun `76933` 于 `2026-08-09T05:04:39.523793Z` 启动、
  `2026-08-09T05:06:07.636323Z` 以 `ok / error=null` 终态结束，严格闭合
  `2026-08-07/08`：分别抓取 `1194/1329`、分页 `40/45`，巨潮共 85 个逻辑请求与 85 次
  物理尝试，0 重试、0 失败、严格 TLS；checkpoint 从 `2026-08-06` 推进到
  `2026-08-08`，`partial_checkpoint=false`。
- 逐切片 disposition 恒等式闭合：08-07 为
  `1194 = 119 inserted + 1073 duplicate_url + 2 duplicate_content_hash + 0 filtered`；
  08-08 为 `1329 = 579 + 746 + 4 + 0`。两切片合计与巨潮源级
  `2523/698/1819/6/0` 逐字段相等，未因周六日期扩窗或填充数据。
- 本轮全来源真实新增 `720` 行：巨潮 `698`、同花顺 `20`、新浪 `2`；带 JobRun 76933
  marker 的行按来源精确同数。生产 `news_items` 从 `9187` 增至 `9907`，720 行全部保留
  `coverage_gap_catchup`、`preceded_by_coverage_gap=true` 与本轮真实 `available_time`；PIT
  异常 0、全库 URL/hash 重复组 0、SQLite `quick_check=ok`。交易表仍为 1 个提案/1 个
  SIMULATE 订单，非 SIMULATE 订单 0；持久化 `.env` 与进程安全值无差异，scheduler 未加载。
- create-only 证据为
  `docs/phase4/reports/P4.1-v2.1-initial-migration-round3-evidence-20260809.json`，SHA-256
  `57f9b99e99358b5e8c596485774702858e5c572cb3116407a279d0195b044318`；绑定 JobRun stats
  raw SHA-256 `f881de16412a364dbcb008a6b0ed09566cd6e38eb743653966fd32b93e052f26` 与 canonical
  JSON SHA-256 `beb5f838c7762cdc958d879c0bc17a7391da6916780d2a88b95ad8ace5ddc111`。
- 机器证据已确认“截至前一上海自然日的闭合日期全部对账”，但 operator 不自宣告完成：
  `initial_backlog_migration_complete=false`、`standard_incremental_validation_complete=false`，
  scheduler/P4.2b/P4.3 继续锁定。下一步只能先独立复核 JobRun 76933 与本证据；复核通过后再签发
  显式 `initial_backlog_migration_complete=true` 的标准增量验证收据，禁止自行运行增量验证或
  重部署 scheduler。

### P4.2a 模型成本复审与 v1.7 选择准则预注册（2026-08-04，held-out 解锁前）

- **动机**：held-out 一次性，验证哪个模型就应是生产模型。owner 提出试 `qwen3.7-flash`
  并对比 `deepseek-v4-flash`。
- **定价实查（2026-08-04 官网）**：qwen3.6-plus ¥2/¥12 每 M；**qwen3.7-flash ¥0.2/¥0.8**
  （显式缓存命中 ¥0.02）；deepseek-v4-flash $0.14/$0.28（≈¥1.0/¥2.0），但官方预告
  **高峰时段全项 2 倍计价，高峰=北京 9:00–12:00 与 14:00–18:00——与 A 股交易时段完全重合**，
  对本工作负载实际约 ¥2.0/¥4.0，叠加第二供应商的账号/合同/运维成本。结论：本负载下
  qwen3.7-flash 严格优于 deepseek-v4-flash，DeepSeek 不再纳入本轮评估。
- **能力探测（复核方，冻结 v1.5 prompt + 候选切片表示，11 个判别性样本）**：
  8 个公告难例二分类 6/8 正确——3.6-flash 全错的 6 条（306/345/287/258/304/336）全部判对；
  309 漏判与 plus 相同（plus 在 v1.6 正式轮同为该条 FN）；250 误报为 2（plus 判对，
  为相对 plus 的唯一退步）。宏观诱饵 0/3 上钩；JSON 11/11 合规；延迟均值 1.8s（plus 3.1s）。
- **v1.7 选择准则（先于任何 v1.7 正式结果固定）**：以 `qwen3.7-flash` + **不变的 v1.5
  prompt** 预注册 v1.7 合同并跑正式 dev60。**若 60/60 零失败且 materiality ≥0.80、
  symbol ≥0.95 两门全过 → 冻结 3.7-flash 进 held-out 与生产**（成本约 ¥12/月）；
  **任一不过 → 维持 v1.6（plus）冻结不变**（约 ¥125/月），不做第三轮模型挑选。
  不以"哪个 dev 分高"择优——避免在 dev 上做模型过拟合选择；flash 通过即取其成本优势。
- 时限约束：v1.7 全流程（预注册→dev60→若过则重冻结）须在 2026-08-06 00:10 CST
  held-out 解锁前完成；未完成则按 v1.6 执行 held-out。

### P4.2a v1.7 正式模型选择结果（2026-08-04）

- **预注册先于结果**：运行时合同、唯一轮次、绝对门、旧 plus 全链路哈希、费率和选择状态机
  已先独立提交 `e9567ef`；抽取合同 SHA `68474e4b…b701`，评测设计 SHA
  `57e04f99…39c9`。正式轮固定且仅执行 `v1.7-r1`，未重试、未运行第三个模型。
- **机械门全过并选择 flash**：60/60 成功、0 失败；materiality≥2 的 AI dev
  **模型间一致率**为 `16/17 = 0.9411764706`（FP=1、FN=0），symbol exact-set 为
  `57/60 = 0.95`。后者恰等于预注册门，按未舍入原值 `>=0.95` 通过；没有拿 plus
  的相对得分或成本反向调整门槛。最终 `decision=select_candidate`，
  `selected_model=qwen3.7-flash`。
- **与 v1.6 plus 同输入对照**：60 条逐 ID 的 legacy input、candidate input 与 text
  三个 SHA 均相等；plus 为 60/60、materiality `15/17=0.8823529412`、symbol
  `59/60=0.9833333333`。flash 平均延迟 `1564.43ms`，plus `3174.72ms`；
  两轮 prompt tokens 均为 `209051`，completion 分别为 `6778/6757`。
- **成本按冻结公式实算**（cache=false，15,000 calls/月）：flash 本轮 `¥0.0472326`、
  月估 `¥11.80815`；plus 本轮 `¥0.499186`、月估 `¥124.7965`。成本只用于通过绝对门后的
  预注册选择，不参与指标判定。
- **正式证据**：predictions `533d2e00…edb4`、manifest `d91063fb…74fa`、report
  `507ada91…5a9c`；本地晋升后的 dev-final predictions 与正式 predictions 同 SHA
  `533d2e00…edb4`，manifest `c246bda5…2df2`；新 freeze receipt
  `1b2aae88…31a1`。选择 outcome 为
  `docs/phase4/eval/P4.2a-model-selection-v1.7.json`，SHA `38f20f4e…59e0`。
- **唯一生效冻结**：旧 plus receipt SHA 仍为 `9adab49b…3f68`、字节未改，但 outcome
  验证器已证明它不再可用于 held-out；新 v1.7 receipt 可独立重验为
  `qwen3.7-flash / 60 success / 0 failure`。两套 receipt 共存不等于双重授权。
- **隔离证据**：P4.1 冻结配置仍为 `d0dcd665…aa790b`；生产 DB
  `mode=ro/query_only/total_changes=0`，提案/委托仍 1/1、非 SIMULATE 为 0；
  60 行原始 prompt/transport payload 持久化数为 0，held-out 未访问，P4.2b 未解锁。
- ⚠ **DEVIATION**：首次 CLI 启动因未给 Python 加仓库模块路径，在 import 阶段以
  `ModuleNotFoundError: scripts` 退出；该次为 0 网络请求、0 模型调用、0 产物。随后在不改
  合同与代码的前提下补 `PYTHONPATH=.` 启动唯一正式轮；没有补跑任何样本。

### P4.2a held-out 解锁前运行加固（2026-08-05，纯代码/测试，未读 held-out）

- 复核方提供的 `scripts/build_p4_2a_adjudication_ui.py` 保留并加固，没有重建另一套 UI。
  入口现在逐字绑定冻结盲样本与显式 AI 草稿，严格拒绝重复 JSON key、非有限数、重复 ID、
  顺序/不可变字段漂移、预测或抽样字段泄漏；AI 草稿 `notes` 必须保持为空，避免预锚定人工
  裁定。页面每条均须由与起草 AI 身份不同的人工裁定者明确确认或修改，任何字段变化会撤销
  该条确认；导出 `.adjudicated.jsonl` 的 `gold` 是裁定终值，`draft_gold` 与
  `adjudication` 是逐条溯源证据。
- `combine-owner` 明确消费 `.adjudicated.jsonl + 同一份 AI draft`，重新计算
  `adjudicated_changed/changed_fields` 后才生成冻结 canonical owner 文件。最终 one-shot
  evaluator 还必须显式接收并独立复验这两份源文件，报告完整文件 SHA、逐条裁定身份摘要
  SHA、确认/修改计数与时间范围；不能只信 canonical 文件。两入口均补齐
  `2026-08-06 00:10 CST` 时间锁，锁前连输入文件都不得读取。
- dev60 仍是 AI 起草开发信号：最终报告仅称
  `materiality_model_interagreement`；只有 held-out40 的人工裁定结果可称
  `materiality_precision`。all100 materiality 只作 mixed-reference diagnostic；
  symbol exact-set 继续分报 dev/test/all，并同时披露各 split 标注来源。
- ⚠ **DEVIATION（候选全集大于单批合同上限，范围与预算未改）**：8/4–8/5 候选全集已超过
  合同 `max_items_per_run=2000`。one-shot runner 改为在**一个持久化 inference started
  状态**下，按冻结候选 ID 原始升序做确定性连续分批，每批 `<=2000`；每个候选最多一次模型
  调用、零自动重试，最终只生成一份全集 predictions/manifest/selection。分批不提高合同
  上限、不删样本、不换样本、不改变选择 seed 或测试窗口。若任一中间批发生进程中断或模型
  失败导致全集无法闭合，本 held-out 轮即按 one-shot 合同作废，禁止续跑成功项；须登记新设计
  与新测试集后另行复核，不能把恢复机制变成隐式重试。
- 本节只实现入口和离线 fixture 测试；截至登记时仍处 8/5 观察窗，未物化候选、未调用
  held-out 模型、未生成盲文件、未打开 owner 评测门，P4.2b 继续锁定。

### P4.2a held-out one-shot 材料化熔断裁定与新轮次授权（2026-08-06，独立复核实测复现）

- **事件**：8/6 00:10 CST 时间锁开启后，one-shot runner 在候选材料化安全门（
  `inference_started` 之前）以 `GoldSampleError: pdftotext output is shorter than the
  minimum extracted-character gate` 熔断。零模型调用、零自动重试、零产物写出、盲文件
  未生成、held-out 未被任何模型消费。**熔断本身是冻结纪律的正确执行，运行方停机待独立
  裁定的处置正确。**
- **根因（只读复现，材料化行序逐条重放）**：候选池 3070 行，其中 cninfo 公告体 1982 行。
  前 108 行（含公告体 53 行）即出现 5 例确定性失败——3 条 pdftotext 抽取字符低于最短
  门限（id 1172《独立董事专门会议决议》＝当轮熔断首例、id 1203/1205 提名与薪酬委员会
  意见，均为一页盖章/扫描件形态），2 条超 8 MiB 下载上界（id 1175/1193，可转债信用评级
  报告）。公告体失败率 5/53≈9.4%，外推全池约 190 条。两类失败均为**文档内容的确定性
  属性**（抽取字符数、文件体积），非网络瞬态；按现行"单条失败=整批致命"语义，该池
  **必然熔断**，与模型/prompt/合同无关。
- **定性**：heldout 材料化的**候选资格语义缺陷**——把逐条资格属性当作整批断言。v1.7
  冻结收据（`1b2aae88…31a1`）、prompt 与抽取合同 SHA 不受影响，禁止借机改动。
- **裁定（新轮次先决条件，全部先预注册后执行）**：
  1. 公告体不可材料化 = **候选资格不合格**：逐条剔除出资格池并落不可变记录
     `{news_item_id, url, reason ∈ {pdf_text_below_min_char_gate, pdf_exceeds_size_bound},
     measured_value, gate_value, pdf_sha256(可得时)}`；冻结候选 artifact 必须同时含
     全量/合格/剔除三层清单与按理由计数。剔除只依据输入可材料化性——与任何预测、标注、
     结果无关，属资格过滤而非幸存者偏差。
  2. **门限一个不改**：最短抽取字符门与 8 MiB 上界原值保留。这是处理语义修正而非放宽
     （先例：v1.3 evidence_span 空白规格裁定）。
  3. 剔除仅允许基于**可测的文档内容属性**。下载瞬态失败（超时/5xx/连接错误）不得归入
     ineligible，仍 fail-closed——"资格"必须是文档的性质，不是网络的性质。
  4. 40 样本抽样只在合格池上执行；合格池不足以支撑既定分层 → fail-closed，不降样本量、
     不改分层、不改 seed 规则。
  5. 本轮登记为 `materialization_failed_no_inference`，失败记录保留不改写。授权：材料化
     修复 + 离线 fixture 测试落地、剔除语义以**新评测设计版本（新 SHA）**预注册后，
     登记唯一一次新 one-shot 轮次；评测窗口、抽样 seed 规则、冻结模型/prompt/合同不变。
- **披露**：本裁定的只读复现使复核方接触了 108 行候选输入（含 5 条失败项的标题/URL）。
  复核方既非预测模型也非金标准标注者，剔除判据只看输入可材料化性，不构成结果性泄漏；
  人工裁定者本就将在盲标阶段阅读入选样本全文。

### P4.2a held-out 材料化资格语义 v1.7 预注册与离线实现（2026-08-06，待唯一正式轮次）

- 已将熔断轮作为不可变历史证据登记为
  `docs/phase4/eval/P4.2a-heldout-materialization-failure-v1.6-r1.json`，SHA-256
  `e14650690f3a1efbe6b455cc0a2d043c08c296ff88433cf14395f40f001ea6db`；状态固定为
  `materialization_failed_no_inference`，记录零 `inference_started`、零模型调用、零预测/盲标
  产物，禁止改写成成功轮。
- 新评测设计预注册为 `config/p4_event_evaluation_v1_7.yaml`，SHA-256
  `4c7964ad547820f5672631939af93978f11cb9f91e5921087770ac7d0d79bec1`。它逐字节继承
  v1.6 的冻结候选窗口、抽样 seed/数量、阈值与标注 provenance，并继续绑定
  `qwen3.7-flash`、prompt `v1_5`、抽取合同 SHA `68474e4b…b701`、freeze receipt
  `1b2aae88…31a1` 和 model-selection outcome `38f20f4e…59e0`；本修复没有改模型、prompt、
  schema、门限、分批上限或重试规则。
- 材料化改为有序三层闭合分区 `all = eligible ⊔ ineligible`。仅
  `pdf_text_below_min_char_gate` 与 `pdf_exceeds_size_bound` 两类确定性文档属性允许逐条剔除；
  每条记录绑定 `news_item_id/url/reason/measured_value/gate_value/pdf_sha256(可得时)`。
  80 字与 8 MiB 原门限保持不变且边界值仍合格；连接错误、超时、5xx、无效/负
  `Content-Length` 以及未知 PDF/解析错误继续整批 fail closed，不能伪装成资格不合格。
- 合格 inputs JSONL 与材料化 manifest 使用专属目录的 create-only 原子发布；manifest 同时绑定
  全量/合格/剔除三层有序清单、原因计数、设计/合同/回执血缘与 inputs 字节 SHA。半套文件、
  分区重叠/缺口、顺序或计数漂移、文件 SHA 漂移均在首个模型调用前拒绝，且不得二次抓取来
  “修复”既有证据。
- runner、零模型 finalizer、owner 选样与最终 evaluator 统一从该 manifest 派生合格 DB 子集；
  `inference_started`、唯一终态、prediction manifest、selection manifest 和最终评测报告必须
  绑定同一份 manifest 路径/SHA/三层计数。被剔除 ID 不得出现在预测、预测正类池、盲标 40 条
  或最终评测；合格预测正类不足 40 条时按原规则失败，不降样本量、不换 seed。
- 离线 fixture 已覆盖短文本/超大 PDF、恰好门限、瞬态下载失败、半套/篡改 manifest、合格池
  不足、零模型 finalizer、started/terminal/prediction/selection 证据一致与 legacy 回归。
  **截至本节登记时，新 one-shot 尚未启动、held-out 盲文件尚未生成，P4.2a 仍未完成，
  P4.2b/P4.3 继续锁定。**完成全量质量门与本预注册独立提交后，才允许执行一次
  `heldout-v1.7-r1`；材料化及全集推理闭合后只生成盲文件并通知 owner，禁止代替人工裁定。

- ⚠ **DEVIATION（材料化预运行发现 dual-hash 身份误用传输预算，零推理）**：预注册提交后
  的首次受管材料化在第 267 份 PDF、`news_item_id=1419` 处于 `inference_started` 之前
  fail closed；材料化 bundle、state、predictions 均未生成，模型调用为 0。细化对拍证明该条
  是合格公告：冻结候选模型输入为 `14,213 <= 16,000` 字符；真正失败的是从不发给模型、仅用于
  血缘的 legacy 八字段身份 JSON（PDF 控制空白转义后 `16,549` 字符），代码误复用了传输预算
  校验。修复将“真实模型请求”和“声明身份哈希”语义分开：前者继续严格受 16,000 上限，后者
  仅按原规范 JSON 计算 SHA、不进入网络；既有 dev 双哈希字节不变。另为可能真实超过 16,000
  的合格公告加入确定性末尾前缀收缩，只满足已冻结的输入上限并同步正文长度/哈希证据，不把它
  伪装成第三类 ineligible。模型、prompt、候选切片算法、80 字/8 MiB 门、设计/合同/receipt SHA
  与 seed 均未改变；新增真实 1419 对拍与离线回归通过后，方可重新执行材料化先决条件。
- 修复提交后，材料化先决条件已在零模型进程中完整执行并原子冻结：全量候选 `3,070`、合格
  `3,013`、剔除 `57`，其中 `pdf_exceeds_size_bound=12`、
  `pdf_text_below_min_char_gate=45`。合格 inputs 为
  `docs/phase4/eval/P4.2a-heldout-materialization-v1.7/candidate-inputs.jsonl`，SHA-256
  `e550435b77c4525fb05717cf6d1c313448b9b7323b2e496f0450484a3a980f88`；三层 manifest
  SHA-256 `3808852cceecbbd384109d36a595d1704197bcaf5344f94530b5b65a5aaa7d6b`。
  只读独立复算确认三层并集闭合、互斥、ID 唯一且按 DB 升序，inputs SHA、原因计数、设计/
  合同/回执血缘均吻合。冻结时 inference state/predictions/manifest 仍不存在、模型调用 `0`、
  生产提案/委托仍为 `1/1`；下一动作只能由该字节冻结池启动唯一 inference one-shot。

### P4.2a v1.8 替换评测预注册（2026-08-09，尚未执行）

- v1.7 正式评测轮已不可变登记为失败：状态文件
  `docs/phase4/eval/P4.2a-heldout-evaluation-one-shot-v1.7.state.jsonl`，SHA-256
  `ba1eef1517fa30c37a7fcba5b171405cf537c84dbbbb70cec57f542842fdc0ff`，禁止删除、改写、
  移动或重生成。事故报告 SHA-256 为 `5182cfcf…f963`；owner 选择方案 B 的替换授权 SHA-256
  为 `2e33220f…089f`，只允许同一 held-out40、同一冻结预测的一次替换评测。
- 新设计 `config/p4_event_evaluation_v1_8.yaml` 已在实现与任何替换轮之前预注册，SHA-256
  `bb36b9713cbf70fa5293683e767655b70720a2baa9e8822bc0396c4e6284452e`。它只改变设计版本/
  血统身份以及替换轮的 create-only state/report 路径；通过 15 个 `byte_frozen` scope 逐字节
  继承 v1.7。样本、seed、窗口、数量、预测、模型、prompt、抽取合同、freeze receipt、门槛、
  指标定义与 owner gold 均保持不变。
- 代码前置预注册为四项：① 版本对从设计声明的 active contract 身份推导，并对真实错配继续
  fail closed；② v1.8 loader 与 scope-gated lineage 绑定；③ dry-run 在不计算/披露真实 held-out
  分数的前提下走到报告扩展和组装；④ 在生产 artifact root 外用结构同形合成输入完整走通
  claim → metrics → extensions → report serialization。机器可读预注册记录为
  `docs/phase4/reports/P4.2a-v1.8-replacement-preregistration-20260809.json`。
- 四项前置现已 code-complete：15-scope 连续血统只接受 v1.8/v1.7/v1.6，至 v1.5 首个
  缺 scope 边即停止；v1.7 inference state（SHA `44253bfb…be3`）与 selection manifest
  （SHA `9da50ea8…abe1`）在 v1.8 下均通过真实冻结字节复验，删任一 scope 或注入无关 SHA
  均 fail closed。真实冻结输入 dry-run 共 28 阶段，其中 27 阶段通过、1 个真实 held-out
  评分阶段明确保持 `not_run_one_shot_protected`；合成指标、报告扩展、离线诊断、报告组装、
  必填字段与内存序列化全部走通，零 DB/文件/网络/LLM 副作用。
- 生产 artifact root 外的结构同形合成预演真实走通 claim → synthetic metrics → extensions →
  diagnostics → create-only report → terminal completion，临时产物随后清除。Ruff 全仓、strict
  mypy（`src/alphapilot` 157 文件 + 变更脚本/测试）与全量 pytest（1,487 collected，1 skip）
  均通过。机器证据为
  `docs/phase4/reports/P4.2a-v1.8-replacement-readiness-20260809.json`，SHA-256
  `b35bf2a4f229ce21127b84b582de03dbe5bfdd4feead06c63487db1b28baa992`；状态仅为
  `READY_FOR_INDEPENDENT_REVIEW`。
- 本节不构成替换轮许可消耗。必须先完成代码、fixture、绑定证明、合成全路径预演与 Ruff、
  strict mypy、全量 pytest，再交独立复核；上述机器门已齐，但独立复核仍未签字。复核通过前
  不得创建 v1.8 state 或正式报告，P4.2b/P4.3 继续锁定。

### P4.2a v1.8 替换评测结果（2026-08-09，完成但指标门未通过）

- 外部独立复核以 `APPROVE_REPLACEMENT_EVALUATION` 解锁唯一替换轮，复核报告
  `docs/phase4/reports/P4.2a-v1.8-replacement-independent-review-20260809.json`，SHA-256
  `18633ea1aa63c1d9cf5358a728937403e35de5e52169637ce549152935c9fcdc`。Codex 随后仅执行
  一次，未重试；进程退出码 `2` 的合同语义为“评测已完成、指标门未通过”，不是技术失败。
- v1.8 state 恰为 `evaluation_started → evaluation_completed` 两行，SHA-256
  `c4ac90fc0d8779a78920cf9e93d9fc80aa1a8c7271bf5f5e245edb198a4da6b9`；冻结报告
  `docs/phase4/eval/reports/v1.8-replacement/P4.2a-heldout-evaluation-v1.8-replacement.json`，
  SHA-256 `c2957297e733a919bdd31c4aa4faafb50c4c4d5c0edac29f663dac03e34a3a8f`，与 state
  terminal 绑定逐字一致。
- 正式 held-out40：materiality precision `21 / (21 + 19) = 0.525 < 0.80`，为唯一失败门；
  recall `21 / 21 = 1.00`。symbol exact-set 为 held-out40 `40/40=1.00`、all100
  `97/100=0.97`；all100 symbol-bearing exact-set `74/77=0.9610`，相关 symbol 门均通过。
  独立只读复算得到相同 `TP=21 / FP=19 / TN=0 / FN=0`，结论为
  `APPROVE_RESULT_EVIDENCE`。
- **既有失败轮继续显式披露**：v1.7 state 仍为 `evaluation_started → evaluation_failed`，
  SHA-256 `ba1eef1517fa30c37a7fcba5b171405cf537c84dbbbb70cec57f542842fdc0ff`，没有删除、
  改写、移动或重生成。v1.8 替换授权现已 `1/1` 消耗，剩余 `0`，禁止再次执行。
- 生产库执行前后 size/mtime 完全一致，JobRun running `0`、交易表仍 `1 proposal / 1
  SIMULATE order / 0 non-SIMULATE`；样本、seed、预测、模型、prompt、合同、门槛与 gold 均
  未改变。机器可读结果证据为
  `docs/phase4/reports/P4.2a-v1.8-replacement-result-20260809.json`。
- 因 precision 门未通过，**P4.2a 不标 done，P4.2b/P4.3 继续锁定**。本结果只允许如实提交
  owner/独立复核；不得借此调门槛、换样本、改 prompt/model/gold，亦不得重跑。

## P4.2 LLM 事件抽取

> **拆分解锁（owner 质询后修订，2026-08-03）**：P4.2 拆为两半。**P4.2a 评测准备**即刻
> 解锁：冻结 taxonomy、抽取 prompt + 严格 JSON schema、对既有 `news_items` 真实数据做
> **离线**抽取试跑、从真实数据分层抽取 100 条金标准样本（建议 60 条取自现库存、40 条取自
> 8/4–8/5 交易日新增以覆盖公告体）、owner 标注、按标注迭代至评测门达标——全程零生产
> 写入（评测产物只进 `docs/phase4/eval/`，不建正式表、不动 scheduler、不接触发链）。
> **P4.2b 生产接线**（`news_events` 表、抽取 job、触发链）仍锁定至 P4.1 三交易日验收
> 通过。理由：标注与 prompt 迭代是关键路径且不依赖底座验收结论；生产管道必须建在已
> 验收底座上。

### P4.2a 标注来源变更登记（2026-08-04）

- owner 决定将 dev60 盲标委托第三方标注者完成。原生成器把导出记录的
  `annotation_owner` **硬编码为 `"owner"`**，会使标注来源失真——已修复：标注人姓名/标识
  改为页面必填项，未填不允许导出，`annotation_owner` 写入实际标注者。生成器
  `scripts/build_p4_2a_labeling_ui.py` 同步更新；因生成器为 create-only，新页面另存为
  `…hardened-v1.2.labeling.html`，v1.1 页面原样保留。
- 交付第三方的离线包仅含盲标页与标注说明，出包前复核：60 条、无任何预测/抽样字段、
  `gold` 全空、不含模型名、零外部请求。样本集合、schema、评测阈值均未变更。
- 评测报告须记录实际标注者标识；若 dev60 与 held-out40 由不同标注者完成，须分别记录并
  在报告中披露（标注者差异是评测结果的已知变异来源）。

### P4.2a dev60 标注回收与"标注者非人类"裁定（2026-08-04）

- 回收文件 `docs/phase4/eval/P4.2a-gold-inventory60-v1.labels-ai-drafted.jsonl`。
  完整性复核全过：60 行、id 集合与冻结样本完全一致、无重复、**不可变字段零漂移**、
  无新增字段（盲标未被污染）、必填项零缺失、`annotation_status` 全为 completed。
- **标注者为 `ChatGPT（GPT-5.6 Pro，受欧阳委托）`，即 AI 而非人类。** 该事实之所以可见，
  是因为 2026-08-04 修复了导出逻辑把 `annotation_owner` 硬编码为 `"owner"` 的缺陷；
  若未修复，这批 AI 标注会被静默记录为 owner 人工标注，整个 P4.2 评测将建立在虚假前提上。
- **裁定**：标签**质量良好**（四项内部一致性检查零违规；复核抽查的 6 条判断均站得住），
  **接受为 dev 集开发信号**，但**不得充当评测门的 ground truth**。理由：若真值亦由 LLM 产生，
  `precision ≥ 0.80` 度量的是"qwen 与 GPT-5.6 有多相似"，而非"qwen 有多准确"；两个 LLM
  可能共享失败模式，相关误差在同类比对中不可见——而排除这种相关性正是人工金标准的唯一作用。
  故：**dev60 的度量一律改称"模型间一致率"，不得表述为 precision/人工金标准**。
- **held-out40 的强制要求**：必须存在**人工裁定**。可采用"AI 起草 + 人工逐条确认/修正"
  （owner 实操约 15–20 分钟），记录为 `ai_drafted_human_adjudicated` 并保留起草者与裁定者
  两个标识；纯 AI 标注的 held-out 结果**不构成有效的 P4.2 通过证据**。
- **人工裁定 UI 合同**：AI 起草文件必须完整覆盖同一份盲化样本，逐字段绑定全部冻结内容；
  禁止含 qwen/model prediction 或抽样依据，起草 `notes` 固定为空。导入只允许一次，预填后
  每条必须由 owner 明确点击确认；任何字段修改都会撤销该条确认。裁定者输入必填、无默认值，
  且必须与 AI 起草者不同。为不修改已冻结的 owner annotation schema，主 JSONL 继续使用
  `drafter_id/adjudicator_id`；另导出逐条 audit sidecar，显式记录
  `draft_annotator`、`adjudicator`、`adjudicated_changed` 与裁定时刻。
- 若 dev60 与 held-out40 标注来源不同，评测报告须分别披露（已知变异来源）。

### P4.2a dev60 指示性结果与系统性偏差诊断（2026-08-04，dev 集允许迭代）

- 可比对 59/60（`news_item_id=190` 模型抽取失败，如实排除）。
  **`materiality>=2` 模型间一致率 = 7/14 = 0.50**（门槛 0.80，当前远不达标）；
  召回位置 7/15 = 0.47；**symbol 完全一致 58/59 = 0.98**（门槛 0.95，达标）。
- **系统性偏差（dev 集最有价值的产出）**：模型**按新闻的戏剧性打分，而非按对该股票价格的
  影响打分**。
  - 假阳性 7 条中 4 条（57%）是**无个股指向的宏观/政策新闻**被判 ≥2
    （如"央行推进融资平台市场化转型""市场监管总局光伏价格合规指导"）；
  - 假阴性 **8 条全部是巨潮公告正文**（回购方案、半年度报告、股东权益变动、上市公告书、
    股东会资料、董事会决议），模型一律给 `materiality=1`。
  - 后果推论：按当前 prompt，事件驱动系统**几乎不会被真正的公司事件触发，反而被宏观新闻
    反复触发**——与 P4 目标恰好相反。这也修正了此前"20% 触发率过高"的判断：触发**量**
    尚可，问题是触发**对象错了**。
- **v1.1 prompt 迭代方向（仅依据 dev 集，held-out 仍封存）**：① 明确 materiality 定义为
  "对该标的股价的潜在影响"，非新闻重要性；② 无明确个股指向的宏观/政策资讯上限为 1；
  ③ 交易所正式公告按事件类型定级，不因文本冗长平实而降分；④ "进展公告"与原始事件分级区分。

#### P4.2a dev prompt 迭代实跑（2026-08-04，外部额度阻断，未冻结）

- 新增严格 dev-only、create-only 的迭代入口
  `scripts/run_p4_2a_dev_iteration.py`。它只读取冻结 dev60 与 AI 起草标签，生产库仅以
  `mode=ro/query_only` 读取证券全集和交易安全计数；产物只写
  `docs/phase4/eval/dev-iterations/`。报告字段固定为
  `metric_semantics=model_interagreement / not_phase_gate=true`，不复用正式 held-out 的
  precision 或人工金标准语义，也不占用 dev-final、freeze receipt 或 held-out one-shot 路径。
- prompt v1.1 及 active contract 在真实调用前完成版本化预注册；只改 prompt 与版本溯源，
  schema、taxonomy、模型、预算、输入和隔离合同均与 v1 相同。唯一候选轮 `v1.1-r1`
  原样留档：60 条中 30 成功 / 30 失败；失败为 `post_validation_failed=20`、
  `http_status_403=10`。仅在 30 条可比子集上的 materiality 正类模型间一致率虽为
  `6/6=1.00`，但覆盖损失 50%，symbol 模型间一致率 `28/30=0.933`，故**明确不通过**，
  不得据此冻结。原始 create-only report 的 `positive_capture=1.00` 也只在可比子集计算；
  30 条失败中有 10 条 AI 参考正类，不能把该值解释成全 dev 召回。blocker 证据已显式列出
  这 10 个 ID；后续报告改名为 `comparable_positive_capture`。
- 20 条后置校验失败全部来自巨潮正文；当前安全错误只持久化通用
  `post_validation_failed`，原始 payload 按合同未保存，因此**不能把具体违规字段写成已证实
  根因**。结合失败集中在长正文、且 v1.1 新增了"引用实质正文"要求，v1.2 做保守的
  `evidence_span` 防错强化：选择短的单行原文片段，禁止删除/增加/规范化空白；其余
  materiality 规则不变。该修正仍是待实测假设，也再次说明 P4.2b 必须落地"违规字段 + 约束
  类型"安全错误码。v1.2 active contract 已在任何 v1.2 输出产生前预注册，尚未启动真实轮次。
- 10 条 HTTP 403 不是限流猜测。失败后一次单条合同探测仍为 403；随后唯一一次最小诊断
  请求得到服务端结构化错误
  `AllocationQuota.FreeTierOnly`（免费额度耗尽，需要补支付信息或关闭“仅使用免费额度”）。
  诊断零自动重试，不持久化 key、请求正文或原始响应。阻断证据：
  `docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.1-r1.blocker.json`。
- **当前裁定**：P4.2a 仍未完成，prompt 未冻结，held-out40 持续封存；不得改模型规避额度，
  也不得把失败轮可比子集的 1.00 当成通过。恢复同一冻结模型 `qwen3.6-flash` 的访问后，
  才能在预注册 v1.2 合同上创建新一轮 dev-only 证据；只有完整覆盖下达到开发阈值，才首次
  生成 dev-final 并创建 freeze receipt。冻结入口新增 fail-closed 重算门：必须 60/60 成功、
  无 active failure、materiality 正类模型间一致率 ≥0.80、symbol
  exact-set 模型间一致率 ≥0.95；receipt 每次验真时对其绑定的 dev-final 重新计算，不能仅凭
  manifest 技术成功绕过开发门。

#### P4.2a v1.3 大陆站模型迁移预注册（2026-08-04）

- Owner 新裁定明确取代上一段“恢复同一 `qwen3.6-flash` 后再继续”的临时阻断处置：
  阿里云大陆站为独立账号，使用
  `https://dashscope.aliyuncs.com/compatible-mode/v1` 与 `qwen3.6-plus`。这不是在旧额度
  错误上静默换模型，而是新的版本化评测条件；旧 v1.1 失败证据继续原样保留。
- 新合同 `config/p4_event_extract_eval_v1_3.yaml` SHA-256
  `1e465f600039a587c26e9686e82a229baf948f8db748b68a5731b23af08fefd6`。它继续绑定 v1.2
  prompt 原文件与 SHA
  `5080bdb2b373f6360527c79465da8645884fd33308c9e3d061120b0a1298fe05`，没有复制或伪装
  成 v1.3 prompt；taxonomy、JSON Schema、temperature `0.2`、max tokens `2000`、
  total deadline `20s`、零重试与 `enable_thinking=false` 均不变。
- 合同加载与运行时守卫改为从**冻结合同**取得期望 model/endpoint，再与 `.env` 的
  purpose-resolved model 和规范化 base URL 精确对拍；purpose override 残留 flash、国际站
  endpoint、非 HTTPS/带 query 或凭证的 endpoint 均 fail-closed。守卫本身没有移除。
- 新 evaluation design `config/p4_event_evaluation_v1_2.yaml` SHA-256
  `1f4e5f6f65a609842c0074735174a23de582c2aff1053b42716eb7ed8434b780` 以旧 v1.1 设计
  SHA 为继承锚，保持 dev60 身份、held-out 窗口、选择 seed、阈值和 one-shot 纪律不变；
  只绑定 v1.3 prediction contract，并为 dev-final、freeze receipt、held-out 与最终报告使用
  独立的 v1.2 create-only 路径。旧 design 默认入口保持不变，新流程必须显式传入 v1.2。
- held-out 标注 provenance 同时预注册为
  `ai_drafted_human_adjudicated`：每条必须同时保留非空且不同的 `drafter_id` 与
  `adjudicator_id`，`annotation_owner` 必须等于人工裁定者；纯 AI 标注 fail-closed。dev60
  的 AI 标签仍只用于 `model_interagreement`，不得称 precision 或人工金标准。
- 显式缓存在 v1.3 固定为 `enabled=false / cache_control=null`。阿里云官方文档说明批量抽取
  可在 system message 上用 `cache_control`，但这会改变请求结构；本轮为保持预注册与可比性
  不启用。若以后评估成本优化，必须另发合同并在任何输出前冻结缓存标记、作用域和审计字段。
- 有效轮必须 60/60 成功且失败为 0，再报告 materiality 与 symbol 模型间一致率，并与
  flash 基线 `7/14=0.50`、`58/59≈0.98` 作**指示性对照**。因 model、endpoint 与 prompt
  version 同时变化，不得把改善写成模型能力的单变量因果证明。达到 `0.80 / 0.95` 开发门后
  才允许创建 dev-final 与冻结 receipt；held-out 仍锁到 2026-08-06 00:10 CST，复核方 4 条
  探测不进入正式分母。
- P4.1 冻结配置继续保持 SHA-256
  `d0dcd665472b50092a1b4fa7f65f7115778e1b89ac11aca0ed49dc70beaa790b`；本批不改观察窗
  配置、scheduler、服务或生产表，不创建提案、委托或交易。

#### P4.2a v1.3 dev60 正式轮次结果（2026-08-04，未通过）

- 预注册提交 `10c655d` 先于任何 v1.3 模型输出完成。随后只执行一次正式 dev-only 轮次
  `v1.3-r1`：`qwen3.6-plus` / 阿里云大陆 endpoint / v1.2 prompt，结果 **53/60 成功、
  7/60 失败**；失败全部为安全错误码 `post_validation_failed`，ID 为
  `250/258/287/304/306/336/358`，其中 AI 参考正类为 `258/287/304/306/336`。
  因未满足 60/60 零失败合同，本轮 `formal_dev_round_valid=false`、
  `development_ready_to_freeze=false`。
- 仅在 53 条可比样本上，materiality≥2 正类模型间一致率为 **10/10=1.00**，可比正类捕获
  为 `10/11=0.909`（假阴性 ID `309`）。相对 flash 的 `7/14=0.50` 是明显的**指示性改善**，
  但 7 条失败（含 5 条参考正类）不在分母中，故不能作为正式通过证据，也不能单变量归因给
  模型能力。
- symbol exact-set 模型间一致率为 **48/53=0.906**，低于 0.95 门；偏差 ID
  `44/75/210/232/393`。其中 `75/210/232/393` 的模型输出把被推荐、被提及或采集入口代码
  当成新闻主体，而 AI dev 标签要求空集合；`44` 则是相反方向——AI 标签仅凭“美的空调”
  映射 `000333`，模型依据“无明确代码”输出空集合。后续须先裁定标签语义，禁止通过猜代码或
  放宽 symbol 约束抬高指标。
- 当前产物：
  `P4.2a-dev60-v1.3-r1.predictions.jsonl` SHA
  `b882a5cdad7025f8499eae75b617e189174ef866ab949749dd58c4a193229134`；
  manifest SHA `4eb7f05e8196ac5dd4d646bb1b8be7a56b93123e4866b0ba4a79121ffb370262`；
  report SHA `781f6b7f30d97b9a43978feccec6891fa9b959aca0572a53784527a7e0e926e9`；
  blocker 记录位于
  `docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.3-r1.blocker.json`。
- 7 条后置校验失败仍只记录通用安全码，原始 payload 与异常细节按合同未持久化，因此不得
  把具体违规字段写成已证实根因。7 条均为巨潮公告正文，长文/换行聚类使
  `evidence_span` 非连续原文片段成为高可信但**尚未证实**的诊断假设。下一轮前先补“违规字段
  + 约束类型”的结构化安全错误码，再只依据 dev 集预注册新 prompt 合同；不得重跑同一合同、
  降低阈值或把部分覆盖结果冻结。
- 运行后生产 `llm_calls` 仍为 109，提案/委托 `1/1`、非 SIMULATE 委托 0，
  `quick_check=ok`；P4.1 配置 SHA 未变。**dev-final、冻结 receipt 均未创建，held-out 未
  访问且继续封存，P4.2a 不标 done，P4.2b 不解锁。**

### P4.2a v1.3 失败根因裁定（2026-08-04，独立复核实测复现）

Codex 如实将 7 条 `post_validation_failed` 登记为"待验证假设"而未编造根因，处置正确。
复核方持相同 endpoint/模型/prompt 逐条复现，**根因已确证并可量化拆分**：

- **7/7 失败均为 `evidence_span` 非原文连续子串**（其余字段无违规：JSON 严格、无多余字段、
  长度合规）。模型对 materiality 的判断本身正确（复现中 258/287/306/304/336 均给出 m≥2）。
- **5/7 属规格缺陷，非模型缺陷**：巨潮公告正文由 PDF 抽取而来，**句中含硬换行**
  （失败项换行密度 501–1291 换行 / 4k–14k 字符，成功项中位数仅 201）。例：
  `[306]` 原文为 `本次\n回购股份金额不低于人民币 3,000 万元`，模型按语义输出同一句但不含
  该断行，严格 `in` 判定即失败。**改用空白不敏感匹配后这 5 条全部通过。**
- **2/7 是真实的模型合成行为，校验器抓对了**：
  `[304]` 把财报表格中分散的单元格拼成一行（`归属于上市公司股东的净利润 49,597,601.47 -46.78`）；
  `[336]` 重述而非逐字引用。这两条**应当继续判失败**——这正是 `evidence_span` 要防的东西。

**裁定与处置**：

1. `evidence_span_must_be_contiguous_substring` 的比较方式改为**空白归一化后匹配**
   （比较前对候选 span 与 `original_text` 同时折叠所有空白字符）。**这是规格缺陷修正，
   不是阈值放宽**——判据：反幻觉保证 100% 保留（增删空白无法伪造内容），且修正后
   2/7 仍然失败，证明该校验依然有效咬合。修正须以**新版本合同**登记，并在报告中同时
   披露修正前后两组数字。
2. 因本次修正是在已看到 dev 结果之后提出的，**必须原样保留 v1.3-r1 的失败记录与本裁定
   全文**，不得改写历史轮次；held-out 至今未解锁、未读取，修正在 held-out 数据产生前
   完成登记，符合预注册纪律。
3. **prompt 同时加强**（针对真实的 2 条）：明确要求 `evidence_span` 为**连续字符的逐字
   引用**，禁止跨表格单元格拼接、禁止重述改写。
4. 待验证的 `post_validation_failed` 安全错误码仍须补齐（违规字段名 + 约束类型）——
   本次根因是复核方靠外部复现拿到的，生产管道不能依赖这种方式定位故障。

**指示性影响**：5 条恢复后可比样本由 53 升至 58；其中 3 条参考正类（258/287/306）
模型与标注均为 m≥2，故 materiality 一致率仍为 1.00，但**分母由 10 增至 13**，
结论稳健性提升。symbol 一致率仍需按 §symbol 偏差单独裁定，不因本修正而改变。

### P4.2a v1.4 dev60 正式轮次结果（2026-08-04，未通过）

- 结构化 `post_validation_failed` 安全码先以独立提交
  `00fccf5` / `8dcccb2` 落地，再由提交 `199f724` 预注册 v1.4，顺序早于任何
  v1.4 模型输出。v1.3-r1 原始三件套与 blocker 均未改写。
- v1.4 合同 SHA
  `e6d3e7db08e2d226c850092f0f794d7194eaf1935a56cbfe267a86e1297f37fc`，
  将 `evidence_span` 约束改为
  `unicode_whitespace_elided_contiguous_substring_v1`；模型仍为
  `qwen3.6-plus`，大陆 endpoint、temperature `0.2`、max tokens `2000`、
  20 秒、零重试、`enable_thinking=false` 和显式缓存关闭均不变。
- 正式 dev-only 轮次 `v1.4-r1` 只执行一次，结果 **54/60 成功、6 失败，正式轮次无效**：
  - 5 条为安全结构化
    `post_validation_failed(field=evidence_span,
    constraint=whitespace_normalized_contiguous_substring)`：
    `253 / 258 / 304 / 336 / 340`；
  - 1 条 `280` 为底层 `schema_validation_failed`。当前安全错误码未暴露字段或约束，
    故根因登记为 `unproven`，不得猜测；
  - 安全产物未持久化候选 span 或原始 payload，因此上述 5 条只能证明违规字段与约束，
    **不能**仅凭本地产物进一步声称是重述、表格拼接或其他具体原因。
- 可比行上的 materiality 模型间一致率为 `11/13 = 0.8461538461`，达到开发目标
  `0.80`，但 6 条失败中含 4 条参考正类，不能据此冻结。symbol 原始冻结标签口径为
  `49/54 = 0.9074074074`，低于 `0.95`：
  - `44` 延续独立裁定为 AI dev 标签缺陷，不得靠猜代码迎合；
  - `28 / 67 / 71 / 96` 为本轮新闻主体漏映射，应由下一版 prompt 收紧主体定义修复；
  - v1.3 的 `75 / 210 / 232 / 393` 四个过度归属在 v1.4 已正确修复。不可改写的
    v1.4 report 仍把该历史列表显示为当前诊断，blocker 已显式勘误。
- v1.4 report 的 flash 指示性对照还漏列了
  `evidence_span_match_mode / validation_contract` 两个变化维度，且可比集为
  `59 vs 54`；blocker 明确禁止把该对照读成单变量因果结论。
- 冻结产物：
  - predictions SHA
    `5aaa4deded34dc858bc7e90b4db5dc2b2cf656f4d4dd673e5a9980d3152257b2`；
  - manifest SHA
    `fa56b4f167530c7da1d0887f0163b13aa69b0a73de8e48a75cc6335cbbb1904a`；
  - report SHA
    `5cf0722fb8851122720ea59c53cb355be9095f1b2a4d1658b827cf48a2dbf969`；
  - create-only blocker SHA
    `3d0038515e208bca37e096b280ec411da2086addd8696d2ee6e8afa36fad00f9`。
- 本轮生产库以 `mode=ro / query_only=1` 打开，`total_changes=0`，生产
  `llm_calls` 前后均为 `109`；提案/委托 `1/1`、非 SIMULATE 委托 `0`、
  `quick_check=ok`，P4.1 冻结配置 SHA 未变。**未创建 dev-final 或冻结 receipt，
  held-out 未访问，P4.2a 不标 done，P4.2b 不解锁。**
- 同合同不得重跑。下一轮必须先以新版本合同预注册：
  逐字证据“定位 → 复制 → Unicode 空白归一化自检 → 失败则缩短”的流程、严格 JSON
  字段预检，以及不依赖 materiality 的直接主体映射；规则和标签不放宽，显式缓存继续关闭。

### P4.2a v1.5 dev60 预注册（2026-08-04，任何新模型输出前）

- v1.4-r1 失败三件套与 blocker 已由提交 `a80cb8e` 原样冻结；本轮只能从这些
  不可变 dev 证据派生 prompt，不得重写历史、变更 frozen labels 或读取 held-out。
- 新 prompt `config/prompts/p4_news_event_extract_v1_4.txt` SHA
  `ff42e6905e009e8a7a3a0ae7b7fedce043cbf73f55f3551cf76bbfdcfef33f2b`：
  - 主体识别与 materiality 解耦；发行人、明确经营单元/产品/品牌及官方董秘/投资者关系回复
    可沿用已审计的非空 `ingested_symbol`；
  - 继续排除推荐对象、研报发布方、同名地点/市场及顺带股东/支持方；`ingested_symbol=null`
    且原文无六位代码时禁止猜测，故 `44` 不得通过迎合 AI 标签修复；
  - `evidence_span` 强制“先定位原文切片 → 原样复制 → 删除双方 Unicode 空白自检 →
    失败则复制更短片段”，禁止跨表格单元格拼接、重述、润色；
  - 返回前静默检查严格 JSON 的七个且仅七个字段，修复 `280` 类 schema 失败时仍保持
    fail-closed，不编造其历史字段级根因。
- 新抽取合同 `config/p4_event_extract_eval_v1_5.yaml` SHA
  `a07f9f37e0877bd06ce3dc9e8a0e03c51bbb92fdc3ba6738b6932d7679aca560`。
  仅版本时间与 prompt 绑定相对 v1.4 改变；模型 `qwen3.6-plus`、大陆 endpoint、
  temperature `0.2`、max tokens `2000`、20 秒、零重试、`enable_thinking=false`、
  taxonomy/schema 与
  `unicode_whitespace_elided_contiguous_substring_v1` 全部保持。显式缓存仍为
  `enabled=false / cache_control=null`。
- 新评测设计 `config/p4_event_evaluation_v1_4.yaml` SHA
  `3a392a6c834cdde219f54e149f22e235b0316765d0bd69501bb1f312d7ee0e33`：
  - 继承并逐字节冻结 v1.3 设计的 dev60、held-out 时间窗/seed、阈值、标注与 provenance；
  - 重新实算绑定 v1.4-r1 predictions / manifest / report / blocker 四个 SHA；
  - 新报告必须同时含
    `evidence_validation.v1_4_r1_actual`、
    `evidence_validation.v1_5_actual`、
    `evidence_validation.v1_5_legacy_exact_shadow`、
    `symbol_diagnostics.v1_4_r1_actual`，不得把历史诊断冒充当前结果；
  - dev runner 在首个模型调用前验证 design v1.4 ↔ contract v1.5、报告字段全集和
    `v1.5-*` round namespace；dev-final、freeze receipt、held-out 全局 seal 与最终评测
    使用新的 v1.4 create-only namespace。
- 正式轮次固定为 `v1.5-r1`，仍须 **60/60 成功、零失败**，materiality 模型间一致率
  `>=0.80` 且原始冻结 AI 标签口径 symbol `>=0.95` 才有效。只要任一门失败，即只追加
  新 blocker，不创建 dev-final 或 freeze receipt；达标后才以独立 create-only 步骤生成
  dev-final 并冻结 prompt+模型。held-out 至少到 2026-08-06 时间锁后仍须
  `ai_drafted_human_adjudicated`，本预注册不解锁、不读取。
- 全程生产库只读、P4.1 冻结配置零改动、P4.2b 继续锁定，禁止 scheduler、提案、委托和交易
  写入。

### P4.2a v1.5 dev60 正式轮次结果（2026-08-04，未通过）

- 预注册提交 `cea0ff2` 先于任何 v1.5 模型输出；首次直接启动因本地 `PYTHONPATH`
  缺失在 Python import 阶段退出，未加载合同、未发请求、未建产物。随后补齐仓库根
  `PYTHONPATH`，只执行一次正式模型轮次 `v1.5-r1`。
- 结果 **50/60 成功、10 失败，正式轮次无效**：
  - 9 条安全结构化
    `post_validation_failed(field=evidence_span,
    constraint=whitespace_normalized_contiguous_substring)`：
    `9 / 250 / 272 / 303 / 304 / 306 / 336 / 340 / 360`；
  - 1 条 `280` 为 `schema_validation_failed`，当前安全产物没有字段/约束，
    继续登记为 `unproven`，不得猜测；
  - 原始 response、候选 span 和异常细节均未持久化，故 9 条只能证明违规字段与约束，
    不能仅凭本轮文件断言具体为重述、表格拼接或字符差异。
- 可比行上 materiality 模型间一致率 `11/12 = 0.9166666667`、symbol 原始冻结标签口径
  `48/50 = 0.96` 均达到开发目标，但 10 条失败中有 4 条参考正类
  `272 / 304 / 306 / 336`，**60/60 零失败硬门未过，两个比例均不能作为冻结证据**。
  symbol mismatch 为 `44 / 393`：`44` 仍是 AI 标签缺陷且禁止猜代码；`393` 是历史过度归属
  的复发，下一 prompt 必须继续排除而不能放宽规则。
- 冻结产物：
  - predictions SHA
    `e9642568d50e0c4d9c1fe7726a117cb90269ed61ce116cd8665a989eb24dc297`；
  - manifest SHA
    `211212ba610a876dd107986c06e12501db08d7f03e9c87a1f5007d306f47f51e`；
  - report SHA
    `250545184031919e798a69dfd00dae9b8a06eb1b4168d628c0e5c48c00b7f845`；
  - create-only blocker SHA
    `856cdd30972b4b287b2c944930c06531b7fd14b2897fa230c71faa9549bfc4a0`。
- 运行时通过进程环境显式把本机 `.env` 中的 Futu trade 开关覆盖为 false；manifest 实证
  research、live/paper-auto/account mutation/Futu trade 全安全，unlock 永久屏蔽。生产库
  `mode=ro / query_only=1 / total_changes=0`，生产 `llm_calls` 前后均为 `109`，
  提案/委托 `1/1`、非 SIMULATE `0`、`quick_check=ok`，P4.1 SHA 未变。
- **未创建 dev-final 或 freeze receipt，held-out 未访问且继续封存。** 同合同不得重跑；
  下一轮只允许依据冻结 dev 证据预注册新版本，不降阈值、不修改标签、不放宽 matcher、
  不启用显式缓存。

### P4.2a v1.6 原文候选证据协议预注册（2026-08-04，任何新模型输出前）

- v1.5-r1 的 predictions / manifest / report / blocker 已由提交 `86ff1a6` 原样冻结；
  本轮仅使用该 dev 证据设计新输入协议，未读取 held-out、未改冻结 AI 标签、未放宽
  `60/60`、materiality `>=0.80` 或 symbol `>=0.95` 任一门槛。
- 安全 schema 诊断已先由独立提交 `20080dc` 补齐：`schema_validation_failed` 只允许持久化
  冻结白名单中的顶层字段与固定约束码；模型 payload、动态键、异常文本和原始响应仍不落盘。
- 新抽取合同 `config/p4_event_extract_eval_v1_6.yaml` SHA
  `4e88990d2ee7671db316794aabd0a476f798b5e542f00bbb8ffbd3f7fd423269`；
  prompt `config/prompts/p4_news_event_extract_v1_5.txt` SHA
  `4b44ed5efe281b68664b415865b758b75b30ace6eda2617952de66a87596c204`；
  模型原始结果 schema `config/schemas/p4_news_event_candidate_v1.schema.json` SHA
  `c106cd15bd974de19ecc01d6e99e8f39c39fbf14df3a3b4dc74ee9b08ff6dd66`；
  持久化最终结果 schema 继续绑定
  `config/schemas/p4_news_event_v1.schema.json` SHA
  `0ac68654ce23ecd4e537d849d695e092c76dcb9de0fb03793e65ae62b181947f`。
- `original_text` 不再交给模型自由复制证据，而由确定性、无标签、无预测访问的算法按原文
  顺序完整分区；模型输入仅包含紧凑候选
  `[id, raw_start, raw_end, whitespace-folded display]`，模型只返回一个已登记
  `evidence_candidate_id`。代码随后按登记的 `raw_start:raw_end` 物化精确原文连续切片，
  再通过既有 Unicode 空白归一化 matcher；不存在的 ID、schema 漂移或 matcher 失败均
  fail closed，禁止模糊匹配、自动修复、候选拼接或降级回自由文本。
- 模型原始结果与持久化最终结果使用两个互不宽松兼容的严格 schema：原始结果必须含
  `evidence_candidate_id` 且不得含 `evidence_span`；落盘结果必须含物化后的
  `evidence_span` 且不得含候选 ID。断点恢复和 held-out 前置校验只验证持久化 schema，
  不会把落盘结果误送回模型候选 schema。
- dev60 冻结输入的旧 `input_sha256` 仍按原八字段 JSON 原样验证；v1.6 模型请求、checkpoint
  与输出行另算候选 JSON SHA。预测行同时绑定
  `declared_input_sha256`（冻结旧身份）与 `input_sha256`（真实请求身份），不得混用或改写
  冻结样本字节。60 条 dev 输入的候选分区均无缺口/重叠、每段 raw `<=500`、display
  `<=320`，最大序列化模型输入 `15,115 < 16,000` 字符。
- prompt 保留 ID `44` 为 AI dev 标签缺陷的裁定：`ingested_symbol=null` 且原文无六位代码时
  继续禁止猜代码；同时继续排除推荐对象、研报发布方、地点同名公司和顺带股东/支持方，
  包括 v1.5 复发的 `393`，不靠放宽 symbol 规则提高比例。
- 模型、endpoint、temperature `0.2`、max tokens `2000`、20 秒、零重试、
  `enable_thinking=false` 与显式缓存关闭均不变。非重叠 320 raw-char 分段可能扩大单条
  证据范围或在边界切断事实，故结果必须披露为“输入/证据协议变更后的新轮次”，禁止把改善
  单变量归因于 prompt 或模型；不得看 dev 结果后原地调分段参数。
- 正式轮次固定为 `v1.6-r1`，必须在新的 evaluation design、create-only namespace、历史
  v1.5 四件套锚和全局 held-out seal 全部预注册并通过质量门后才可执行。只有 `60/60`
  零失败且双指标达门才允许另行创建 dev-final 与冻结回执；held-out 至少到
  `2026-08-06 00:10 CST` 后仍须 `ai_drafted_human_adjudicated`，本节不解锁、不读取。
- 配套评测设计 `config/p4_event_evaluation_v1_5.yaml` SHA
  `6a8193828df380a94b36fd7b0bc995930e64909339cd009c834d3487d3ae3c05`：
  逐字节继承 v1.4 的样本、时间窗、seed、阈值、标注与 provenance；绑定 v1.5-r1
  predictions / manifest / report / blocker 四件套及 `50/60`、10 个失败 ID、
  gold∩失败 `[272,304,306,336]`；为 dev-final、freeze receipt、held-out 和最终报告
  启用全新的 v1.5 create-only namespace。报告必须同时保留 v1.4/v1.5 历史层，并新增
  双输入哈希、候选 schema、物化 schema 与候选切片证据；全局 seal 同时覆盖 v1.1–v1.5
  的所有 held-out 命名空间。

### P4.2a v1.6 dev60 正式轮次结果（2026-08-04，通过开发门）

- v1.6 抽取合同与评测设计已先由独立提交 `38d30a3`、`6458195` 冻结；正式调用前两轮
  只读审计均核对了合同/提示词/双 schema/评测设计/P4.1 配置哈希、全局 create-only seal、
  held-out 时间锁和 SQLite 只读安全。正式命令以进程环境强制关闭 live、paper、
  paper-auto、Futu quote/query/trade、账户写入、scheduler 与 market poll，只执行一次
  `v1.6-r1`，未补跑失败行。
- 正式轮次 **60/60 成功、0 失败**，`formal_dev_round_valid=true`。`materiality>=2`
  正类模型间一致率为 **15/17 = 0.8823529412**（开发目标 `>=0.80`；`FP=[303,414]`、
  `FN=[309]`）；symbol exact-set 原始冻结标签口径为
  **59/60 = 0.9833333333**（开发目标 `>=0.95`），唯一差异为已裁定的 AI dev
  标注缺陷 `ID=44`。排除该缺陷的诊断口径为 `59/59=1.00`，仅作诊断、不替代原始门。
- 60 条成功结果全部由已登记 candidate ID 物化为原文连续精确切片；active matcher 与
  legacy exact shadow 均为 `60/60`。预测行同时具备两类输入哈希，`rows_with_both=60`、
  `distinct_hash_pair_count=60`；冻结旧输入身份与真实候选请求身份未混用。
- create-only 证据：
  - `docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.6-r1.predictions.jsonl`
    SHA `44f09a0e20d51d392461980d0b3dce886dbaa9699448ca24d2a8ce1f27839e10`；
  - `docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.6-r1.manifest.json`
    SHA `ef11067c94652262535c928140dd60d721dfb7638a7ac41ea05108cbda844dbd`；
  - `docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.6-r1.report.json`
    SHA `6aa0fdaa3f105e87f4ac40975628b9dcc7d26117a07c945421fa899c7190afa4`。
- manifest 实证生产库 `mode=ro / query_only=1 / total_changes=0`、`production_writes=0`、
  交易表仍为 proposal/order `1/1` 且非 SIMULATE 委托为 `0`；P4.1 冻结配置 SHA 仍为
  `d0dcd665472b50092a1b4fa7f65f7115778e1b89ac11aca0ed49dc70beaa790b`。
  `heldout_accessed=false` 且当前仍早于 `2026-08-06 00:10 CST`。本轮只证明开发集已达
  冻结条件，**不是 held-out phase gate，也不解锁 P4.2b**；下一步仅可另行创建
  dev-final 与 prompt/model freeze receipt，然后继续等待人工裁定 held-out。
- 预注册的 dev-final 随后独立执行并再次得到 **60/60 成功、0 失败**；重新计算的
  materiality/symbol 模型间一致率仍为 `0.8823529412 / 0.9833333333`。create-only
  predictions SHA 为 `e419c06a0e1112753490b66cfb3709dd9fb311361d0a4c392c5e78029e193685`，
  manifest SHA 为 `1ef00b10876c3f4ca8cbb0e02efaeeac17d057b7758297b9decaab89e501c79f`，
  双输入有序身份 SHA 为
  `48235e5ed026f22f19b1d571b42f0166489de1e035df0aacefa59f94ab157dea`。
- ⚠ **DEVIATION（冻结验证器漏接双哈希，结果数据未改写）**：首次纯本地 freeze 在创建
  receipt 前被 `heldout_safety_gate_failed` 拒绝。分段复核证明 dev-final 逐行及两项开发门
  均通过，真实根因是 `validate_dev_final_prediction_freeze` 仍沿用 v1.5 单哈希身份，把
  冻结 legacy `input_sha256` 错当成 v1.6 candidate request SHA；同时 freeze receipt
  尚未切换到 materialized result schema。修复只让 authoritative validator 落实预注册的
  `required_dual_hash_identity=true` 与 model/materialized 双 schema 绑定，未改合同、
  prompt、模型输出、标签、matcher、symbol 规则或阈值；新增隔离回归测试后，原 dev-final
  文件原样通过。未重跑模型，失败尝试未产生 receipt。
- prompt/model freeze receipt 已于 `2026-08-04T12:17:31.297151Z` create-only 生成：
  `docs/phase4/eval/P4.2a-heldout-prediction-contract-freeze-v1.5.json`，SHA
  `9adab49b5b5e8d0bf942a591878c1718fc3d158f5638144db7c5cb80b1e63f68`。它绑定
  `qwen3.6-plus`、大陆 DashScope、prompt SHA `4b44ed5e…c204`、materialized schema
  SHA `0ac68654…947f`、contract SHA `4e88990d…3269`、cache=false 与上述双哈希
  dev-final。runner 与独立 gold-builder 两条加载路径均重新验真通过。
- 截至冻结完成，held-out v1.5 candidate/selection/prediction/annotation/report 产物仍全部
  不存在，未读取任何 held-out 标签或正文；时间锁与
  `ai_drafted_human_adjudicated` 要求不变。**P4.2a 尚未完成，P4.2b 仍未解锁。**

### P4.2a v1 评测合同预注册（2026-08-03，首次真实 LLM 试跑前）

- Owner 解锁基线：`4c79373`。冻结配置 `config/p4_event_extract_eval_v1.yaml`，SHA-256
  `b3eb24c63816043edf0ef728d8d9778cd9083d720649d6fff3ae6289bba74300`；prompt 与严格
  JSON Schema 分别独立哈希。模型固定 `qwen3.6-flash`，`enable_thinking=false`、单条输出
  ≤2,000 tokens、总时限 20 秒、零自动重试、单轮上限 2,000 条。
- 现库存快照在 2026-08-03 10:13 CST 以只读事务冻结为 `news_items id<=423`（423 条，
  `max(available_time)=2026-08-03T02:10:09.075785Z`）。真实模型输出产生前先冻结合同并独立
  提交；离线试跑只读该快照，结果与失败审计仅写 `docs/phase4/eval/`。
- 100 条盲标样本与模型预测解耦：现库存 60 条固定为巨潮正文 24、同花顺有/无 symbol
  各 9、新浪有/无 symbol 各 9；未来 40 条固定从 8/4、8/5 每日各取 20 条，逐日配额为
  巨潮正文 10、同花顺 2/3、新浪 2/3。以冻结 seed 的 SHA-256 排名决定 ID；任何分层不足、
  巨潮 PDF 正文获取失败均 fail-closed，不换样本，owner 标签不得预填模型结果。
- 指标在标注前固定：`materiality>=2` precision = TP/(TP+FP)，无预测阳性直接失败，门槛
  0.80；symbol 采用逐条集合 exact-match，全部样本与 gold 有标的子集均须 ≥0.95。失败轮
  追加留档；改 prompt/模型必须发布新合同版本，样本和阈值不变。
- 隔离边界：生产库强制 `mode=ro/query_only`，LLM 审计使用 eval 进程内存会话；不建
  `news_events`、不改 ORM/迁移/registry/API，不修改或重启 scheduler，不触碰 P4.1 配置与
  验收器，不创建提案/委托。symbol 仅接受“原文中按数字边界明确出现且属于证券全集”的
  6 位代码，或资讯底座已审计绑定的 `ingested_symbol`；严格 JSON 解析拒绝重复键。
  P4.2b 继续冻结。

#### P4.2a 执行进度（2026-08-03，未完成）

- 冻结合同与离线工具已先于真实输出独立提交：`d020cfe`。随后仅对“失败终态报告可冻结”
  与部分成功审计展示作运维修正：`a850dfc`、`ae39689`；taxonomy、prompt、schema、抽样、
  模型及评测阈值均未改变。
- `qwen3.6-flash` 已对冻结库存 `id=1..423` 完成一次全量离线试跑，未自动或人工重试：
  406 成功、17 `extract_failed`（16 `post_validation_failed`、1 `http_status_400`），覆盖
  423/423。生产 `llm_calls` 前后均为 109，交易安全表仍为 1/1；结果 JSONL SHA-256
  `b1baca41a2d4cbbfa62ec921bc77ea760f2b80f7a075e1625c68fb74acac06a4`，终态报告 SHA-256
  `794daa7b6f2152194575e3bf764139fdd046eb4aa31f4d310b57f92446b37e07`。
- 现库存 60 条盲标样本已冻结：巨潮公告正文 24、同花顺有/无 symbol 各 9、新浪有/无
  symbol 各 9；60 个 ID 唯一，gold 全为 null，模型预测字段为 0，24 份巨潮正文均记录
  PDF/全文 SHA-256 且未持久化 PDF。样本 SHA-256
  `81b3c0b27cd344fe4c2a735261e501dd2f60a0927c14b2c37e5b2a4879b4a2ba`。
- v1.1 未来运行链已完成纯代码准备并经独立只读复核 `APPROVE / zero blocker`：active
  contract 与 JSON/JSONL 均严格拒绝重复键、YAML merge-key 冲突和非有限数；dev-final
  完成时刻在 60 条覆盖校验后取证；heldout started 在首个模型调用前 fsync 并绑定完整交易
  安全快照；crash finalizer 零模型调用且不得弱化证据；selection/evaluator 独立重验
  receipt、contract、候选身份、prediction manifest 与 one-shot 两行终态。正式 heldout CLI
  禁止数据库覆写。以上仅为合成 fixture 验证，未读取 8/4–8/5 数据、未调用真实 LLM。
- 尚欠 8/4、8/5 自然新增的 40 条（硬时间门 8/6 00:10 CST）及 owner 完整盲标；两项
  金标准指标尚未运行。故 **P4.2a 不标 done，P4.2b 继续冻结**。

#### 评测设计修订 v1.1（2026-08-03，独立复核提出；标注开始前、零结果状态下记录）

复核在 60 条冻结样本上实测到两处会使评测门失效的设计缺陷，按 owner 既有授权修订。
**两项阈值（precision ≥ 0.80、symbol 准确率 ≥ 0.95）一字未改，本次修订只加严、不放水。**
修订合同独立冻结于 `config/p4_event_evaluation_v1_1.yaml`（SHA-256
`8e9c1d107ef235f9c017dbfb679fa01e52e0ff966f01d9efad625110588ebf97`），显式引用且不改写
v1 抽取/标注基线合同 `b3eb24c6…`；dev60、heldout40、预测正类池确定性抽样、模型合同冻结回执、
推理/评分各一次性状态、owner 盲标字段禁区及报告强制披露字段均由 v1.1 合同 fail-closed 校验；
其中最终 dev60 预测及其 manifest 必须 create-only 冻结并绑定 60 条有序身份摘要、成功/失败计数
与 active contract SHA，校验通过后才允许开始 heldout 推理。heldout selection 只生成独立的
40 条盲样本，不得抢占 completed owner 标签或 combined100 路径；dev60/heldout40 两份 owner
标签均为 completed、通过身份/哈希/盲性校验后，才按 1–60/61–100 重编号 create-only 合并，
并由 owner completion manifest 绑定三份标签 SHA，最终评测一次性状态在该 manifest 验真前不得启动。

1. **precision 分母过小 → 剩余 40 条改为从预测正类抽样**。实测 60 条中仅 14 条被模型判为
   `materiality>=2`，凑满 100 条约 23 条；n=23 时 precision 的 95% 置信区间约 ±0.16，
   "测得 0.80"无法与真值 0.65 区分，门形同虚设。故 8/4–8/5 的 40 条改为**从该批次中模型
   预测 `materiality>=2` 的条目内随机抽取**（IE 评测标准做法：估计 precision 应从预测正类
   抽样），使分母升至约 55。owner 仍全程盲标、看不到任何预测值，抽样依据不写入交付给
   owner 的标注文件。60 条按来源分层不变，继续提供无偏的整体分布与漏报（false negative）信号。
2. **同一批样本反复调 prompt = 过拟合评测集 → 改为 dev/test 划分**。原合同允许"不达标就改
   prompt 再评"，若始终在同一 100 条上迭代，通过阈值只反映记忆而非泛化。故：
   **60 条现库存 = dev 集**（prompt 迭代仅可依据此集）；**40 条交易日样本 = held-out 测试集**，
   仅在 prompt 冻结后运行**一次**，`materiality>=2` 的 precision 门在此集判定；
   symbol 准确率在全部 100 条上判定并分别报告 dev/test 两组数字。测试集一经评测即失效，
   如需二次评测必须新抽样本并登记。

**执行顺序修订**：owner 对 60 条 dev 集的盲标即刻开始（不再等 8/6）；40 条测试集于
8/6 00:10 CST 后按上述抽样规则冻结并盲标。

#### 复核发现（非阻断，须在 P4.2b 处置）

- **`post_validation_failed` 缺安全错误码**：16/423（3.8%）如实失败但未记录失败字段与约束
  （`exception_detail_persisted=false`），无法定位根因。失败项 13/17 来自同花顺、标题与
  长度均无异常，指向模型输出的 schema 符合性而非输入病理。P4.2b 须在不持久化原始 payload
  的前提下补结构化安全错误码（违规字段名 + 约束类型），否则生产将静默丢失约 4% 资讯。
- **触发量预警**：预测 `materiality>=2` 占 81/406 = **20.0%**；其中 `event_type != other`
  且带 `symbols` 的为 42 条。按当前日均资讯量推算，P4.3/P4.4 每日约 40–80 次"重磅"触发，
  若 precision 偏低即为噪声洪水。评测报告须显式列出该比例；P4.3 触发条件可据评测结果
  收紧为 `materiality>=2 AND symbols 非空 AND event_type != other`（如需收紧，按 policy
  新版本登记）。
- **gold ∩ 失败集 = {news_item_id 190}**：该条无模型预测，评测时计入召回分母、不计入
  precision 分母，须在报告中显式披露。

- 事件 taxonomy v1（版本化常量）：`earnings_preannounce / major_contract / buyback_or_holder_change /
  regulatory_action / halt_resume / ma_restructure / policy_sector / dividend / other`。
- 每条新闻 → 严格 JSON（走 `src/alphapilot/llm` 现有层，purpose model 配置）：
  `{symbols[], event_type, direction(-1/0/+1), materiality(0-3), summary, confidence(0-1),
  evidence_span}`；解析失败/超预算如实落 `extract_failed`，禁止编造。
- 成本与延迟预算（config 固定）：走本地 LLM 配置；单条输出 ≤ 2k tokens、端到端 ≤ 20s、
  日抽取上限 2,000 条；超限降级为仅入库不抽取并计数告警。
- 表 `news_events`（FK news_items，幂等 upsert，含 `model_version`）。
- **金标准评测**：owner 参与人工标注 100 条固定样本；预注册验收门：
  `materiality>=2` 事件的 precision ≥ 0.80、symbol 映射准确率 ≥ 0.95。prompt 迭代只允许看
  dev60；heldout40 只评一次，失败后必须登记新设计版本并换一批 heldout，禁止复用测试集或
  放水阈值。
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
