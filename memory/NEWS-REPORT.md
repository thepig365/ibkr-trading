# News report

Rolling append-only record of pre-open major-news briefings. Each
entry is written by ``python -m bot.cli pre-open-news`` (or the
scheduled ``pre_open_news`` job at 08:30 America/New_York on US
trading weekdays).

Structured copies of each report are saved under
`data/pre_open_news/YYYY-MM-DD.json`. See
[`docs/pre-open-news-report.md`](../docs/pre-open-news-report.md) for
the schema and risk rules.

---

## Pre-Open Major News Report - 2026-04-24
_run_time_new_york_: 08:30

### Market regime
- regime: **neutral**
- regime_confidence: **medium**
- research_scans_allowed: **yes**
- new_positions_allowed: **no**
- regime_reason: VIX missing; execution requires VIX; regime confidence=medium; config forbids medium-confidence entries; missing market data: VIX, VIX3M; VIX/VIX3M unavailable; using SPY/QQQ trend fallback
- research_available: True (ibkr_news=True, external=False)
- SPY above 200MA: True  QQQ above 200MA: True

### Missing market data
- VIX
- VIX3M

### Major news
- (medium) Advanced Micro Devices soars on massive multi-year AI infrastructure deal with Meta (source: BRFG; symbols: AMD)
- (low) Amazon-Anthropic alliance deepens, strengthening competitive stance vs. Microsoft and Google (source: BRFG; symbols: AMZN)
- (low) Apple Turns Over a New Leaf as Tim Cook Hands CEO Role to John Ternus (source: BRFG; symbols: AAPL)
- (low) Arm Holdings strategic pivot to in-house silicon triggers massive re-rating (source: BRFG; symbols: ARM)
- (low) Broadcom Powers Up: AI Chip Boom and Strong Q2 Outlook Drive Shares Higher (source: BRFG; symbols: AVGO)
- (low) Broadcom-Google partnership signals stronger push into custom AI silicon (source: BRFG; symbols: AVGO)
- (low) Kick off your portfolio as the FIFA Club World Cup heads into its final week (source: BRFG; symbols: SPY)
- (low) Micron falls despite blockbuster Q2 report as focus turns to rising CapEx, cycle risks (source: BRFG; symbols: MU)
- (low) Micron slides despite AI demand surge as massive $200 bln expansion plan raises capex concerns (source: BRFG; symbols: MU)
- (low) NVIDIA Powers Up: Blowout Q4, Massive Q1 Guide, and AI Demand Still Hitting Full Throttle (source: BRFG; symbols: NVDA)

### Earnings news
- (low) NVIDIA Shocks the System: Q3 Results Prove AI Demand Is Still Compute-ing Higher (source: BRFG; symbols: NVDA)
- (low) Taiwan Semiconductor Manufacturing rallies on strong Q1 revenue driven by AI and HPC demand (source: BRFG; symbols: TSM)

### Analyst rating updates (68 total)
- (low) Arete downgraded Meta Platforms (META) to Neutral (source: BRFUPDN; symbols: META)
- (low) Argus downgraded Super Micro Computer (SMCI) to Hold (source: BRFUPDN; symbols: SMCI)
- (low) Argus upgraded Super Micro Computer (SMCI) to Buy (source: BRFUPDN; symbols: SMCI)
- (low) BNP Paribas Exane upgraded Apple (AAPL) to Outperform (source: BRFUPDN; symbols: AAPL)
- (low) Barclays reiterated Taiwan Semiconductor Manufacturing (TSM) coverage with Overweight and target $325 (source: BRFUPDN; symbols: TSM)
- ... (63 more, see JSON)

### Bot instruction
Research incomplete; block new entries unless manually reviewed. Reasons: external research unavailable.


### 中文完整报告

【盘前重大市场新闻报告】2026-04-24

一、市场机制判断
- 市场状态：neutral（中等置信度）
- 是否允许新开仓：否
- 是否允许研究扫描：是
- 缺失数据：VIX、VIX3M
- 说明：VIX/VIX3M 当前不可用，系统使用 SPY/QQQ 200MA 趋势作为 fallback。研究扫描允许，但交易执行仍然关闭。
- 机制原因：VIX missing; execution requires VIX; regime confidence=medium; config forbids medium-confidence entries; missing market data: VIX, VIX3M; VIX/VIX3M unavailable; using SPY/QQQ trend fallback
- IBKR 新闻数据：可用
- 外部研究数据：未启用 / 不可用
- 当前报告基于 IBKR headlines，不代表完整外部新闻覆盖。

二、今日重点新闻摘要
1. AMD — medium
  英文标题：Advanced Micro Devices soars on massive multi-year AI infrastructure deal with Meta
  中文摘要：AI / 基础设施相关消息，涉及 AMD。
  潜在影响：可能影响 AI 芯片、数据中心基础设施及相关半导体板块情绪；需人工复核发布时间和盘前价格反应。
  处理：加入人工观察，不自动交易。
2. NVDA — low
  英文标题：NVIDIA Shocks the System: Q3 Results Prove AI Demand Is Still Compute-ing Higher
  中文摘要：基于标题的初步摘要，需人工复核。
  潜在影响：潜在影响未知；建议人工复核新闻真实性、发布时间和盘前价格反应。
  处理：加入人工观察，不自动交易。
3. TSM — low
  英文标题：Taiwan Semiconductor Manufacturing rallies on strong Q1 revenue driven by AI and HPC demand
  中文摘要：股价大幅上涨相关消息，涉及 TSM。
  潜在影响：偏利多；需复核触发原因（基本面 vs 技术面）。
  处理：加入人工观察，不自动交易。
4. AMZN — low
  英文标题：Amazon-Anthropic alliance deepens, strengthening competitive stance vs. Microsoft and Google
  中文摘要：基于标题的初步摘要，需人工复核。
  潜在影响：潜在影响未知；建议人工复核新闻真实性、发布时间和盘前价格反应。
  处理：仅记录，不自动交易。
5. AAPL — low
  英文标题：Apple Turns Over a New Leaf as Tim Cook Hands CEO Role to John Ternus
  中文摘要：基于标题的初步摘要，需人工复核。
  潜在影响：潜在影响未知；建议人工复核新闻真实性、发布时间和盘前价格反应。
  处理：仅记录，不自动交易。
6. ARM — low
  英文标题：Arm Holdings strategic pivot to in-house silicon triggers massive re-rating
  中文摘要：基于标题的初步摘要，需人工复核。
  潜在影响：潜在影响未知；建议人工复核新闻真实性、发布时间和盘前价格反应。
  处理：仅记录，不自动交易。
7. AVGO — low
  英文标题：Broadcom Powers Up: AI Chip Boom and Strong Q2 Outlook Drive Shares Higher
  中文摘要：AI / 基础设施相关消息，涉及 AVGO。
  潜在影响：可能影响 AI 芯片、数据中心基础设施及相关半导体板块情绪；需人工复核发布时间和盘前价格反应。
  处理：仅记录，不自动交易。
8. AVGO — low
  英文标题：Broadcom-Google partnership signals stronger push into custom AI silicon
  中文摘要：与 AI / 基础设施相关的合作或协议，涉及 AVGO。
  潜在影响：可能影响 AI 芯片、数据中心基础设施及相关半导体板块情绪；需人工复核发布时间和盘前价格反应。
  处理：仅记录，不自动交易。
9. SPY — low
  英文标题：Kick off your portfolio as the FIFA Club World Cup heads into its final week
  中文摘要：基于标题的初步摘要，需人工复核。
  潜在影响：潜在影响未知；建议人工复核新闻真实性、发布时间和盘前价格反应。
  处理：仅记录，不自动交易。
10. MU — low
  英文标题：Micron falls despite blockbuster Q2 report as focus turns to rising CapEx, cycle risks
  中文摘要：基于标题的初步摘要，需人工复核。
  潜在影响：潜在影响未知；建议人工复核新闻真实性、发布时间和盘前价格反应。
  处理：仅记录，不自动交易。
11. MU — low
  英文标题：Micron slides despite AI demand surge as massive $200 bln expansion plan raises capex concerns
  中文摘要：股价大幅上涨相关消息，涉及 MU。
  潜在影响：偏利多；需复核触发原因（基本面 vs 技术面）。
  处理：仅记录，不自动交易。
12. NVDA — low
  英文标题：NVIDIA Powers Up: Blowout Q4, Massive Q1 Guide, and AI Demand Still Hitting Full Throttle
  中文摘要：基于标题的初步摘要，需人工复核。
  潜在影响：潜在影响未知；建议人工复核新闻真实性、发布时间和盘前价格反应。
  处理：仅记录，不自动交易。
13. ORCL — low
  英文标题：Oracle's AI Crystal Ball Looks Bright: $90 bln FY27 Outlook Sparks Rally
  中文摘要：基于标题的初步摘要，需人工复核。
  潜在影响：潜在影响未知；建议人工复核新闻真实性、发布时间和盘前价格反应。
  处理：仅记录，不自动交易。
14. SPY — low
  英文标题：Trump Trade: We take a look at which areas should benefit and which areas should get hurt
  中文摘要：基于标题的初步摘要，需人工复核。
  潜在影响：潜在影响未知；建议人工复核新闻真实性、发布时间和盘前价格反应。
  处理：仅记录，不自动交易。

三、财报 / 业绩相关新闻
- NVDA (low): NVIDIA Shocks the System: Q3 Results Prove AI Demand Is Still Compute-ing Higher
  中文摘要：基于标题的初步摘要，需人工复核。
- TSM (low): Taiwan Semiconductor Manufacturing rallies on strong Q1 revenue driven by AI and HPC demand
  中文摘要：股价大幅上涨相关消息，涉及 TSM。

四、分析师评级 / 目标价更新
- META:
    · Arete downgraded Meta Platforms (META) to Neutral
    · Erste Group downgraded Meta Platforms (META) to Hold
    · Morgan Stanley reiterated Meta Platforms (META) coverage with Overweight and target $775
    · Rosenblatt reiterated Meta Platforms (META) coverage with Buy and target $1015
    · Wolfe Research reiterated Meta Platforms (META) coverage with Outperform and target $800
- SMCI:
    · Argus downgraded Super Micro Computer (SMCI) to Hold
    · Argus upgraded Super Micro Computer (SMCI) to Buy
    · CJS Securities downgraded Super Micro Computer (SMCI) to Market Underperform
    · Goldman resumed Super Micro Computer (SMCI) coverage with Sell and target $26
    · Northland Capital downgraded Super Micro Computer (SMCI) to Market Perform with target $22
- AAPL:
    · BNP Paribas Exane upgraded Apple (AAPL) to Outperform
    · BofA Securities reiterated Apple (AAPL) coverage with Buy and target $320
    · BofA Securities reiterated Apple (AAPL) coverage with Buy and target $325
    · Rosenblatt reiterated Apple (AAPL) coverage with Neutral and target $268
- TSM:
    · Barclays reiterated Taiwan Semiconductor Manufacturing (TSM) coverage with Overweight and target $325
    · Bernstein reiterated Taiwan Semiconductor Manufacturing (TSM) coverage with Outperform and target $330
    · DA Davidson initiated Taiwan Semiconductor Manufacturing (TSM) coverage with Buy and target $450
    · Needham reiterated Taiwan Semiconductor Manufacturing (TSM) coverage with Buy and target $410
- AMD:
    · Bernstein reiterated Advanced Micro Devices (AMD) coverage with Mkt Perform and target $265
    · DA Davidson initiated Advanced Micro Devices (AMD) coverage with Neutral and target $220
… 另有 48 条评级更新，请查看完整 JSON 报告。

五、需要人工复核的股票
- AAPL、AMD、AMZN、ARM、AVGO、CRWV、GOOGL、META、MSFT、MU、NVDA、ORCL、PLTR、SMCI、TSLA、TSM

六、被阻止 / 不应交易的股票
- 暂无强制阻止股票。

七、Bot 指令
- 研究数据不完整时，不允许新开仓。
- 当前仅允许研究扫描和人工复核。
- 不自动下单。
- execution_allowed=false；research_only=true。
- 系统说明：Research incomplete; block new entries unless manually reviewed. Reasons: external research unavailable.
