# 来源选择、符号和口径

同类数据选择一个质量最高、时间最贴近 run 的来源。来源变化时不要直接拼接成连续序列；先做 overlap 校准并在 manifest 记录 source change。

## 推荐职责分工

- 标的 1 分钟与完整期权链：使用有相应交易所授权、能给出 quote timestamp 的实时或历史供应商。
- dealer exposure 与 gradients：VolSignals 数值导出优先；若只能截图，保留完整截图并将数值文件标为缺失。
- 实际 option flow、个股 dark pool、short 数据：Unusual Whales。
- 墙位与 dealer map：在 MenthorQ、VolSignals、SpotGamma 和自算中选择定义最透明、时间最接近的一套；不要对同名墙位求平均。
- GEX/DEX/VEX by strike 与历史 session 对比：QuantData REST API 可作为数值来源，但必须保留 `greekMode`、`representationMode`、`sessionDate`/`snapshotTime` 和原始响应。
- Cliff/CDF/PDF：由同一时刻的 option chain 自算并记录算法版本。

以上是来源路由，不是对供应商口径的替代定义。

## manifest 中每个平台必须填写

```json
{
  "name": "VolSignals",
  "retrieved_at": "2026-08-16T15:58:04-04:00",
  "data_timestamp": "2026-08-16T15:58:00-04:00",
  "feed_type": "realtime",
  "delay_seconds": 4,
  "perspective": "dealer",
  "contract_multiplier": 100,
  "sign_convention": {
    "gex_positive": "VERBATIM_VENDOR_DEFINITION",
    "vanna_positive": "VERBATIM_VENDOR_DEFINITION",
    "charm_positive": "VERBATIM_VENDOR_DEFINITION",
    "put_gamma_handling": "VERBATIM_VENDOR_DEFINITION"
  },
  "units": {
    "gex": "VERBATIM_VENDOR_UNIT",
    "vanna": "VERBATIM_VENDOR_UNIT",
    "charm": "VERBATIM_VENDOR_UNIT",
    "iv": "decimal_or_vol_points"
  },
  "calculation": {
    "oi_scaled": true,
    "multiplier_scaled": true,
    "spot_scaled": "per_1_point_or_per_1_percent",
    "expiry_scope": "selected_or_all",
    "iv_source": "vendor",
    "interest_rate": "vendor"
  }
}
```

`VERBATIM_VENDOR_*` 必须替换为供应商当时版本的明确定义。没有文档时填 `unknown`；不要根据图形颜色倒推。

## 不能直接互换的常见口径

- customer long gamma 与 dealer long gamma 正负相反。
- contract gamma、美元每 1 点、美元每 1% 的数量级不同。
- 有的平台对 put gamma 先保留数学正值，再按推测仓位加符号；有的平台直接展示净 dealer exposure。
- VEX 可能指 Vanna exposure，也可能是供应商自定义暴露值。
- Charm 可能按 calendar time、trading time，或 dealer/customer 视角展示。
- 单 expiry、0DTE-only、全期限汇总不能直接比较。
- 盘中 volume-based exposure 与 OI-based exposure 不是同一库存代理。

因此任何跨平台对比都必须先归一：同一 timestamp、spot、expiry scope、perspective、multiplier 和单位，然后才能比较方向与变化。
