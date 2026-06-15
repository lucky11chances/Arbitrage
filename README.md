# PM/Kalshi 体育套利 Data Pipeline

这个项目用于监控 Polymarket (PM) 和 Kalshi (KS) 上同一场体育赛事二元合约的价格差，按 taker 成本计算跨平台套利候选。

当前阶段对应 `ONBOARDING.md` 的 Week 1-2：先把 data pipeline 跑稳，持续收集真实盘口数据。Week 3-4 的 paper-trade simulator 还没有开始。

## 当前能做什么

- 发现 PM / KS 上的体育和电竞事件。
- 将两边市场映射到同一场比赛或同一个官方赛程事件。
- 拉取 PM / KS orderbook 的 BBO，也就是最优 bid / ask 和对应 size。
- 计算两条套利腿的 `gross_cost` 和扣 fee 后的 `net_edge`。
- 输出 latest CSV：每次运行覆盖，只保存最新一轮。
- 输出 history CSV：每次运行追加，用于之后 backtest。
- 对无法安全套用二元公式的 universe 输出 compatibility CSV，而不是硬算套利。

## 主要脚本

### `scripts/build_all_snapshots.py`

统一的一次性 snapshot runner。

默认跑全部 universe：

```bash
python3 scripts/build_all_snapshots.py --show-warnings
```

只跑指定 universe：

```bash
python3 scripts/build_all_snapshots.py --sports mlb,nba --show-warnings
```

测试时不写 history：

```bash
python3 scripts/build_all_snapshots.py --show-warnings --no-history
```

它会写 `data/*_latest.csv`，并在非 `--no-history` 模式下追加 `data/history/*_YYYY-MM-DD.csv`。

### `scripts/run_mlb_nba_loop.py`

MLB/NBA 的持续采集 loop。

当前推荐命令：

```bash
python3 -u scripts/run_mlb_nba_loop.py --interval 5 --sports mlb,nba --show-warnings --bbo-workers 20 2>&1 | tee -a data/mlb_nba_loop.log
```

特点：

- 只支持 `mlb,nba`。
- 每轮刷新 BBO / net edge。
- 默认每 5 分钟刷新一次比赛配对缓存，避免每 5 秒重复做 discovery / official schedule pairing。
- 每轮追加 history。
- 单轮如果超过 5 秒，会在 log 中输出 warning。

### `scripts/pipeline_core.py`

通用 pipeline 核心层。

负责：

- PM Gamma / PM CLOB / Kalshi Trade API 的 HTTP 请求。
- PM / KS orderbook BBO 解析。
- fee 和 net edge 计算。
- CSV latest 覆盖写入。
- CSV history 追加写入。
- 并发抓取 BBO。

关键公式与 `ONBOARDING.md` 一致：

```text
Leg A: Buy PM Yes + Buy KS No
gross_cost = pm_ask + (1 - ks_bid)

Leg B: Buy PM No + Buy KS Yes
gross_cost = (1 - pm_bid) + ks_ask

net_edge = 1 - gross_cost - PM_fee - KS_fee
```

其中：

```text
PM_fee = 0.03 * p * (1 - p)
KS_fee = 0.07 * p * (1 - p)
```

### `scripts/universe_adapters.py`

各 universe 的配对和标准化逻辑。

当前处理方式：

- MLB：使用 MLB Stats API 官方 schedule 做比赛对齐。
- NBA：使用 NBA team mapping + ESPN scoreboard schedule；当前无 active NBA 市场时输出 header-only。
- LoL：只保留能匹配 Riot LoL Esports 官方 schedule 的比赛。
- Valorant：只保留能匹配 Riot Valorant Esports 官方 schedule 的比赛。
- CS2：当前 header-only。因为还没有接入可靠的 Valve 或赛事主办方官方 schedule adapter，不做名称硬配。
- World Cup soccer：当前输出 compatibility snapshot。遇到 3-way / Tie 市场时跳过二元套利公式。

### `scripts/nba_common.py`

NBA team metadata 和名称映射工具。

负责：

- NBA 官方 team id。
- team abbreviation。
- 常见别名。
- Kalshi NBA ticker 日期解析。

### `scripts/validate_snapshots.py`

CSV 结果校验脚本。

运行：

```bash
python3 scripts/validate_snapshots.py
```

检查内容包括：

- latest CSV 是否存在。
- binary CSV 必要字段是否齐全。
- 当前有行的 binary universe 是否没有空 BBO / net edge / best leg。
- `net_edge > 30%` 是否为 0。
- LoL / Valorant 是否带 Riot official schedule source。
- CS2 是否保持 header-only。
- soccer compatibility CSV 是否不包含 `net_edge`，且全部 `skip_binary_arb=True`。

## 输出文件

### Latest snapshots

Latest 文件每轮覆盖，只代表最新一轮状态。

```text
data/mlb_arb_snapshot_latest.csv
data/nba_arb_snapshot_latest.csv
data/cs2_arb_snapshot_latest.csv
data/lol_arb_snapshot_latest.csv
data/valorant_arb_snapshot_latest.csv
data/worldcup_soccer_snapshot_latest.csv
```

注意：latest CSV 不会变得很长。例如 MLB 当前是一场比赛两个方向，所以大概是：

```text
39 games * 2 Yes contracts = 78 rows
```

### History snapshots

History 文件每轮追加，才是长数据文件。

```text
data/history/mlb_arb_snapshot_YYYY-MM-DD.csv
data/history/nba_arb_snapshot_YYYY-MM-DD.csv
data/history/cs2_arb_snapshot_YYYY-MM-DD.csv
data/history/lol_arb_snapshot_YYYY-MM-DD.csv
data/history/valorant_arb_snapshot_YYYY-MM-DD.csv
data/history/worldcup_soccer_snapshot_YYYY-MM-DD.csv
```

`data/history/` 默认不提交 GitHub，因为它会持续增长，适合保存在本地采集机器或后续迁移到对象存储。

## Binary Arb CSV 格式

用于 MLB / NBA / LoL / Valorant 等二元套利 universe。

字段：

```text
ts_utc
universe
category
match_name
event_date
canonical_event_id
market_type
pm_yes_outcome
ks_yes_outcome
pm_bid
pm_ask
pm_bid_sz
pm_ask_sz
ks_bid
ks_ask
ks_bid_sz
ks_ask_sz
net_edge
best_leg
gross_cost
best_leg_bbo_size
net_profit_at_bbo
pm_event_slug
pm_market_id
pm_token_id
ks_event_ticker
ks_market_ticker
match_format
schedule_source
```

重要字段解释：

- `pm_bid` / `ks_bid`：Yes contract 当前最高买价。
- `pm_ask` / `ks_ask`：Yes contract 当前最低卖价；我们作为 taker 买 Yes 时使用 ask。
- `*_sz`：对应 BBO 档位上的 size。
- `gross_cost`：选中 best leg 后，买两边合约的总成本，不含 fee。
- `net_edge`：`1 - gross_cost - fees`，大于 0 才是理论套利。
- `best_leg`：
  - `PM_YES_KS_NO`：买 PM Yes + 买 KS No。
  - `PM_NO_KS_YES`：买 PM No + 买 KS Yes。
- `best_leg_bbo_size`：best leg 两边 BBO 可成交 size 的较小值。例如 `PM_YES_KS_NO` 使用 `min(pm_ask_sz, ks_bid_sz)`。
- `net_profit_at_bbo`：`net_edge * best_leg_bbo_size`，只是 top-of-book 理论值，不代表真实成交一定可得。
- `schedule_source`：用于说明配对依据，例如 `mlb_stats_api` 或 Riot 官方 schedule。

## Soccer Compatibility CSV 格式

World Cup / soccer 当前不直接计算套利，因为很多市场是三结果：

```text
Team A / Tie / Team B
```

三结果市场不能套用二元 Yes/No 套利公式。

字段：

```text
ts_utc
source
universe
event_id
title
event_date
market_count
outcomes
is_binary_candidate
skip_binary_arb
reason
```

如果 `skip_binary_arb=True`，说明该行只用于兼容性观察，不参与 `net_edge` 计算。

## 当前运行建议

在稳定设备上长期跑 MLB/NBA：

```bash
cd /path/to/Arbitrage
python3 -u scripts/run_mlb_nba_loop.py --interval 5 --sports mlb,nba --show-warnings --bbo-workers 20 2>&1 | tee -a data/mlb_nba_loop.log
```

查看实时日志：

```bash
tail -f data/mlb_nba_loop.log
```

查看最新 snapshot：

```bash
head -5 data/mlb_arb_snapshot_latest.csv
```

查看累计数据行数：

```bash
wc -l data/history/mlb_arb_snapshot_YYYY-MM-DD.csv
```

停止前台 collector：

```text
Ctrl-C
```

## GitHub 注意事项

当前 `.gitignore` 会排除：

```text
data/history/
data/*.log
data/*.pid
```

原因是这些都是运行时数据，会持续变大。GitHub 主要保存代码、文档和小的 latest sample CSV。
