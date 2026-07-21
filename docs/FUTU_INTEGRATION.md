# Futu OpenD 集成

AlphaPilot 通过本机命令行版 Futu OpenD 和 `futu-api` 连接行情与交易上下文。OpenD 只监听
`127.0.0.1:11111`，运维命令只监听 `127.0.0.1:22222`。

## 启动与状态

```bash
make futu-start
curl http://127.0.0.1:8000/v1/futu/status
make futu-stop
```

`make futu-start` 从 macOS 钥匙串读取登录凭据，在仅当前用户可读的运行目录生成 MD5
登录配置，并通过 launchd 启动无 GUI 的 OpenD。明文密码不会写入仓库、启动参数或日志。

## API 表面

```text
GET  /v1/futu/status
GET  /v1/futu/capabilities
POST /v1/futu/quote/{method}
POST /v1/futu/trade/{security|future|crypto}/{method}
WS   /v1/futu/stream
```

`/capabilities` 返回当前 SDK 中每个受审计方法的签名、可用性、类别和启用状态。当前桥接层覆盖：

- 行情快照、基础信息、搜索、交易日、市场状态和历史 K 线；
- 实时报价、K 线、逐笔、盘口、经纪队列和订阅查询；
- 板块、筛选器、财务、公司行为、机构持仓、宏观和新闻；
- 期权、期货、窝轮、事件合约和相关分析接口；
- 自选股、到价提醒及期权事件提醒（独立开关）；
- 证券、期货、加密货币的账户、资产、持仓、订单和成交查询；
- 模拟/真实订单、改单和撤单方法（默认禁用并受多重门禁保护）。

SDK 的内部网络、回调、测试方法不会暴露。`unlock_trade` 永远不会通过 AlphaPilot HTTP API
暴露。

## 行情示例

快照：

```bash
curl -X POST http://127.0.0.1:8000/v1/futu/quote/get_market_snapshot \
  -H 'Content-Type: application/json' \
  -d '{"args":[["HK.00700","US.AAPL","SH.600519"]],"kwargs":{}}'
```

历史 K 线：

```bash
curl -X POST http://127.0.0.1:8000/v1/futu/quote/request_history_kline \
  -H 'Content-Type: application/json' \
  -d '{
    "args":[],
    "kwargs":{
      "code":"HK.00700",
      "start":"2026-07-01",
      "end":"2026-07-18",
      "ktype":"K_DAY",
      "autype":"qfq",
      "max_count":1000
    }
  }'
```

订阅与订阅后报价：

```bash
curl -X POST http://127.0.0.1:8000/v1/futu/quote/subscribe \
  -H 'Content-Type: application/json' \
  -d '{"args":[["HK.00700"],["QUOTE"]],"kwargs":{"is_first_push":false}}'

curl -X POST http://127.0.0.1:8000/v1/futu/quote/get_stock_quote \
  -H 'Content-Type: application/json' \
  -d '{"args":[["HK.00700"]],"kwargs":{}}'
```

OpenD 对订阅额度和退订时间有约束；应用应复用订阅，并至少等待一分钟再退订同一标的。

订阅推送通过 WebSocket 统一输出 JSON。连接成功后先收到 `ready`，随后可能收到
`stock_quote`、`kline`、`ticker`、`order_book`、`broker_queue`、`price_reminder`、
`option_event`、`event_contract_*` 等事件；空闲期间每 15 秒发送一次 `heartbeat`：

```javascript
const stream = new WebSocket("ws://127.0.0.1:8000/v1/futu/stream")
stream.onmessage = (event) => console.log(JSON.parse(event.data))
```

## 复杂 SDK 参数

普通 JSON 字符串可以直接表示大多数 Futu 常量。要求 SDK 对象的筛选与期权接口使用下面的编码：

```json
{
  "args": [
    "HK",
    [
      {
        "__futu_type__": "SimpleFilter",
        "attributes": {
          "stock_field": "CUR_PRICE",
          "filter_min": 10,
          "filter_max": 100
        }
      }
    ]
  ],
  "kwargs": {}
}
```

枚举对象写成：

```json
{"__futu_constant__": "WarrantMarket.HK"}
```

`StockScreenRequest`、`OptionScreenRequest` 等构建器还可以使用 `calls` 依次调用其公开构建方法：

```json
{
  "__futu_type__": "StockScreenRequest",
  "calls": [
    {"method": "add_simple_property", "args": ["CUR_PRICE"], "kwargs": {"lower": 10}},
    {"method": "add_retrieve_basic", "args": ["SECURITY_NAME"]}
  ]
}
```

可用对象类型和方法签名以 `/v1/futu/capabilities` 返回值为准。

分页键若由 SDK 返回为二进制，会编码成 `{"__bytes_base64__":"..."}`；把该对象原样放回
下一次请求的 `page_req_key` 即可。

## 交易安全开关

默认配置下，账户查询、账户资料变更和订单变更均关闭：

```env
ALPHAPILOT_FUTU_ENABLE_TRADE_QUERY=false
ALPHAPILOT_FUTU_ENABLE_ACCOUNT_MUTATION=false
ALPHAPILOT_FUTU_ENABLE_TRADE=false
ALPHAPILOT_LIVE_TRADING_ENABLED=false
```

只读账户查询可以单独开启：

```env
ALPHAPILOT_FUTU_ENABLE_TRADE_QUERY=true
```

模拟订单需要 `ALPHAPILOT_FUTU_ENABLE_TRADE=true`，并显式提交
`environment: "SIMULATE"`。真实订单还必须同时启用 `ALPHAPILOT_LIVE_TRADING_ENABLED=true`
并在每次请求中提交 `confirmation: "SUBMIT_REAL_ORDER"`。即使满足这些条件，AlphaPilot 也不会
调用 `unlock_trade`；交易解锁必须由账户所有者在 API 外完成。

加密货币交易上下文仅支持 `REAL` 环境，因此不能用于模拟下单。

## 官方资料

- [Futu OpenAPI AI 接入说明](https://openapi.futunn.com/futu-api-doc/intro/ai.html)
- [命令行 OpenD](https://openapi.futunn.com/futu-api-doc/opend/opend-cmd.html)
- [运维命令](https://openapi.futunn.com/futu-api-doc/opend/opend-cmd-monitor.html)
