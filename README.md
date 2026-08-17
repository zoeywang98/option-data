# option-data

期权结构研究的数据契约、运行目录生成器和校验器。目标是把不同平台的原始数据归一到同一个可追溯 run 中，并明确记录时间、延迟、单位、合约乘数、正负号和 dealer/customer 视角，防止 GEX、Vanna、Charm 因口径不一致而被反向解读。

> 仓库不包含伪造的实时行情，也不包含平台账号、API key 或受许可限制的数据。`examples/` 只有表头和结构示例；真实 run 需由已授权的数据源填充。

## 快速开始

要求 Python 3.11+，运行时无第三方依赖。

```bash
python -m option_data init \
  --symbol SPX \
  --asset-type index \
  --timestamp 2026-08-16T15:58:00-04:00 \
  --spot 7776.55 \
  --previous-close 7799.19 \
  --expiration 2026-08-16 \
  --expiration 2026-08-17

python -m option_data validate runs/2026-08-16/SPX/155800
python -m option_data validate --strict runs/2026-08-16/SPX/155800
python -m option_data publish-latest --strict runs/2026-08-16/SPX/155800
```

普通校验允许 header-only 文件，便于检查模板和进行中的抓取。`--strict` 用于可发布 run：必需 CSV 必须有数据行，必需文件不得缺失，平台口径不能仍为 unknown。

仓库内置 QuantData 的 direct REST adapter，可抓取 GAMMA/VANNA/DELTA by strike，同时保存原始 JSON 并更新 manifest。因为供应商定义可能变更，CLI 强制调用方明确传入 perspective、sign convention 和 units：

```bash
export QUANTDATA_API_KEY=...  # 不写入仓库
python -m option_data collect-quantdata-exposure runs/2026-08-16/SPX/155800 \
  --ticker SPX --timestamp 2026-08-16T15:58:00-04:00 \
  --session-date 2026-08-16 --representation PER_ONE_DOLLAR_MOVE \
  --feed-type historical --delay-seconds 0 --contract-multiplier 100 \
  --perspective dealer \
  --gex-positive '供应商当时版本的原文定义' \
  --vanna-positive '供应商当时版本的原文定义' \
  --delta-positive '供应商当时版本的原文定义' \
  --gex-unit '供应商单位' --vanna-unit '供应商单位' --delta-unit '供应商单位'
```

## 每个 run 的目录

```text
runs/YYYY-MM-DD/SYMBOL/HHMMSS/
├── manifest.json
├── underlying_1m.csv
├── option_chain.csv
├── iv_surface.csv
├── dealer_exposure.csv
├── option_flow.csv
├── option_flow_1m.csv       # 建议的 1 分钟 flow 聚合
├── cliff_levels.csv
├── optiondepth_3d.csv        # 可导出数值时优先于截图
├── levels.json
├── market_regime.csv
├── events.json
├── dark_pool.csv             # 个股必需，指数可选
├── short_data.json          # 个股强烈建议
├── positions.json           # 仅在需要持仓建议时提供
└── screenshots/
    ├── volsignals_gamma.png
    ├── volsignals_vanna.png
    ├── volsignals_charm.png
    ├── optiondepth_3d.png
    └── menthorq_levels.png
```

`latest/manifest.json` 指向最近一次通过校验的 run；`latest/state.json` 保存上一轮 regime、short state、pivot、墙位、IV/flow 状态和 invalidation。

## 数据范围

- SPX 分析同时保留 SPX、ES、SPY、NQ/QQQ；ES 要覆盖夜盘高低点和成交量。
- 标的 1 分钟；逐笔或 1 分钟 option flow；期权链/IV/Greeks 常规 5 分钟；0DTE 最后一小时建议 1 分钟。
- 链覆盖所有可用 strike，最低 5Δ–95Δ，以及 0DTE、1DTE、后续周度、月度、约 30D/60D。
- OI 至少保留前一天 15:58、开盘前、09:31、盘中、15:58、收盘后、次日 OI 更新后的快照。
- 每个时间字段均为带 UTC offset 的 ISO-8601；`manifest.timezone` 另外保存 IANA 时区。

详细字段和采样要求见 [数据契约](docs/data-contract.zh-CN.md)，平台口径见 [来源与正负号](docs/source-conventions.zh-CN.md)，机器可读 JSON 规则见 [`schemas/`](schemas/)；CSV 的规范表头由 [`option_data/contract.py`](option_data/contract.py) 唯一定义。

## 数据质量原则

- UW flow 是市场成交，不等于 dealer flow。
- dealer inventory 无法直接观察；即使有 OI 次日变化，也只能推断开/平仓和库存变化。
- 同类指标只选质量最高、时间最匹配的来源，不跨平台重复拼接后假装可比。
- 原始平台图片放在 `screenshots/`，但可导出的数值必须优先保存为 CSV/JSON。
- raw payload 建议写入未跟踪的 `raw/<source>/`，归一化后再进入本契约。

## 开发

```bash
make test
make validate-example
```

GitHub Actions 会运行单元测试并验证示例 run。
