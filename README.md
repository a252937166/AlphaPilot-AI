# AlphaPilot AI

> 面向 A 股为主、可扩展港股与美股的概率预测、自动选股、持续追踪、板块研判、大盘监控与交易辅助平台。

[![CI](https://github.com/a252937166/AlphaPilot-AI/actions/workflows/ci.yml/badge.svg)](https://github.com/a252937166/AlphaPilot-AI/actions/workflows/ci.yml)

## 当前状态

当前为 **v0.5.0 P3-M2 因子诊断 + 样本外重构验证版**：

- 数据底座：全市场证券主档与日线、盘中分钟聚合、事件日历/事件总线、可审计调度、
  断点续传、数据源熔断，以及覆盖全部审计日线的复权因子；
- 引擎层：多因子、风格、板块预测与滚动胜率、五维评分、市场情绪、信号结果评估和
  投资逻辑漂移；
- 模拟交易：可审计提醒 → 风控提案 → 人工确认或受控 `paper_auto` → 富途 `SIMULATE`
  订单 → 回填 → 组合快照/归因；
- AI 能力：事件抽取、个股解读、大盘摘要润色和复盘建议；无 LLM 时自动使用规则、模板或
  统计降级；
- 严格回测：PIT 数据截断、T 日决策/T+1 开盘成交、涨跌停/停牌/T+1 约束、全成本、
  沪深300与复权等权市场双基准，以及可复现参数快照；
- 因子诊断：13 因子 full 窗 IC/t 统计、方向源码审计、相关矩阵、train-only IC_IR 定权，
  以及冻结 test 窗的 v1/v2 同协议对照；
- 样本外结论：`composite-v2` test IC 为 `+0.0357`，但 t=`1.669` 不显著；扣成本多头
  `-18.53%`、相对等权 `-1.10pp`，因此预注册裁定为“❌ 仍失败”。权重未在 test 后调整；
- 产品界面：Vue 3 + ECharts 的 9 个真实产品页、“因子诊断”研究 tab、通知中心、
  日期/来源/模型口径和中文降级；
- 质量门：strict mypy、Ruff、609 项离线 pytest、前端类型检查与生产构建。

**实盘交易保持硬禁用。** REAL 路径同时要求
`futu_enable_trade + live_trading_enabled + confirmation="SUBMIT_REAL_ORDER"`，
`unlock_trade` 永不通过 HTTP 暴露；默认配置不会提交真实订单。

## 产品原则

1. 数值模型负责收益概率和风险分布，LLM 不直接编造未来价格。
2. LLM 用于公告理解、事件抽取、解释和交互报告。
3. 多智能体用于重大事件的情景推演，不替代统计回测。
4. 所有结论必须包含 `as_of`、模型版本、置信度和失效条件。
5. 真实事实、模拟事实和模型推断分库存储。
6. 自动化先覆盖抓数、选股、追踪、提醒、复盘和模拟交易，再逐步开放实盘。

## 快速启动

### 后端

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -e ".[dev]"
make api-start
make api-status
```

打开：

- API 文档：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/health`

macOS 本地开发由 LaunchAgent `com.alphapilot.api` 设置 `KeepAlive=true`；改动后端后运行
`make api-restart`。日志位置由 `make api-status` 输出。也可用 `make run` 启动前台开发进程，
但不要和受管进程同时占用 8000 端口。

### 前端

```bash
cd apps/web
npm install
npm run dev
```

打开 `http://127.0.0.1:5173`。

Apple Silicon 验收环境使用 arm64 Node 22；若本机有多套 Node，请先确认 `node -p process.arch`
输出 `arm64`，再执行 `npm run build`。

### Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

默认启动：

- Web：`http://127.0.0.1:5173`
- API：`http://127.0.0.1:8000`
- PostgreSQL：`127.0.0.1:5432`
- Redis：`127.0.0.1:6379`

> PostgreSQL 容器目前是迁移预览基础设施。应用尚有版本化迁移、方言 upsert、UTC/JSONB、
> sequence 和并发测试阻断，不能把 `docker compose up` 视为可切换生产事实源。

## 数据源配置

默认 `auto`：日线走 `baostock → akshare → futu` 故障转移，快照走 `futu → akshare`，
全部失败时回退本地数据库缓存。可用值：`auto` / `mock` / `akshare` / `baostock` / `futu`。

```env
ALPHAPILOT_DEFAULT_DATA_PROVIDER=auto
ALPHAPILOT_BAOSTOCK_SOCKET_TIMEOUT_SECONDS=2
ALPHAPILOT_BAOSTOCK_LOCK_TIMEOUT_SECONDS=1
```

> 注意：东方财富（AKShare 历史行情上游）会按出口 IP/TLS 指纹间歇性封锁请求，
> 因此日线主源是 BaoStock；AKShare 作为备源保留。BaoStock 的连接、接收与进程内锁等待
> 均有硬超时；连接失效后会丢弃会话并继续走故障转移链，避免阻塞 API 工作线程。

严格回测的复权因子可配置 Tushare token：

```env
ALPHAPILOT_TUSHARE_TOKEN=
```

低积分账号实测 `adj_factor` 可能只有每小时一次配额；同步器会明确记录配额错误，并按证券锁定
BaoStock/Sina 后复权来源，绝不把不同绝对标尺的因子静默拼接。当前本地审计库复权证券和
日线行覆盖率均为 100%。

巨潮资讯（可选，公告接口无需凭据即可用；公司档案需要 WebAPI 凭据）：

```env
ALPHAPILOT_CNINFO_ACCESS_KEY=你的AccessKey
ALPHAPILOT_CNINFO_ACCESS_SECRET=你的AccessSecret
```

数据库默认 SQLite（`data/alphapilot.db`，启动自动建表）。PostgreSQL 目标 URL 形式如下，
但当前**禁止仅改这一项直接切库**：

```env
ALPHAPILOT_DATABASE_URL=postgresql+psycopg://alphapilot:alphapilot@127.0.0.1:5432/alphapilot
```

先运行不联网、不读生产库的就绪检查：

```bash
.venv/bin/python scripts/check_postgres_readiness.py --project-root .
```

所有 blocker 和数据对账签字完成后才能迁移，详见
[`docs/operations/postgresql-readiness.md`](docs/operations/postgresql-readiness.md)。

安装中国市场数据扩展：

```bash
pip install -e ".[cn-data]"
```

切换富途行情：

```bash
pip install -e ".[futu]"
make futu-start
ALPHAPILOT_DEFAULT_DATA_PROVIDER=futu
ALPHAPILOT_FUTU_HOST=127.0.0.1
ALPHAPILOT_FUTU_PORT=11111
```

富途代码格式示例：`SH.600000`、`SZ.000001`、`HK.00700`、`US.AAPL`。
完整接口、复杂参数和交易门禁见 [`docs/FUTU_INTEGRATION.md`](docs/FUTU_INTEGRATION.md)。

### 受控模拟自动交易

默认仍关闭。启用时必须同时满足以下本地配置：

```env
ALPHAPILOT_SCHEDULER_ENABLED=true
ALPHAPILOT_FUTU_ENABLE_TRADE_QUERY=true
ALPHAPILOT_FUTU_ENABLE_TRADE=true
ALPHAPILOT_PAPER_TRADING_ENABLED=true
ALPHAPILOT_TRADING_MODE=paper_auto
ALPHAPILOT_PAPER_AUTO_TRADING_ENABLED=true
ALPHAPILOT_PAPER_AUTO_MAX_ORDERS_PER_DAY=3
ALPHAPILOT_PAPER_AUTO_MAX_ORDER_NOTIONAL_PCT=0.02
ALPHAPILOT_LIVE_TRADING_ENABLED=false
```

`paper_auto_trade` 在 A 股交易日 09:35、13:35、14:35 重算自选提醒，并且只处理最新、
未过期、来源可审计、置信度达标且目标区间与实时价格同量级的方向性信号。同标的一天最多一次，
单次任务最多提交一单，每日最多三单；每次下单前重新读取富途实时价格、模拟账户、持仓和未完成
委托，并重新执行全部风控。盘外、节假日、陈旧行情、Kill Switch 或任一门禁不满足时只记录
JobRun，不提交订单。

该模式用于持续验证模拟策略，不代表已获得可交易 Alpha。P3-M2 样本外重构仍未通过，
当前研究里程碑必须保持 `ALPHAPILOT_TRADING_MODE=research` 与
`ALPHAPILOT_PAPER_AUTO_TRADING_ENABLED=false`；`composite-v2` 未经 M3 多年 PIT 样本确认前
不得恢复自动模拟盘。REAL 路径和 `unlock_trade` 安全边界没有因模拟自动化而放宽。

## LLM 配置与降级

LLM 客户端使用 provider-neutral 的 OpenAI-compatible HTTP 契约：

```env
ALPHAPILOT_LLM_BASE_URL=
ALPHAPILOT_LLM_API_KEY=
ALPHAPILOT_LLM_MODEL=qwen3.6-flash
ALPHAPILOT_LLM_PURPOSE_MODELS={"stock_insight":"qwen3.7-plus"}
```

Qwen 是当前部署，但业务层不写死供应商名称；兼容相同 HTTP 契约的模型可以通过 URL、key 和
模型名切换。当前请求为 Qwen 保留 `enable_thinking=false`。Claude CLI、Codex CLI 等进程式
后端仍需要独立 transport adapter、沙箱、超时和审计设计，留待后续独立里程碑；v0.5 不宣称
已支持。

没有 LLM 配置时，总览/解读/监测/复盘继续返回规则、模板或统计结果并标注 `source`；没有
Futu/OpenD 时，缓存模块保留日期与来源，实时模块返回中文原因，不用随机数或昨日值冒充实时值。
北向盘中数据因官方停发明确显示不可用；当前接口不提供跨日分时，5 日分时不做拼接伪造。

## API 概览

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/health` | 健康检查（DB、cninfo、Futu、交易开关） |
| GET | `/v1/dashboard/overview` | 总览聚合：状态机+指数+板块+自选+提醒+AI摘要 |
| GET | `/v1/jobs/runs` | 调度任务运行、状态和 stats 审计 |
| GET | `/v1/screens/latest`、`/diff`、`/style-exposure` | 最新选股、状态变化和风格暴露 |
| POST | `/v1/screens/run` | 运行全市场/指定股票筛选并落库 |
| GET | `/v1/factors/weights`、`/v1/style/daily` | 因子权重与每日风格分布 |
| GET | `/v1/stocks/{symbol}/overview`、`/bars`、`/forecast` | 行情头、K 线和概率预测 |
| GET | `/v1/stocks/{symbol}/score`、`/factors`、`/insight` | 五维评分、因子明细和 AI/规则解读 |
| GET | `/v1/stocks/{symbol}/calendar`、`/signals` | 事件日历和可审计 B/S 信号 |
| GET | `/v1/sectors/forecast`、`/lifecycle` | 多周期板块预测、整体滚动胜率和生命周期 |
| GET | `/v1/sectors/overbought`、`/reversal`、`/{plate_code}/leaders` | 超买、反转和联动个股 |
| GET | `/v1/market/intraday`、`/sentiment`、`/breadth-full` | 分时、情绪和全市场宽度 |
| GET | `/v1/market/indices`、`/monitor-feed`、`/cross` | 指数、监测事实流和跨市场信号 |
| GET/POST/DELETE | `/v1/watchlist`（含 `/track`、`/summary`） | 自选 CRUD、漂移追踪和汇总 |
| GET/POST | `/v1/alerts` (+`/refresh`, `/{id}/acknowledge`) | 提醒查询/重算/确认 |
| GET | `/v1/notifications`、`/unread-count` | 通知中心与未读数 |
| POST | `/v1/notifications/read` | 标记通知已读 |
| GET | `/v1/events` | 统一事件流 |
| GET | `/v1/portfolio/account`、`/overview`、`/attribution` | 模拟账户、持仓和组合归因 |
| GET | `/v1/trades/proposals`、`/v1/orders` | 提案与券商订单审计流水 |
| POST | `/v1/trades/evaluate` | 只做风险预检，不创建提案 |
| POST | `/v1/trades/proposals/{id}/execute` | 人工确认或调度器批准后的 SIMULATE 执行；REAL 仍受三重门禁 |
| GET | `/v1/disclosures/{symbol}` (+`/sync`) | 巨潮公告查询与同步 |
| GET/POST | `/v1/reports/daily` (+`/generate`) | 日报、信号/组合归因和 `sector_call_excess` |
| POST | `/v1/backtest/run` | 创建只读 PIT 回测；异步执行并返回可轮询 run |
| GET | `/v1/backtest`、`/{id}`、`/{id}/daily`、`/{id}/report` | 回测档案、状态、日序列和诚实结论 |
| GET | `/v1/backtest/factors/ic`、`/factors/diagnosis` | 持久化单因子 IC 与 13 因子方向/相关/权重诊断 |
| GET | `/v1/backtest/compare?v1=&v2=` | 仅比较同 test 协议的 v1/v2，并按预注册三档门裁定 |
| POST | `/v1/scenarios/run` | 运行本地多智能体情景模拟 |
| GET | `/v1/futu/status` / `/capabilities` | OpenD 状态与能力目录 |
| POST | `/v1/futu/quote/{method}` | 调用受审计的行情、筛选或订阅方法 |
| POST | `/v1/futu/trade/{context}/{method}` | 调用受门禁保护的交易查询或变更方法 |

## 仓库结构

```text
apps/web/                    Vue 3 多页面仪表盘（vue-router + ECharts）
src/alphapilot/api/          FastAPI 路由（dashboard/watchlist/alerts/sectors/reports 等）
src/alphapilot/data/         Mock、AKShare、BaoStock、富途 Provider + auto 故障转移路由
src/alphapilot/db/           SQLAlchemy 引擎与 ORM 模型（SQLite 生产事实源 / PostgreSQL 就绪审计）
src/alphapilot/cninfo/       巨潮资讯客户端（OAuth2 token + 公司档案 + 公告）
src/alphapilot/services/     服务层：行情缓存、自选追踪、板块、复盘、总览聚合、AI 摘要
src/alphapilot/features/     特征工程
src/alphapilot/prediction/   概率预测和市场状态
src/alphapilot/screening/    自动选股
src/alphapilot/backtest/     复权、PIT、成交约束、IC/相关诊断、train 定权、回测与结论报告
src/alphapilot/alerts/       自动提醒规则
src/alphapilot/scenario/     MiroFish 式情景模拟契约
src/alphapilot/risk/         交易风控门禁
src/alphapilot/trade/        交易网关边界（默认禁用）
config/                      数据源、股票池、风控样例
scripts/                     初始化、日报、GitHub 发布脚本
docs/                        产品、架构、数据、风控、UI 设计稿和路线文档
tests/                       单元和 API 测试（conftest 隔离测试库并规避 futu 线程挂起）
```

## MiroFish 集成边界

本项目不复制 MiroFish 源代码。MiroFish 保持为独立服务，只通过适配器接收金融场景输入并返回模拟结果。这样可以：

- 避免将社交平台行为模型误当成市场价格模型；
- 隔离真实图谱和模拟图谱；
- 控制 AGPL 网络服务义务和商业项目边界；
- 让量化预测、风险和交易服务保持独立可审计。

详见 [`docs/MIROFISH_INTEGRATION.md`](docs/MIROFISH_INTEGRATION.md)。

## 开发命令

```bash
make install
make lint
make test
make run
make futu-start
make futu-stop
```

## 安全声明

- 本项目不构成投资建议。
- 基线模型只用于验证工程链路，不代表可交易 Alpha。
- 免费数据接口可能变更、延迟或存在商业使用限制。
- 实盘前必须启用持仓、额度、数据时效、重复订单、日内亏损和 Kill Switch 检查。
- 任何真实交易都必须由账户所有者明确授权，并满足券商、交易所和适用监管要求。

## 下一步

P3-M1 的严格回测证据见
[`docs/phase3/01_P3.1_backtest_framework.md`](docs/phase3/01_P3.1_backtest_framework.md)，
P3-M2 的 13 因子诊断、train-only 重构与样本外失败结论见
[`docs/phase3/02_P3.2_factor_diagnosis_and_rebuild.md`](docs/phase3/02_P3.2_factor_diagnosis_and_rebuild.md)。
下一研究里程碑应先补多年 PIT 历史和新的 alpha 来源，再预注册 M3 walk-forward；不得在本次
91 日 test 窗继续调参。基础设施候选包括 PIT 查询批量化、可恢复任务队列、
PostgreSQL/TimescaleDB 迁移、多 transport LLM 适配器和完整身份边界；长期任务池见
[`docs/BACKLOG.md`](docs/BACKLOG.md)。
