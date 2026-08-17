# 采集器边界

当前仓库内置 QuantData direct REST 的 exposure-by-strike adapter。它保存 GAMMA/VANNA/DELTA 三份原始 JSON，输出规范化 `dealer_exposure.csv`，并把请求参数、数据时刻与口径写回 manifest。

VolSignals、Unusual Whales、MenthorQ、SpotGamma 的账户访问和浏览器会话不应硬编码在公开仓库。对应 adapter 应遵守以下接口：

1. 凭证只从环境变量、系统 keychain 或外部会话读取。
2. 原始响应写入 `raw/<source>/`；截图写入 `screenshots/`。
3. 规范化文件只能写契约字段，额外原始字段保留在 raw。
4. 同时更新 manifest 的 `sources` / `source_definitions`。
5. 抓取失败要更新 `missing_files` 和错误日志，不能输出全 0 文件。
6. 供应商 UI、API 和许可条款发生变化时，应停止 strict 发布，先复核字段映射。

平台 adapter 的优先级：

- VolSignals：dealer exposure、Positions by Strike、gradients、Delta Change、OptionDepth。
- UW：逐笔 option flow、dark pool、short data。
- MenthorQ/SpotGamma：关键墙位、gamma/liquidity map；同名指标只选择一套。
- 有授权的 OPRA/行情源：完整期权链和 1 分钟标的行情。
- 事件日历源：宏观事件和结算属性。

公开仓库提供数据契约与无秘密 adapter；带账号的自动化应放在私有部署层，通过这个目录协议交付结果。
