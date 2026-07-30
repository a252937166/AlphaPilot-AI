# PostgreSQL 迁移就绪基线

当前生产事实源仍是 `data/alphapilot.db`。ORM 的 40 张表和 38 个索引能够编译为 PostgreSQL
DDL，但这不代表应用已经可以通过替换 `ALPHAPILOT_DATABASE_URL` 安全切库。

## 离线检查

```bash
.venv/bin/python scripts/check_postgres_readiness.py --project-root .
```

检查器只编译 SQLAlchemy DDL 并扫描仓库源码：

- 不连接 PostgreSQL；
- 不读取或写入生产 SQLite；
- 输出稳定的 JSON blocker ID；
- `ready=false` 时退出码为 2，可直接用于 CI 门禁。

当前基线为 `PG_SCHEMA_COMPILES=pass`，其余阻断包括：

1. `PG_VERSIONED_MIGRATIONS`：没有 Alembic 基线和版本化 revision；
2. `PG_STARTUP_DDL_OWNERSHIP`：API 启动仍执行 `create_all` / raw migrations；
3. `PG_DIALECT_NEUTRAL_UPSERTS`：通知、公告、事件和洞察等写路径使用 SQLite insert；
4. `PG_SQLITE_ONLY_TOOLS`：研究、体检、备份及 S2 回传工具仍包含 PRAGMA/SQLite SQL；
5. `PG_JSON_POLICY`：尚未裁定 JSON 与 JSONB、索引和序列化策略；
6. `PG_UTC_SESSION_POLICY`：连接没有钉住 UTC session；
7. `PG_POOL_TIMEOUT_POLICY`：API+scheduler 双进程的池、连接和 statement timeout 未定义；
8. `PG_SEQUENCE_RESEED`：批量导入后没有 `setval` 步骤；
9. `PG_INTEGRATION_TESTS`：没有真实 PostgreSQL 的 CRUD/并发/重启测试；
10. `PG_DATA_PARITY_SIGNOFF`：P3.3-S2 完成签字及数据对账尚未进入本离线检查范围。

## 解除顺序

在 P3.3-S2 全市场覆盖、PIT、幂等与架构师签字完成前，不启动生产数据迁移。

1. 建立 Alembic baseline；PostgreSQL 启动只校验 schema version，DDL 作为单独审批操作。
2. 把 SQLite 方言 upsert 收敛到数据库适配层，并为两个方言分别做并发幂等测试。
3. 明确 JSONB、UTC、连接池、statement timeout 和索引策略。
4. 在一次性 disposable PostgreSQL 上跑 schema、API、scheduler、PIT 回测和异常恢复测试。
5. 从已验证的 SQLite online backup 导入；按外键顺序装载并为每个整数 identity 执行
   `setval`。
6. 独立核对逐表行数、分块摘要、重复键、外键、PIT `available_time`、关键日期截面以及
   `trade_proposals` / `broker_orders` 安全计数。
7. 做 API+scheduler 并发写 soak、重启和回滚演练；阻断项全部转绿后才允许切换配置。

## 切换与回滚边界

正式切换必须有维护窗口：暂停 API/scheduler 写入、做最终 delta、验证 PG、再启动新连接。
原 SQLite、WAL/SHM 和已验证备份保持只读不删，作为回滚事实源。PG 失败时先停 PG 写入者，再
按反向顺序恢复 SQLite API 与独立 scheduler；禁止双写尚未验证的两个事实源。

`docker compose up` 中的 PostgreSQL 服务目前只是开发基础设施预览，不是迁移完成证明。
