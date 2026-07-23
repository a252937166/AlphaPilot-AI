# P2.4-S15 无 Futu / OpenD 降级证据

- 验收日期：2026-07-23
- 方式：先记录正常健康状态，再运行 `make futu-stop`；确认 11111 不可达后重启受管 API，
  使 `/health` 反映真实连接状态。
- 安全边界：只读页面与 GET API；未修改交易配置，未创建提案、委托或交易。

## 正常基线

- Futu SDK `10.09.6908`、OpenD server `1009`；
- OpenD/qot/trd 均已登录且健康；
- `live_trading_enabled=false`、`unlock_trade_endpoint_exposed=false`。

## 降级结果

首轮发现 Futu SDK 在 OpenD 不可达时会自行进入重连循环，使总览、指数和个股接口超过 20 秒。
S15 将其作为发布阻断修复：所有新建行情/交易查询 context 前先做 350ms TCP 探测，失败立即返回
中文 `FutuUnavailableError`；注入 SDK 的离线单测不走真实网络。

修复后的只读接口实测：

| 接口 | 结果 | 耗时 |
|---|---|---:|
| `/v1/portfolio/account` | 503，诚实说明账户不可用 | 0.05s |
| `/v1/market/indices` | 200，缓存/其他可用来源 | 3.20s |
| `/v1/dashboard/overview` | 200，独立模块有界降级 | 7.26s |
| `/v1/stocks/600519/overview` | 200，报价为空、历史 K 线保留来源 | 5.68s |
| `/v1/market/intraday?symbols=SH.000001` | 503，中文说明 OpenD 未启动 | 有界失败 |

8 个真实路由全部可进入，无白屏、console error、页面横向溢出、`undefined` 或 `NaN`：

- 总览：Futu 状态为红，板块缓存显示来源与日期；
- 个股：实时报价为 `—`，明确“行情源未知 · 行情时间缺失”，日线继续显示 BaoStock 来源；
- 自选：实时价格/盈亏为 `—`，历史评分与事件保留；最慢一次约 19 秒后完成，不是无限 spinner；
- 大盘：分时和美期模块中文降级，市场宽度/规则事实流继续可用；
- 提醒：风控 fail-closed，显示“缺少可审计实时价格、行情时间缺失、报价无审计来源”；
- 选股、板块、复盘：依赖库内已审计截面的模块保持可用。

截图：

- `degrade_no_futu_overview.png`
- `degrade_no_futu_stock.png`
- `degrade_no_futu_market.png`
- `degrade_no_futu_alerts.png`

针对 fail-fast 新增 2 个离线回归用例，分别证明行情查询和交易查询在 TCP 不可达时不会构造 Futu
context；Ruff、strict mypy 和 `tests/test_futu_client.py` 通过。

## 恢复

运行 `make futu-start` 后，11111 端口恢复；最终 `/health` 再次确认 OpenD/qot/trd 全健康，
模拟账户 GET 返回 200，受管 API 保持在线，`live=false`、`unlock=false`。
