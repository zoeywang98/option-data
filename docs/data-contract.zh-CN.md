# 数据契约 v1.0

本契约对应一个 symbol 在一个统一 as-of 时刻的结构快照。跨平台数据必须保留各自 `data_timestamp` 和 `retrieved_at`；run 的 `timestamp` 只是归一化分析时点，不能覆盖原始时间。

## 1. `manifest.json`（必须）

除 run_id、symbol、asset_type、spot、previous_close、session、expirations 外，必须记录：

- `contract_version`：当前为 `1.0.0`。
- `timestamp`、`capture_started_at`、`capture_completed_at`：全部带 UTC offset。
- `timezone`：IANA 名称，例如 `America/New_York`。
- `products`：SPX/SPXW/ES/SPY/QQQ 等产品及各自合约乘数。
- `sources` 与 `source_definitions`：抓取时刻、数据时刻、实时/延迟、延迟秒数、perspective、单位、正负号。
- `missing_files`：未能取得的文件，不允许静默缺失。

`source_definitions` 的信息必须来自供应商文档或已验证的导出定义。无法确认时填 `unknown` 并让 strict 校验失败，不能猜测。

## 2. `underlying_1m.csv`（必须）

原始字段：1 分钟 OHLC、bid/ask、size、volume、VWAP、trade_count、前收、当日开盘、夜盘/当日高低点和 session。

派生字段：新高/新低、VWAP 方位、HH/LL、ATR、realized vol、价格速度/加速度、距 call wall、put wall、gamma flip 的距离。派生值必须由时间点不晚于该行的数据计算，避免 look-ahead。

SPX run 建议写入多个 symbol 的行：SPX、ES、SPY、NQ/QQQ。指数没有真实可交易 bid/ask 或 volume 时保持空值，不用 ETF 数值冒充。

## 3. `option_chain.csv`（必须）

每个合约、每个快照一行。包括 quote、size、last、volume、OI、IV、Delta/Gamma/Vega/Theta/Vanna/Charm/Vomma、现价、quote timestamp、利率、股息和 multiplier。

覆盖范围：所有可用 strike，最低 5Δ–95Δ；0DTE、1DTE、后续几个周度、月度、约 30D/60D。Cliff 算法所用 strike 必须为固定间距并保留相邻 strike 的 mid、精确 expiry 和同一 as-of 的 spot。

历史比较字段包括 `oi_today`、`oi_next_day`、`oi_change`、session volume 以及 IV/Delta/Gamma/mid 的变化。OI 的次日更新不能回填成盘中已知事实。

## 4. 历史快照节奏（必须）

- 前一天 15:58 ET
- 当天开盘前
- 09:31 ET
- 盘中每 5 分钟
- 15:58 ET 和收盘后
- 次日早晨 OI 更新后

常态频率：underlying 1 分钟，flow 逐笔或 1 分钟，链/IV/Greeks 5 分钟；重大行情和 0DTE 最后一小时提高到 1 分钟。

这些快照用于拆分机械 Greek 变化、IV/Vanna、时间/Charm 和疑似开平仓。最终 dealer inventory 依然是推断。

## 5. `iv_surface.csv`（必须）

同时保存 fixed strike 与 fixed delta 坐标。逐点字段包括 expiry、DTE、strike、call/put、delta、IV、spot；摘要包括 ATM IV、10Δ/25Δ wings、RR25、BF25、call/put/wing skew、front/back IV、term slope、RV 和 IV-RV。

`RR25 = 25Δ Call IV - 25Δ Put IV`。`stickiness` 记录 sticky-strike、sticky-delta 或 mixed 的判定。

用于观察上涨时 call IV 是否确认、下跌时 call IV 是否同步下降、put wing 是否突然变贵、0DTE 与后端 IV 的相对变化。

## 6. `dealer_exposure.csv`（必须）

逐 strike/expiry/timestamp 保存 GEX、Vanna/VEX、Charm、Delta exposure 及 Gamma/Vanna/Charm gradient、Delta Change；同时保留全量 net/call/put GEX、net Vanna/Charm/Delta、zero gamma 和 gamma flip。

每行必须保留 `perspective`、`gex_unit`、`contract_multiplier` 和 `source`。平台级计算细节（是否乘 OI/100/spot、IV、利率、expiry 范围）放入 manifest 的 source definition。

VolSignals 的 Positions by Strike、各梯度、Delta Change 和 3D/heatmap 原图应放在数值数据旁边。若平台只提供图片，截图必须完整包含 symbol、spot、指标、轴、色标、expiry 和生成时间。

## 7. `option_flow.csv` 与 `option_flow_1m.csv`（必须/建议）

逐笔数据保留 trade 与 NBBO、size/premium、OI/volume、spot/IV/Delta、交易所/condition、sweep/block/multi-leg、aggressor 和 opening/closing inference。

1 分钟聚合保留 ask/bid call/put premium、sweep count、call/put aggression 以及同合约先 ask 后 bid 的行为。方向和开平仓字段都是 inference，不能标为事实。

## 8. `levels.json`（必须）

保存 gamma flip、zero gamma、call/put wall、HVL、volatility trigger、blind spots、正负 gamma 区、liquidity vacuums 和 expected move。`metadata` 对每个 level 记录来源、生成时间、expiry、盘中是否动态、上下方 Gamma 密度。

## 9. OptionDepth 3D（强烈建议）

优先导出 timestamp、spot grid、time/expiry grid、strike、GEX/Vanna/Charm/Delta exposure。若只能截图，必须带 symbol、spot、指标名称、时间轴、价格轴、正负色标、生成时间和当前 expiry。

## 10. `cliff_levels.csv`（必须）

保留相邻 strike 的 call/put mid、间距、prob above/below 和 density；摘要包括 call/put cliff、floor、upper boundary、tail top、peak density、spot gap、vitality 和跨 expiry 对齐。

`comparison_anchor` 标识 current、prior_1558、open_0931、5d 等比较基准，避免不同基准混在同一变化列。

## 11. `dark_pool.csv`（个股必须，SPX 可选）

逐笔字段及价位累计量、DP%、1/5/20 日和 4–6 周窗口、吸筹/出货斜率、成本区方位、突发大额出货和 floor 支撑。DP 是方向安全闸，不单独作为点火信号。

## 12. `short_data.json`（个股强烈建议）

保存 short interest、float%、days-to-cover、可借股、borrow fee、utilization、FTD、下一公布日和历史变化。不同字段可能有不同 as-of 日期，必须逐字段记录。

## 13. `market_regime.csv`（SPX 必须）

核心为 VIX/VIX1D/VIX9D/VVIX、前两个月 VIX futures 与 slope、SPX IV/RV、ES/NQ/RTY、DXY、2Y/10Y。增强字段包括 breadth、TICK、up/down volume、put/call、相关性、CTA/vol-control 估计和指数相对强弱。

## 14. `events.json`（必须）

经济数据、earnings、除息、OPEX/MOPEX/QOPEX、rebalance、FOMC/CPI/NFP、国债拍卖、假日/提前收市。每个事件记录时间、预期、是否已公布、距事件时间、AM/PM settlement 和是否 0DTE/MOPEX。

## 15. `positions.json`（按需）

仅在请求具体操作建议时提供：quantity、long/short、entry/current、expiry/strike/call_put、Greeks、max loss、PnL、计划持有期、stop。没有持仓数据只分析市场结构，不输出个性化仓位动作。

## 缺失值、布尔值与单位

- CSV 缺失值使用空字段；JSON 使用 `null`。不要使用 0 代替未知。
- CSV 布尔值统一为小写 `true` / `false`。
- 价格使用对应产品的报价货币；premium 和 notional 为美元时在 source units 中明确。
- IV 与 Greeks 的缩放（小数或百分点）必须写入 source units。
- 所有衍生字段的公式版本建议写入 manifest 的 `derivations` 扩展对象。
