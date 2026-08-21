# SQLite 数据库备份与恢复

AlphaPilot 的主库当前是 `data/alphapilot.db`。备份工具使用 SQLite Online Backup API，
以 `mode=ro`、`query_only=ON` 打开生产库，能够包含尚未 checkpoint 的 WAL 提交，同时不停止
API 或调度任务。

## 备份合同

每次成功备份会在 `data/backups/` 原子发布一对文件：

- `alphapilot-full-<UTC 时间>.db`
- `alphapilot-full-<UTC 时间>.manifest.json`

manifest 包含 SHA-256、文件大小、`PRAGMA quick_check`、页信息以及
`trade_proposals`、`broker_orders`、`runtime_flags`、`job_runs` 的验收证据。目录权限为
`0700`，备份、manifest、锁和日门文件为 `0600`。

手动创建并保留最近 3 份受管备份：

```bash
.venv/bin/python scripts/manage_database_backup.py backup \
  --db data/alphapilot.db \
  --backup-dir data/backups \
  --retain 3
```

验证一份备份：

```bash
.venv/bin/python scripts/manage_database_backup.py verify \
  data/backups/alphapilot-full-<UTC 时间>.db
```

只有名称符合 `alphapilot-full-*`、且具有格式与文件证据相符的 manifest 的备份才进入自动
保留轮换；手工命名文件和 manifest 缺失或异常的文件不会被自动删除。
manifest 继续记录发布时的文件系统 `device`，轮换时要求它是合法非负整数，但不把跨挂载后
可能变化的 `device` 作为等值条件；`inode`、`mtime_ns`、`ctime_ns` 和文件大小仍须精确匹配，
format、manager、filename、quick-check 与 SHA 字段形状仍须通过既有门。任一不符都会 fail
closed，不会被当成健康备份修剪。

## 每日 LaunchAgent

安装或安全更新服务：

```bash
./scripts/install_database_backup_launchd.sh
./scripts/status_database_backup_launchd.sh
```

`com.alphapilot.database-backup` 每 15 分钟唤醒一次，但日门保证只在
Asia/Shanghai 22:00 之后成功运行一次。失败不会推进
`~/Library/Application Support/AlphaPilot-AI/database-backup/last-success-shanghai-date`，
下一次唤醒会重试；若数据库和 manifest 已成功发布但日门写入失败，重试会先完整验证同日备份，
只补写日门而不会再生成一份约 5 GiB 的副本。日志位于：

- `~/Library/Logs/AlphaPilot-AI/database-backup.stdout.log`
- `~/Library/Logs/AlphaPilot-AI/database-backup.stderr.log`

## 恢复演练与正式恢复

恢复不是自动动作。正式操作前必须安排维护窗口，并确认目标备份的 manifest 和 SHA-256
验证通过。

1. 用 `manage_database_backup.py verify` 验证候选备份，并保存输出。
2. 停止 API、独立 scheduler 及其他所有生产库写入者；确认没有 `data/alphapilot.db` 的打开句柄。
3. 对当前生产库再做一份故障前救援副本；若源库已损坏，则保留原文件、`-wal`、`-shm` 供取证。
4. 把已验证备份复制到生产库同目录的临时文件，设置 `0600`，再次执行
   `PRAGMA quick_check`。
5. 仅在所有写入者已停止时，原子替换 `data/alphapilot.db`；清理与旧主库配套的
   `alphapilot.db-wal`、`alphapilot.db-shm`，不得在服务运行时删除它们。
6. 启动服务并核对 `/health`、`PRAGMA quick_check`、三闸安全态，以及
   `trade_proposals` / `broker_orders` 行数和 manifest 证据一致。

自动备份和当前手工备份位于同一磁盘，只能防逻辑损坏和误操作，不能防整盘损坏。需要另行把
已验证备份纳入 Time Machine 或异机加密归档；外传前不得泄露 `.env` 或其他凭据。
