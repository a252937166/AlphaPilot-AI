# Phase 2 执行守则（实现者必读，每个 Session 开工前先读本文件）

本目录是 AlphaPilot AI 二期的**实现级规格**。按编号顺序执行：
`01_P2.1_data_foundation.md` → `02_P2.2_engines.md` / `03_P2.3_paper_trading.md`（可并行）→ `04_P2.4_llm_and_frontend.md`。
每份文档由编号步骤（如 P2.1-S3）组成；**严格按步骤顺序做，每步做完必须跑完该步"验证"小节的全部命令并通过，才能进入下一步**。

---

## 1. 仓库与运行环境事实

- 项目根：`/Users/ouyangduning/Documents/project/interesting/AlphaPilot-AI`
- Python venv：`.venv/`（已装 dev 依赖）。所有命令用 `.venv/bin/python`、`.venv/bin/ruff` 等显式路径，不要裸 `python`。
- 后端：FastAPI，入口 `src/alphapilot/main.py`，由 LaunchAgent `com.alphapilot.api` 常驻本机
  `127.0.0.1:8000`（无 `--reload`，`RunAtLoad=true`、`KeepAlive=true`）。首次安装/拉起与日常管理：
  ```bash
  make api-start    # 安装 ~/Library/LaunchAgents/com.alphapilot.api.plist 并拉起
  make api-status   # 核对受管 PID、8000 监听与日志路径
  make api-restart  # 改完后端代码后必须重启
  make api-stop     # 停止服务并移除 plist；仅在明确需要关闭常驻服务时使用
  ```
  日志写入 `~/Library/Logs/AlphaPilot-AI/api.stdout.log` 与 `api.stderr.log`。启动脚本发现 8000
  被非受管进程占用时会拒绝接管且不会杀进程；不要再用模糊 `pkill`/手工 `nohup` 启动后端。
- 前端：`apps/web/`（Vue3+TS+vite），dev server 5173 常驻（vite 热更新，改前端不用重启）；构建校验用 `cd apps/web && npm run build`。
- 富途 OpenD：launchd 服务常驻 `127.0.0.1:11111`（`make futu-start` 可拉起）。行情+交易均已登录。
- 数据库：SQLite `data/alphapilot.db`（启动时 `init_db()` create_all）。`.env` 在项目根（**已 gitignore，含巨潮凭据，绝不能进 git/日志/前端**）。
- 现有测试：`tests/`，`pytest -q` 全绿是硬门槛。`tests/conftest.py` 顶部把测试环境指到临时 SQLite + mock provider，并在会话结束关闭 futu 单例——**不要改动这个机制**。

## 2. 分层与代码放置约定（新代码必须遵守）

```text
src/alphapilot/
├── core/        配置(config.py: pydantic-settings, 前缀 ALPHAPILOT_)、timeutil.iso_utc
├── db/          engine.py(get_session 上下文)、models.py(全部 ORM)、migrate.py(P2.1-S1 新增)
├── data/        行情 Provider（协议见 data/base.py）+ router.py 故障转移
├── futu/        富途桥接（白名单方法，quote_call_raw 返回 DataFrame/tuple）
├── cninfo/      巨潮客户端
├── services/    业务逻辑（纯函数风格：第一个参数 session: Session，可测试、无 FastAPI 依赖）
├── jobs/        P2.1 新增：调度任务（每个 job 一个函数，自开 session，写 job_runs）
├── engines/     P2.2 新增：因子/风格/板块/情绪等计算引擎（纯计算，输入 DataFrame/rows，输出 rows）
├── llm/         P2.4 新增：LLM 客户端与 prompt
└── api/routes/  FastAPI 路由：只做参数校验+调 service+序列化，不写业务逻辑
```

- 路由依赖注入统一用 `api/dependencies.py`（`db_session_dependency` / `futu_client_dependency` / `settings_dependency` / `get_provider`）。
- **所有从 DB 读出的 datetime 序列化必须走 `alphapilot.core.timeutil.iso_utc()`**（SQLite 丢时区，直接 `.isoformat()` 会导致前端把 UTC 当本地时间，一期已因此出过 8 小时偏差 bug）。
- 新配置一律加进 `core/config.py` 的 `Settings`（带默认值），并同步 `.env.example`（**不含真实密钥**）。
- 用户可见文案（提醒理由、报告、错误提示）一律**中文**。

## 3. 质量门（每一步的完成定义）

每个步骤完成 = 以下全部通过：

```bash
.venv/bin/ruff check src tests           # 0 error（中文标点已在 pyproject 豁免 RUF001-003）
.venv/bin/mypy src/alphapilot            # strict，0 error
.venv/bin/python -m pytest -q            # 全绿（退出码 0）
# + 该步骤文档里列出的 curl 冒烟命令，输出符合预期
```

- mypy 是 strict 模式：新函数全部带类型注解；pandas 相关用 `pd.DataFrame` 注解即可。
- 每个新引擎/服务至少 1 个离线单测（用 mock provider / 构造 DataFrame，**测试内绝不连真实网络或 OpenD**；路由测试沿用 `tests/test_api.py` 的 `StubFutuClient` + `dependency_overrides` 模式）。
- 前端步骤完成 = `npm run build` 通过 + 打开对应页面无控制台报错。

## 4. 安全不变量（违反 = 立即回滚，任何步骤不得触碰）

1. `unlock_trade` 永不通过 HTTP 暴露（futu/client.py 的 PERMANENTLY_BLOCKED_METHODS 不许动）；
2. REAL 实盘路径的三重门禁不许放松：`futu_enable_trade` + `live_trading_enabled` + 每请求 `confirmation="SUBMIT_REAL_ORDER"`；P2.3 只放开 **SIMULATE**；
3. 巨潮/任何密钥只存 `.env` 与环境变量，不进代码、示例文件、日志、前端 bundle；
4. UI 不摆假数据：数据源不可用时显示降级说明（参照现有 sector_error 处理），**禁止**用随机数/写死数字填充；
5. 风控 `risk/guardrails.py` 的既有检查只可增不可删。

## 5. 一期踩坑清单（新代码必须内建这些认知）

| # | 坑 | 应对（已有实现可参考） |
|---|---|---|
| 1 | 富途快照**没有 change_rate 字段** | 涨跌幅 = `last_price/prev_close_price - 1`（见 `data/futu_provider.py`、`services/sectors._sample_snapshot`） |
| 2 | 富途 SDK 线程非 daemon，进程不退出 | 脚本/任务用完调 `get_futu_client().close()`；测试靠 conftest 兜底 |
| 3 | 富途配额 | 快照 400 只/请求、60 请求/30s；`get_plate_stock` 10 次/30s（冷启动分批+落库缓存）；**历史K线 7 日额度 → 全市场历史只走 BaoStock** |
| 4 | BaoStock 全局单连接 | 复用 `data/baostock_provider.py` 的 `_baostock_lock` 与惰性 login；`query_all_stock(day=...)` 必须传**交易日**（周末返回空） |
| 5 | 东财(AKShare 多数接口)被间歇性 TLS 指纹封锁 | 东财类数据一律 try/except 降级为备源或跳过并记 warning；**主链路禁止依赖东财**。已验证可用：`stock_zt_pool_em`(涨停池)、`stock_info_a_code_name`(全A)、`stock_zh_a_hist`(时好时坏) |
| 6 | SQLite datetime 丢时区 | 序列化必须 `iso_utc()`（见 §2） |
| 7 | `Base.metadata.create_all` **不会**给已存在的表加列 | 改表结构必须同步写 `db/migrate.py` 的幂等迁移（P2.1-S1 建立该机制后，所有加列走它） |
| 8 | pandas `to_dict(orient="records")` 键类型是 Hashable | 需要 `dict[str,Any]` 时做 `{str(k): v for k,v in ...}`（mypy strict 会拦） |
| 9 | pyproject 的 pytest addopts=-q，再传 -q 会吞汇总行 | 判断结果看**退出码**，别 grep "passed" |
| 10 | 涨跌停幅度分板块 | 主板±10% / 创业板&科创板±20% / 北交所±30% / ST±5%，按 `securities.board/is_st` 判，勿写死 10% |

## 6. 领域数据字典（跨步骤共用口径）

- `symbol`：6 位数字字符串（如 `600519`），库内主键口径；富途码 `SH.600519`/`SZ.000001` 只在 Provider 边界转换（`5/6/9` 开头→SH，其余→SZ，函数已有：`futu_provider._normalize_symbol`、`baostock_provider._code`）；指数用富途码全称（`SH.000001`）。
- `plate_code`：富途板块码（如 `SH.BK0031`）。
- 时间：DB 内 UTC；`trade_date` 用 `date` 类型；展示层转本地。
- 金额单位：元（不做万/亿换算，前端 `fmtAmount` 负责显示）。
- `model_version` 命名：`<engine>-v<major.minor.patch>`，如 `factor-score-v1.0.0`。每个引擎输出行都必须带。

## 7. 交接与进度纪律

- 每完成一个步骤，在对应文档的步骤标题行末尾追加 `✅ done <日期>`（直接编辑文档），并 git commit（信息格式 `P2.1-S3: full-market daily bars sync`）。**不要一次性做多步再验证**。
- 遇到与规格冲突的现实（接口字段变了、配额不符），停下：在文档该步骤下追加 `> ⚠ DEVIATION:` 说明实际情况与你的处理，再继续。禁止静默改设计。
- 本文档与四份步骤文档是唯一事实源；`../PHASE2_DESIGN.md` 是架构背景（先通读一遍再开工）。
