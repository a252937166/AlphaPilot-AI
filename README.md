# AlphaPilot AI

> 面向 A 股为主、可扩展港股与美股的概率预测、自动选股、持续追踪、板块研判、大盘监控与交易辅助平台。

[![CI](https://github.com/a252937166/AlphaPilot-AI/actions/workflows/ci.yml/badge.svg)](https://github.com/a252937166/AlphaPilot-AI/actions/workflows/ci.yml)

## 当前状态

当前为 **v0.3 二期版**：

- 数据底座：全市场证券主档与日线、盘中分钟聚合、事件日历/事件总线、可审计调度、
  断点续传和数据源熔断；
- 引擎层：多因子、风格、板块预测与滚动胜率、五维评分、市场情绪、信号结果评估和
  投资逻辑漂移；
- 模拟交易：风险校验 → 提案 → 人工确认 → 富途 `SIMULATE` 订单 → 回填 → 组合快照/归因；
- AI 能力：事件抽取、个股解读、大盘摘要润色和复盘建议；无 LLM 时自动使用规则、模板或
  统计降级；
- 产品界面：Vue 3 + ECharts 的 8 个真实产品页、通知中心、日期/来源/模型口径和中文降级；
- 质量门：strict mypy、Ruff、540+ 离线 pytest 用例、前端类型检查与生产构建。

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

## 数据源配置

默认 `auto`：日线走 `baostock → akshare → futu` 故障转移，快照走 `futu → akshare`，
全部失败时回退本地数据库缓存。可用值：`auto` / `mock` / `akshare` / `baostock` / `futu`。

```env
ALPHAPILOT_DEFAULT_DATA_PROVIDER=auto
```

> 注意：东方财富（AKShare 历史行情上游）会按出口 IP/TLS 指纹间歇性封锁请求，
> 因此日线主源是 BaoStock；AKShare 作为备源保留。

巨潮资讯（可选，公告接口无需凭据即可用；公司档案需要 WebAPI 凭据）：

```env
ALPHAPILOT_CNINFO_ACCESS_KEY=你的AccessKey
ALPHAPILOT_CNINFO_ACCESS_SECRET=你的AccessSecret
```

数据库默认 SQLite（`data/alphapilot.db`，启动自动建表），切换 PostgreSQL：

```env
ALPHAPILOT_DATABASE_URL=postgresql+psycopg://alphapilot:alphapilot@127.0.0.1:5432/alphapilot
```

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
后端仍需要独立 transport adapter、沙箱、超时和审计设计，计划放在 P3，v0.3 不宣称已支持。

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
| POST | `/v1/trades/proposals/{id}/execute` | 人工确认后的 SIMULATE 执行；REAL 仍受三重门禁 |
| GET | `/v1/disclosures/{symbol}` (+`/sync`) | 巨潮公告查询与同步 |
| GET/POST | `/v1/reports/daily` (+`/generate`) | 日报、信号/组合归因和 `sector_call_excess` |
| POST | `/v1/scenarios/run` | 运行本地多智能体情景模拟 |
| GET | `/v1/futu/status` / `/capabilities` | OpenD 状态与能力目录 |
| POST | `/v1/futu/quote/{method}` | 调用受审计的行情、筛选或订阅方法 |
| POST | `/v1/futu/trade/{context}/{method}` | 调用受门禁保护的交易查询或变更方法 |

## 仓库结构

```text
apps/web/                    Vue 3 多页面仪表盘（vue-router + ECharts）
src/alphapilot/api/          FastAPI 路由（dashboard/watchlist/alerts/sectors/reports 等）
src/alphapilot/data/         Mock、AKShare、BaoStock、富途 Provider + auto 故障转移路由
src/alphapilot/db/           SQLAlchemy 引擎与 ORM 模型（SQLite 默认 / PostgreSQL 可切）
src/alphapilot/cninfo/       巨潮资讯客户端（OAuth2 token + 公司档案 + 公告）
src/alphapilot/services/     服务层：行情缓存、自选追踪、板块、复盘、总览聚合、AI 摘要
src/alphapilot/features/     特征工程
src/alphapilot/prediction/   概率预测和市场状态
src/alphapilot/screening/    自动选股
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

二期验收依据见 [`docs/PHASE2_DESIGN.md`](docs/PHASE2_DESIGN.md) 与
[`docs/phase2/P2.4-S15_ACCEPTANCE_CHECKLIST.md`](docs/phase2/P2.4-S15_ACCEPTANCE_CHECKLIST.md)。
P3 候选包括 PostgreSQL/TimescaleDB 迁移、多 transport LLM 适配器、完整身份边界、分板块独立
胜率和性能/可访问性收口；长期任务池见 [`docs/BACKLOG.md`](docs/BACKLOG.md)。
