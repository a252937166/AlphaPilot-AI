# API 与 scheduler 进程监督

生产运行把 HTTP API 与 APScheduler 拆成两个精确的 LaunchAgent：

- `com.alphapilot.api`：仅提供 HTTP、初始化 schema/迁移和运行时种子；
- `com.alphapilot.scheduler`：仅注册并运行定时任务，不执行 `create_all` 或迁移。

API plist 固定覆盖 `ALPHAPILOT_SCHEDULER_ENABLED=false`；scheduler plist 固定为 `true`，
同时钉住 research、live=false、paper_auto=false、account_mutation=false 和
BaoStock 财务 cron=false。`alphapilot.main` 仍保留嵌入式 scheduler 兼容路径，方便本地启动和
回滚，但生产两个 plist 永远不能同时启用 scheduler。

## 日常命令

```bash
./scripts/status_api_launchd.sh
./scripts/status_scheduler_launchd.sh
./scripts/restart_scheduler_launchd.sh
```

scheduler 状态不仅检查 PID，还显示 `poll_market_snapshot`、`sync_orders` 的最新 JobRun 和当前
running 行数。每个 job 还持有按数据库身份隔离的跨进程文件锁，因此 API 手动触发与自然调度
不会并行执行同名任务；该锁是本机锁，不是多主机分布式锁。

## 无双跑部署顺序

部署或回滚前先确认 `job_runs.status='running'` 为 0，并避开 cron 窗。

1. 停止 `com.alphapilot.api`，确认旧 PID 和内嵌 scheduler 一并退出。
2. 用新 plist 启动 API，确认 8000 健康且其 plist 明确
   `ALPHAPILOT_SCHEDULER_ENABLED=false`。
3. 启动 `com.alphapilot.scheduler`，确认只有一个 PID，并观察 90 秒：
   `poll_market_snapshot` 约每 60 秒、`sync_orders` 约每 30 秒产生新 JobRun，且没有同秒双份。
4. 复核 API 延迟、SQLite lock 错误、OpenD、三闸和提案/订单安全计数。

回滚必须反向执行：先停止独立 scheduler，再把 API 恢复为嵌入式 scheduler；禁止先恢复 API
内嵌调度，否则会出现双跑窗口。停止 scheduler 会先 pause 再等待正在执行的任务完成；不要用
模糊 `pkill`，也不要自动改写遗留的 running JobRun。
