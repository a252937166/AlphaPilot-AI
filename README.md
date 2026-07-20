# AlphaPilot AI

> 面向 A 股为主、可扩展港股与美股的概率预测、自动选股、持续追踪、板块研判、大盘监控与交易辅助平台。

[![CI](https://github.com/a252937166/AlphaPilot-AI/actions/workflows/ci.yml/badge.svg)](https://github.com/a252937166/AlphaPilot-AI/actions/workflows/ci.yml)

## 当前状态

这是项目的 **Foundation / MVP 骨架**，已建立可运行的端到端最小闭环：

- 统一市场数据 Provider 协议；
- 可离线运行的确定性 Mock 数据源；
- AKShare A 股历史行情适配器；
- 富途 OpenD 行情快照与历史 K 线适配器；
- 透明、可审计的基线趋势预测；
- 多股票自动评分与选股 API；
- 自动提醒规则；
- 大盘状态分类；
- MiroFish 式情景推演契约与本地启发式模拟器；
- 交易提案和硬性风险门禁；
- Vue 3 简易仪表盘；
- Docker、CI、测试、配置与项目路线文档。

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
uvicorn alphapilot.main:app --reload --host 0.0.0.0 --port 8000
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

默认使用 `mock`，不会访问外网。切换 AKShare：

```env
ALPHAPILOT_DEFAULT_DATA_PROVIDER=akshare
```

安装中国市场数据扩展：

```bash
pip install -e ".[cn-data]"
```

切换富途行情：

```bash
pip install -e ".[futu]"
# 确保本机 OpenD 已启动
ALPHAPILOT_DEFAULT_DATA_PROVIDER=futu
ALPHAPILOT_FUTU_HOST=127.0.0.1
ALPHAPILOT_FUTU_PORT=11111
```

富途代码格式示例：`SH.600000`、`SZ.000001`、`HK.00700`、`US.AAPL`。

## API 概览

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/health` | 健康检查和安全状态 |
| POST | `/v1/screens/run` | 多股票自动评分和选股 |
| GET | `/v1/stocks/{symbol}/forecast` | 单股 1/5/20 日概率预测 |
| GET | `/v1/stocks/{symbol}/alert` | 生成结构化自动提醒 |
| GET | `/v1/market/regime` | 大盘状态识别 |
| POST | `/v1/scenarios/run` | 运行本地多智能体情景模拟 |
| POST | `/v1/trades/evaluate` | 仅评估交易提案，不执行订单 |

## 仓库结构

```text
apps/web/                    Vue 3 仪表盘
src/alphapilot/api/          FastAPI 路由
src/alphapilot/data/         Mock、AKShare、富途 Provider
src/alphapilot/features/     特征工程
src/alphapilot/prediction/   概率预测和市场状态
src/alphapilot/screening/    自动选股
src/alphapilot/alerts/       自动提醒
src/alphapilot/scenario/     MiroFish 式情景模拟契约
src/alphapilot/risk/         交易风控门禁
src/alphapilot/trade/        交易网关边界（默认禁用）
config/                      数据源、股票池、风控样例
scripts/                     初始化、日报、GitHub 发布脚本
docs/                        产品、架构、数据、风控和路线文档
tests/                       单元和 API 测试
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
```

## 安全声明

- 本项目不构成投资建议。
- 基线模型只用于验证工程链路，不代表可交易 Alpha。
- 免费数据接口可能变更、延迟或存在商业使用限制。
- 实盘前必须启用持仓、额度、数据时效、重复订单、日内亏损和 Kill Switch 检查。
- 任何真实交易都必须由账户所有者明确授权，并满足券商、交易所和适用监管要求。

## 下一步

优先完成：Point-in-Time 数据仓库、财务因子、板块引擎、滚动回测、模型注册、投资逻辑追踪、富途模拟交易和自动盘前/盘后报告。完整任务见 [`docs/BACKLOG.md`](docs/BACKLOG.md)。
