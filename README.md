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

跑 Polymarket Sports 全分类 inventory，并只对安全启用的 universe 计算 PM/Kalshi arb：

```bash
python3 scripts/build_all_snapshots.py --sports all --show-warnings
```

只跑指定 universe：

```bash
python3 scripts/build_all_snapshots.py --sports mlb,nba,soccer --show-warnings
```

测试时不写 history：

```bash
python3 scripts/build_all_snapshots.py --show-warnings --no-history
```

它会写 `data/*_latest.csv`，并在非 `--no-history` 模式下追加 `data/history/*_YYYY-MM-DD.csv`。

### `scripts/run_snapshot_loop.py`

主 orchestrator。它加载每个 sport adapter，统一写 per-sport inventory、safe arb snapshots、history 和 alert。

当前推荐命令：

```bash
python3 -u scripts/run_snapshot_loop.py --interval 5 --sports mlb,nba,soccer --show-warnings --bbo-workers 20 2>&1 | tee -a data/snapshot_loop.log
```

全分类 inventory + 安全 arb universe 的命令：

```bash
python3 -u scripts/run_snapshot_loop.py --sports all --interval 5 --bbo-workers 20 --show-warnings
```

特点：

- 支持 `--sports all`，也支持 `--sports tennis`、`--sports formula_1`、`--sports mlb,nba,world_cup`。
- `--sports all` 会调用全部 sport adapters；只有 `arb_status=paired` 的 adapter 会计算 PM/Kalshi `net_edge`。
- 每轮刷新 BBO / net edge。
- 默认每 5 分钟刷新一次比赛配对缓存，避免每 5 秒重复做 discovery / official schedule pairing。
- `--sports all` 或 `--inventory` 会每 5 分钟刷新一次 `data/polymarket_sports_inventory_latest.csv` 和 `data/sports/*_latest.csv`。
- 每轮追加 history。
- 每轮打印 `positive_edges` 和 `max_edge`，方便快速看到有没有正 net edge。
- 每轮更新 `data/arb_alerts_latest.csv`；如果有正 edge，会追加 `data/history/arb_alerts_YYYY-MM-DD.csv`。
- 每轮更新 `data/alerts/`，里面有当前机会的 CSV 和文字摘要。
- 单轮如果超过 5 秒，会在 log 中输出 warning。

`scripts/run_mlb_nba_loop.py` 仍保留为兼容入口，实际委托给 `run_snapshot_loop.py`。

### `scripts/run_sport_snapshot.py`

单 sport adapter 调试入口。

```bash
python3 scripts/run_sport_snapshot.py --sport tennis
python3 scripts/run_sport_snapshot.py --sport formula_1
python3 scripts/run_sport_snapshot.py --sport world_cup --show-warnings
```

inventory-only sports 只写 `data/sports/<sport>_latest.csv`。已启用严格配对的 sports 还会写对应 arb CSV。

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
- World Cup soccer：使用本地 FIFA World Cup 2026 group-stage schedule 做日期 + 国家配对；逐 outcome 配对 Team A win / Draw / Team B win。

### `scripts/sports_registry.py`

Polymarket Sports 全分类 registry。

当前 registry 覆盖截图中的 sports，再加 onboarding 明确要求的 NBA：

```text
World Cup, MLB, NBA, UFC, Football, Soccer, Tennis, Cricket, Basketball,
Baseball, Rugby, Table Tennis, Golf, Formula 1, Boxing, Pickleball,
Lacrosse, Hockey, Esports
```

每个 category 记录 PM sport code、tag id、tag slug、series、resolution source、adapter name、market focus、是否启用 arb、Kalshi series、schedule source 和当前风险状态。

Formula 1 使用 Polymarket `/sports` 里的真实 metadata：`sport=f1`、`tag_id=435`、`series=11635`。不要用猜测的 `formula-1` slug。

### `scripts/sports_adapters/`

每个截图 sport 一个 adapter module：

```text
world_cup, mlb, nba, ufc, football, soccer, tennis, cricket, basketball,
baseball, rugby, table_tennis, golf, formula_1, boxing, pickleball,
lacrosse, hockey, esports
```

MLB / NBA / World Cup 当前是 `paired` adapter，会输出 arb rows。其他 adapter 目前是 `inventory_only`，不会写 `net_edge`，避免误报。

### `scripts/sports_inventory.py`

全分类 Polymarket discovery 层。

负责：

- 按 registry 抓取所有 Polymarket Sports category 的 open events / markets。
- 写出 `data/polymarket_sports_inventory_latest.csv`。
- 标注哪些 category 只是 inventory，哪些可以进入 PM/Kalshi arb。
- 不拉 BBO，不计算 edge；inventory 是市场覆盖和 debug 索引，不是套利信号。

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
- soccer binary rows 是否都映射到本地 World Cup schedule，且 PM/KS outcome 一致，包括 draw/tie 对 draw/tie。
- 如果全分类 inventory CSV 存在，则逐行检查必要字段和 category/market 类型。

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
data/polymarket_sports_inventory_latest.csv
data/sports/<sport>_latest.csv
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
data/history/polymarket_sports_inventory_YYYY-MM-DD.csv
```

`data/history/` 默认不提交 GitHub，因为它会持续增长，适合保存在本地采集机器或后续迁移到对象存储。

## Binary Arb CSV 格式

用于 MLB / NBA / World Cup soccer / LoL / Valorant 等二元套利 universe。

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
alert
alert_threshold
alert_reason
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
- `alert`：`net_edge > 0` 时为 `ALERT`，否则为空。
- `alert_reason`：当前为 `net_edge_positive`。
- `best_leg`：
  - `PM_YES_KS_NO`：买 PM Yes + 买 KS No。
  - `PM_NO_KS_YES`：买 PM No + 买 KS Yes。
- `best_leg_bbo_size`：best leg 两边 BBO 可成交 size 的较小值。例如 `PM_YES_KS_NO` 使用 `min(pm_ask_sz, ks_bid_sz)`。
- `net_profit_at_bbo`：`net_edge * best_leg_bbo_size`，只是 top-of-book 理论值，不代表真实成交一定可得。
- `schedule_source`：用于说明配对依据，例如 `mlb_stats_api`、`fifa_world_cup_2026_local_schedule` 或 Riot 官方 schedule。

## World Cup Soccer 配对

World Cup soccer 当前按三结果逐项二元合约计算套利：

```text
Team A wins / Team A does not win
Draw / Not draw
Team B wins / Team B does not win
```

也就是说 Kalshi 的 `Germany` 对 PM 的 `Germany win`，Kalshi 的 `Tie` 对 PM 的 `draw`，Kalshi 的 `Argentina` 对 PM 的 `Argentina win`。配对依赖本地 FIFA World Cup 2026 group-stage schedule，canonical id 形如 `worldcup_soccer:2026-06-15:belgium:egypt`。

## Alert CSV

每轮 collector 会从所有 snapshot rows 中筛出 `net_edge > 0` 的行：

```text
data/arb_alerts_latest.csv
data/history/arb_alerts_YYYY-MM-DD.csv
```

`arb_alerts_latest.csv` 每轮覆盖，只保留当前这一轮的正 edge；history alert 文件只在出现正 edge 时追加。

同时会写一个更适合人工查看的文件夹：

```text
data/alerts/latest_opportunities.csv
data/alerts/latest_opportunities.txt
data/alerts/ALERT_ACTIVE.txt
data/alerts/NO_CURRENT_ALERTS.txt
```

当前没有机会时，会存在 `NO_CURRENT_ALERTS.txt`。只要出现机会，`ALERT_ACTIVE.txt` 会出现，并列出 sport、比赛、outcome、`net_edge`、`best_leg`、PM event/token 和 Kalshi market ticker。

## 当前运行建议

在稳定设备上长期跑 MLB/NBA/World Cup soccer：

```bash
cd /path/to/Arbitrage
python3 -u scripts/run_snapshot_loop.py --interval 5 --sports mlb,nba,soccer --show-warnings --bbo-workers 20 2>&1 | tee -a data/snapshot_loop.log
```

查看实时日志：

```bash
tail -f data/snapshot_loop.log
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
