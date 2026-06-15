# Sports Cross-Venue Arb Onboarding

## 1. 项目目标

在 **Polymarket (PM)** 和 **Kalshi (KS)** 两个预测市场，监控同一场体育赛事二元合约（Yes/No payout $1）的价格差，做跨平台套利。这是 R&D 项目，6 周内回答一个问题：**这条线值不值得部署 capital？**

- 决策门槛：实战月化 > **$20k** 才有意义
- 你的核心交付物：用真实数据 calibrate 出可信的月化估计 → deploy / kill 决策

### 1.1 两个平台

**Polymarket (PM)** — crypto-native，部署在 Polygon L2，USDC 结算
- 每个 outcome 是 ERC1155 token（Yes 赢 → $1，输 → $0），CLOB 撮合
- 全球开放（部分国家受限），用户：crypto retail + sharp bettors + MM bot
- 流动性深 (top event $M+/day)，long-tail event 偏 thin
- Settle 即时（合约自动 redeem）

**Kalshi (KS)** — US CFTC-regulated DCM（Designated Contract Market），2020 批准
- 二元事件合约，1¢-99¢，到期 $0 或 $1
- **仅限 US 用户**（SSN + US 地址 + US 银行账户）
- 用户：US retail + 少量 institutional MM
- 流动性整体浅于 PM，KS 自营 MM 给冷门 event 报价
- Settle T+1~14 days（按 official 数据源 finalize 后才放钱）

**arb 来源**：两边用户群、定价信号、监管成本、资金成本都不同 → 同一比赛的隐含概率会短暂背离。捕捉背离 = 你的工作。

---

## 2. 套利数学

```
Leg A:  Buy PM Yes  + Buy KS No   →  cost = pm_ask + (1 - ks_bid)
Leg B:  Buy PM No   + Buy KS Yes  →  cost = (1 - pm_bid) + ks_ask

Net edge per share = 1 - cost - PM_fee - KS_fee

# Takers only on both venues; makers free
PM_fee = 0.03 × p × (1-p)   # source: docs.polymarket.com/trading/fees
KS_fee = 0.07 × p × (1-p)   # source: kalshi.com/docs/kalshi-fee-schedule.pdf
```

两边 fee 都对称（p=0.5 取最大），仅 rate 不同。Break-even gross gap：

| 价位 (p) | 双边 fee | Break-even gap |
|---|---|---|
| 0.10 / 0.90 | 0.0090 | **0.90%** |
| 0.30 / 0.70 | 0.0210 | 2.10% |
| 0.50 / 0.50 | 0.0250 | **2.50%** |

**经济常识硬约束**：跨平台 sustained net 不会 > 5%（套利者会瞬间收割）。如果你算出 net > 30% 持续 > 30 min → 100% 是数据问题（mis-pair / settle 不一致 / 一边 stale）。这条比任何统计都重要。

---

## 3. 你要做的事（6 周）

### 任务 1：Data pipeline（Week 1-2）

写 PM + KS data collector，5 秒 polling，捕捉 arb 候选。输出 CSV，建议字段：

| 字段 | 说明 |
|---|---|
| ts_utc | 时间戳 |
| match_name | 比赛标识 |
| pm_yes_team / ks_yes_team | 配对方向（必须验证一致，见 §4.1） |
| pm_bid / pm_ask / pm_*_sz | PM BBO + size |
| ks_bid / ks_ask / ks_*_sz | KS BBO + size |
| net_edge | 计算结果 |

**Sample code — PM Gamma + CLOB**：

```python
import requests
PM_GAMMA = "https://gamma-api.polymarket.com"
PM_CLOB  = "https://clob.polymarket.com"

# Discover events by tag (mlb / nba / soccer / esports 等)
events = requests.get(f"{PM_GAMMA}/events", params={
    "tag_slug": "mlb",
    "closed":   "false",
    "limit":    100,
    "order":    "startDate",
    "ascending":"true",   # 重要：default order=volume 会拉到 season-long market 而不是 per-game
}).json()

# 拉某 event 详情（含 outcomes + clobTokenIds）
event = requests.get(f"{PM_GAMMA}/events",
                     params={"slug": "mlb-bos-nyy-2026-06-08"}).json()[0]
# event['markets'][i]['outcomes']      = ['Boston Red Sox', 'New York Yankees']
# event['markets'][i]['clobTokenIds']  = ['<yes_token>', '<no_token>']

# 拉 orderbook
book = requests.get(f"{PM_CLOB}/book",
                    params={"token_id": yes_token_id}).json()
# {'bids': [{'price': '0.55', 'size': '300'}, ...], 'asks': [...]}
```

**Sample code — KS Trade API v2**（public endpoints 无需 auth）：

```python
import requests
KS = "https://api.elections.kalshi.com/trade-api/v2"

# Discover events
# series: KXMLBGAME / KXNBAGAME / KXLOLGAME / KXCS2GAME / KXVALORANTGAME / KXWCGAME 等
events = requests.get(f"{KS}/events", params={
    "series_ticker": "KXMLBGAME", "status": "open", "limit": 200
}).json()["events"]

# 每个 event 通常含 2 个 market（yes for team A, yes for team B）
markets = requests.get(f"{KS}/markets", params={
    "event_ticker": "KXMLBGAME-26JUN081335BOSNYY", "status": "open", "limit": 10
}).json()["markets"]
# markets[0]['yes_sub_title']      = 'Boston Red Sox'   # 用来匹配 PM outcomes[0]
# markets[0]['yes_bid_dollars']    # BBO，注意字段返回 str
# markets[0]['yes_ask_dollars']

# Orderbook depth
book = requests.get(
    f"{KS}/markets/KXMLBGAME-26JUN081335BOSNYY-BOS/orderbook",
    params={"depth": 10}
).json()["orderbook_fp"]
# {'yes_dollars': [['0.55', '200'], ...], 'no_dollars': [...]}
```

**Fee + net 计算**：

```python
def pm_fee(p): return 0.03 * p * (1 - p) if p else 0   # PM sports, takers only
def ks_fee(p): return 0.07 * p * (1 - p) if p else 0   # KS, takers only

def best_net(pm_bid, pm_ask, ks_bid, ks_ask):
    # Leg A: pm_yes + ks_no
    cost_a = pm_ask + (1 - ks_bid)
    net_a  = 1 - cost_a - pm_fee(pm_ask) - ks_fee(1 - ks_bid)
    # Leg B: pm_no + ks_yes
    cost_b = (1 - pm_bid) + ks_ask
    net_b  = 1 - cost_b - pm_fee(1 - pm_bid) - ks_fee(ks_ask)
    return max(net_a, net_b)
```

### 任务 2：Paper-trade simulator（Week 3-4）

用 Task 1 数据离线 backtest。**这是 R&D 项目的核心交付物**。

- **输入**：每个 net > 2% alert 时刻的 PM + KS book snapshot
- **下单模拟**：选 leg、walking-the-book 算 average fill price、决定 size（建议 $100-1000/event 上限）
- **延迟模拟**：用 T+5s / T+15s / T+30s 的真实历史 book 验证你订单还能 fill 多少
- **输出指标**：`actual fill $ / theoretical net $ = capture ratio`
- 分维度统计：sport / net 区间 / book 厚度 / 时段 / 比赛阶段（赛前 / live / 即将结算）

**核心交付物**：真实 capture rate 区间（例 8-12%）+ 对应月化 $ 估计 + 哪些 sport / 时段贡献最大。

### 任务 3：决策 + 部署（Week 5-6）

基于 Task 2 真实月化估计：

- **月化 > $20k** → 开 PM + KS 账户，写 execution bot，$100-500/event 小额验证
- **月化 < $20k** → 写 kill report，建议 pivot（7 月 NHL playoffs / 8 月 NFL preseason / 7 月 Wimbledon 等新 universe）
- **KS 开户需 US identity**。非美国公民 → Curtis 协调

---

## 4. 必读陷阱（你一定会遇到的概念性坑）

### 4.1 问题对齐：真的是同一场比赛吗？

- **MLB 规则差异**：KS "official game" 5 局即结算，PM 可能要 9 局 → 雨天比赛两边结算不同
- **Esports BO 制不一致**：PM 可能挂 BO5 series winner，KS 同名 event 实为 BO3 单场
- **NBA series vs game**：PM 同一 slug 可能挂多个 market，确认你选的是 game winner 不是 series winner
- **3-way 比赛**：WC group stage 含 Tie outcome → binary 套利公式失效，必须 skip
- **Cancellation 不对称**：PM 50/50 refund，KS 按 last-trade 结算
- **Timezone 错位**：KS 用 EDT，PM 用 UTC，slug 日期可能差一天
- **多日 series 同两队**：NBA Game 2/3/4 都是 Knicks vs Spurs → 必须按比赛日期匹配 PM slug，不然 PM 配错 game
- **验证方法**：随机选 5 个 high-net pair，逐字对比两边 `rules_primary` / market description

### 4.2 套利窗口判定

- **net > 0 ≠ 可吃**：可能只 5 sec 就消失，5-sec polling 会错过
- **BBO depth ≠ fill size**：API 显示 1000 shares 你可能只能吃 300（别人也在吃）
- **持续 window ≠ 多次机会**：sustained 60s arb 通常只能 fill 1 次，MM 立即调整
- **Stale book 假象**：一边几个 tick BBO 不变 → 可能 MM 离开了，不是真挂单
- **In-game spike**：球员受伤 / 红牌瞬间跳价 → 5-sec polling 看到的可能是 spike 后的回归价
- **Settle 边界**：比赛已完但 KS 未 finalize → 99c 卡 30 分钟看似 arb，实为 reconciliation 延迟

### 4.3 执行延迟（最致命）

- **Polling 5 sec**：detect alert 时已是 T+0~5s 历史
- **API latency**：PM Gamma ~200ms，PM CLOB ~300-500ms（Polygon 链上 confirm），KS ~100-300ms
- **双边非原子**：必须先后下两单。先成交一边后另一边价格已变 → 单腿暴露
  - 缓解：先下流动性差的一边（通常 KS），成交后立即下 PM，失败要有 immediate exit
- **Polygon 拥堵**：高峰 confirm 5-10s = 别人的收割窗口
- **KS rate limit**：约 5 req/sec 软限制，trading 调用容易触发 429
- **Settle 时间不对称**：PM 立即（USDC），KS T+1~14 days（USD 银行）→ 两边各预留 100% 资金
- **Simulator 必须模拟延迟**：不模拟 = 高估 capture 2-3x

### 4.4 账户 / 资金 / 监管

- **KS US-only 开户**：SSN + US 地址 + US 银行账户。非 US 公民 → Curtis 协调
- **PM 全球可开**：Polygon wallet + USDC + KYC
- **双边 100% margin**：$1000 arb event 锁 PM $500 + KS $500，资金效率低
- **KS account freeze 风险**：历史上 freeze 过 high-frequency 账户，trading polling 要克制
- **PM CLOB API key 安全**：每个订单 sign with Polygon wallet 私钥，泄漏 = 钱没了

---

## 5. 参考链接

| 用途 | 链接 |
|---|---|
| PM API 全文档 | https://docs.polymarket.com/ |
| PM Gamma (event / market 发现) | https://docs.polymarket.com/developers/gamma-markets-api/overview |
| PM CLOB (orderbook + trading) | https://docs.polymarket.com/developers/CLOB/introduction |
| PM Sports fee | https://docs.polymarket.com/trading/fees |
| KS API 全文档 | https://trading-api.readme.io/ |
| KS Events / Markets endpoint | https://trading-api.readme.io/reference/getevents |
| KS Orderbook endpoint | https://trading-api.readme.io/reference/getmarketorderbook |
| KS Fee schedule PDF | https://kalshi.com/docs/kalshi-fee-schedule.pdf |
| KS sports series tickers | https://kalshi.com/markets (sports tab) |
