# AlphaPilot AI

> 面向 A 股为主、可扩展港股与美股的概率预测、自动选股、持续追踪、板块研判、大盘监控与交易辅助平台。

[![CI](https://github.com/a252937166/AlphaPilot-AI/actions/workflows/ci.yml/badge.svg)](https://github.com/a252937166/AlphaPilot-AI/actions/workflows/ci.yml)

## 当前状态

当前为 **v0.2 架构版**，在 Foundation 骨架上补齐了持久化、数据源故障转移、巨潮公告接入与完整多页面仪表盘：

- 统一市场数据 Provider 协议 + **auto 故障转移链**（日线 baostock→akshare→futu；快照 futu→akshare）；
- Mock / AKShare / **BaoStock** / 富途四个数据源实现；
- **巨潮资讯（深证信 WebAPI + 公开公告接口）**：公司档案与公告抓取入库（凭据仅存本地 `.env`）；
- **SQLAlchemy 数据库层**（SQLite 开箱即用，可切 PostgreSQL）：证券主档、日线缓存、公告、自选/投资逻辑、预测快照、提醒、选股记录、交易提案、板块快照、复盘报告；
- 富途 OpenD 全量行情/订阅桥接、证券/期货/加密交易查询及受控订单边界；
- 透明基线预测 + 预测历史落库与 **1 日方向命中率评估**；
- 自选追踪（信号/置信度/逻辑状态）与提醒持久化；
- **板块强度引擎**（富途板块抽样 + 单次快照聚合）与样本市场宽度；
- 大盘状态分类、每日自动复盘报告（可选 LLM 摘要，默认规则模板）；
- 交易提案审计流水：风控校验 → 人工批准/拒绝（执行网关默认禁用）；
- **Vue 3 多页面仪表盘**（vue-router + ECharts，按 `docs/AlphaPilot-AI-UI-16x9/` 设计稿实现 8 个页面）；
- Docker、CI、测试（pytest 退出挂起已修复）、配置与项目路线文档。

**实盘交易默认硬禁用。** 当前代码不会在默认配置下提交真实订单。生产选股和交易前，必须完成 Point-in-Time 数据建设、滚动回测、概率校准、交易成本建模、券商授权和风险审批。

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
uvicorn alphapilot.main:app --reload --host 127.0.0.1 --port 8000
```

打开：

- API 文档：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/health`

离线选股示例：

```bash
curl -X POST http://127.0.0.1:8000/v1/screens/run \
  -H 'Content-Type: application/json' \
  -d '{"symbols":["600000","000001","000333","600519"],"top_n":3,"provider":"mock"}'
```

### 前端

```bash
cd apps/web
npm install
npm run dev
```

打开 `http://127.0.0.1:5173`。

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

## API 概览

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/health` | 健康检查（DB、cninfo、Futu、交易开关） |
| GET | `/v1/dashboard/overview` | 总览聚合：状态机+指数+板块+自选+提醒+AI摘要 |
| POST | `/v1/screens/run` | 多股票自动评分和选股（结果落库） |
| GET | `/v1/screens/latest` / `/universe` | 最近一次选股 / 默认股票池 |
| GET | `/v1/stocks/{symbol}/forecast` | 单股 1/5/20 日概率预测 |
| GET | `/v1/stocks/{symbol}/bars` | 日线（带 DB 缓存与来源标注） |
| GET | `/v1/stocks/{symbol}/overview` | 报价+巨潮档案+预测+提醒+公告 |
| GET/POST/DELETE | `/v1/watchlist` (+`/track`) | 自选 CRUD 与追踪增强视图 |
| GET/POST | `/v1/alerts` (+`/refresh`, `/{id}/acknowledge`) | 提醒查询/重算/确认 |
| GET | `/v1/sectors/strength` | 板块抽样强度排名 |
| GET | `/v1/market/regime` / `/indices` / `/breadth` | 大盘状态 / 指数 / 样本宽度 |
| GET | `/v1/disclosures/{symbol}` (+`/sync`) | 巨潮公告查询与同步 |
| GET/POST | `/v1/reports/daily` (+`/generate`) | 每日复盘报告 |
| POST | `/v1/scenarios/run` | 运行本地多智能体情景模拟 |
| POST | `/v1/trades/evaluate` / `/proposals` | 风控评估 / 提案审计流水与批准拒绝 |
| GET | `/v1/futu/status` / `/capabilities` | OpenD 状态与能力目录 |
| POST | `/v1/futu/quote/{method}` | 调用受审计的行情、筛选或订阅方法 |
| POST | `/v1/futu/trade/{context}/{method}` | 调用受门禁保护的交易查询或变更方法 |
| WS | `/v1/futu/stream` | 接收报价、K 线、逐笔、盘口和提醒等订阅推送 |

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

**二期开发详细设计已定稿：[`docs/PHASE2_DESIGN.md`](docs/PHASE2_DESIGN.md)**（对照 `docs/AlphaPilot-AI-UI-16x9/` 九张设计稿逐功能拆解：全市场数据底座、多因子/风格/板块预测/情绪引擎、Thesis 漂移、富途模拟交易闭环、LLM 事件抽取与解读、8 页前端二期，约 9 周四个里程碑）。长期任务池见 [`docs/BACKLOG.md`](docs/BACKLOG.md)。
