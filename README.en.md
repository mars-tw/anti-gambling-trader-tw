# Anti-Gambling Trader (反詐投資王)

[繁體中文](README.md)

## Beginner GUI first (about five minutes after installation)

Version 0.2.0 provides one local UI in two user-selected shells:

- **Local browser mode** uses only the Python standard library and opens in your default browser.
- **Desktop window mode** loads the exact same URL and service in a native WebView window.

Install and start browser mode from source:

```bash
pip install -e .
anti-gambling-trader ui                 # choose browser or desktop
anti-gambling-trader ui --mode browser  # open browser mode directly
```

On Windows, `start-ui.cmd` is also available. It prefers the repository's `.venv` and never
installs anything silently. Desktop mode and Excel import are optional; Windows desktop mode
also requires the Microsoft Edge WebView2 Runtime:

```bash
pip install -e ".[desktop,excel]"
anti-gambling-trader ui --mode desktop
```

If the desktop renderer is unavailable, use `--mode browser`. Both modes bind only to
`127.0.0.1` and follow the same beginner flow: try a labeled sample, import or manually record,
read integrity and the core stage, download a report, then optionally download a paper scaffold.
Bundled samples are demonstrations, never your results.

Manual rows and analyses may use local temporary files; source files are never modified. Normal
completion or exit clears these temporary files, but an unexpected failure or forced stop may
leave system temporary files behind. Downloads are explicit persistent copies—download anything
you want to keep. After a restart, load the downloaded current-record CSV from **Load previous
records** to continue. Closing a browser tab does not stop the local server—use **End this
session** in the UI or press `Ctrl+C` in the terminal.

Automation can use `--no-open --ready-file ui-ready.json`. The ready JSON contains only the
local URL, PID, mode, instance ID, and ready state—never the session nonce—and an existing file
is never overwritten. Maintainers can build a Windows onedir bundle and ZIP from an environment
with the desktop dependencies installed by running
`python scripts/build_windows_ui.py --output dist`. This is a build instruction, not a claim or
link to an executable release that has not yet passed portable validation.

> **Live trading is not provided.** The GUI accepts no broker keys, makes no broker connection,
> and submits no orders. Even if the analytical core reports `tiny_live_validation`, the GUI
> remains live-disabled. Every generated project is fixed to `PaperBroker`, `stage=None`, and
> false live flags; it is only a strategy scaffold to complete. The GitHub Pages site contains
> instructions only—it is not a hosted analysis application and does not accept trade records.

**Wondering whether that investment group is a scam, whether the "guru" calling trades can be trusted, or whether your trading profits come from skill or luck?**
This is a free, open-source toolkit for trading statistics, investment-fraud defense and
automated-trading development: feed it your trade history (Taiwan stocks / US stocks / crypto),
and it uses expectancy, significance testing and out-of-sample validation to tell you honestly — whether your profit is a **repeatable edge**
or **luck plus survivorship bias (gambling)**. It also has built-in scam-language scanning, fake-performance forensics and fake-guru claim checking.
It can also turn trading logic into backtestable strategy skeletons and generate automated-trading
projects that can be connected to broker APIs. When you run this project's CLI directly, it reads and
analyzes trade records locally and does not upload them. If you give files, screenshots, or tool output
to Claude Code or another cloud AI, the content may be transmitted and retained under that provider's
policy; mask names, account numbers, LINE IDs, and other identifiers first. See the
[official Claude Code data-usage documentation](https://code.claude.com/docs/en/data-usage).
Some generated charts load an external CDN, and broker/API paths explicitly connect to their external services.

> A tool that is **honest to the point of being unlikable**. It will not tell you "you will make money" —
> if your record is not suited to long-term investing, it will plainly talk you out of it.
> 👉 In a hurry for answers? Start with the **[FAQ](docs/faq.md)** (currently in Traditional Chinese):
> Is this investment group a scam? Is a 90% win rate believable? Is it normal to be asked to
> "pay tax before withdrawal"? What to do if you have been scammed?

> 🤖 **It can build automated-trading projects, not just analyze trades.** `scaffold` generates a
> self-contained strategy, risk controls, broker interface, data feed and charting project with
> `PaperBroker` plus 13 live-broker/exchange templates. Paper mode runs out of the box. If you build
> real automation, you must connect the broker API and live-data runner and preserve all four safety
> checks. The generated historical runner never submits live orders; a live adapter can submit only
> after you provide the keys, implement a live runner and explicitly unlock it.

**TL;DR** — An honest, open-source (MIT) trading-performance analyzer for
Taiwan securities and derivatives, US stocks, crypto and forex. It uses expectancy, significance tests (t-test +
centered bootstrap) and out-of-sample validation to tell whether your P&L is a
repeatable edge or survivorship-biased luck — and it will actively discourage you
if it's the latter. Includes scam-language scanning, fake-performance forensics
and a guru-claim probability checker. The statistical core uses the Python standard library,
and trade-record analysis stays local.
It also generates backtestable strategies and automated-trading projects, with paper trading by
default and explicit safety gates for any user-built live connection.

## 👶 Never used a command line? Start here

If you have never installed Python, opened a terminal, or used AI, read the
**[beginner quickstart guide docs/quickstart.md](docs/quickstart.md)** (in Traditional Chinese) —
it starts from "how to install Python and open a terminal", walks you through step by step in about 15 minutes,
and starts with one command: `anti-gambling-trader start`. It routes you to a complete trade log,
a five-prompt single-trade recorder, a trading screenshot, or a LINE chat export.

## 🛡 This tool's anti-fraud mission

Taiwan is full of **fake hot-stock groups, fake "second-stage" groups, fake VIP groups, fake gurus,
fake performance screenshots, "guaranteed profit" sales pitches, scam coins and fake investment platforms**.
Most of them rely on the same trick: using **survivorship bias** and **cherry-picked screenshots**
to make you believe there is a sure-win shortcut.

The name "Anti-Gambling Trader" (literally "Anti-Fraud Investment King" in Chinese) is not for show —
the real reason this tool exists is to **use statistics and mathematics to expose these schemes**.
What scams fear most is you calmly putting their promises to a mathematical test.

```bash
# Scan group messages for scam-language features (ordinal risk, never a fake percentage)
anti-gambling-trader scan-text "老師帶單保證獲利，快加VIP客服"

# Test how unusual a claimed 90% win rate and 20% monthly return are under the null model
anti-gambling-trader guru-check --win-rate 0.9 --trades 10 --monthly-return 0.2

# Quantify how easily a ten-win "guru" can appear by survivorship alone
anti-gambling-trader survivorship

# Flag suspicious properties in a claimed return series (over-smoothing, implausible Sharpe)
anti-gambling-trader forensics --file 老師的月報酬.txt

# Run the interactive self-check
anti-gambling-trader scam-check
```

See the **[complete behavior and interpretation guide](docs/user-guide.md)**,
the **[anti-scam guide](docs/anti-scam.md)** and the **[FAQ](docs/faq.md)**
(all in Traditional Chinese).

> 🆘 **Suspect you are being scammed right now?** Stop transferring money immediately, call the
> **165 anti-fraud hotline**, or visit the [165 anti-fraud portal](https://165.npa.gov.tw).
> To verify licensed operators: [金管會證期局 (FSC Securities and Futures Bureau)](https://www.sfb.gov.tw);
> for securities/futures dispute assistance: [投保中心 (SFIPC)](https://www.sfipc.org.tw).
> (Official links last verified: 2026-07)
> *Note for non-Taiwan readers: 165 is Taiwan's national anti-fraud police hotline, and the
> 金管會 (Financial Supervisory Commission, FSC) is Taiwan's financial regulator.*
> This tool is a statistical aid, **not forensic evidence for legal proceedings**; official channels always come first.

---

Supports **Taiwan stocks / US stocks / crypto**, imports trade records from **CSV / JSON / Excel**, and automatically:

- Computes **win rate, payoff ratio, expectancy, profit factor, max drawdown, Sharpe / Sortino**
- Uses **statistical significance testing (t-test + bootstrap)** to judge "edge or luck"
- Uses **out-of-sample validation** to expose overfitting and survivorship bias
- Scans for **gambling warning signs** (small wins / large losses, profits overly concentrated in a few trades, long losing streaks, pure day trading…)
- **Per-strategy checkup**: descriptive statistics for each of your approaches (breakout / dip buying / tips from others…), pinpointing **which one is giving money away**
  (deliberately does not certify individual strategies as "edges" — uncorrected multiple comparisons would mistake luck for edge; see the methodology)
- **Counterfactual analysis**: computes "what the overall result would look like if you dropped your worst approach"
- **Break-even numbers**: tells you "what win rate / payoff ratio you would need for expectancy to turn positive"
- **Anti-fraud detection**: isolates the "guru-following / copy-trade" trades and computes their expectancy separately — testing with your own numbers whether following the calls actually pays
- **Risk scenario simulation**: simulates future paths from your own P&L distribution to see what fraction of paths blow up the account
- **Time trends**: monthly/quarterly descriptive summaries plus one fixed early-vs-recent edge-decay test
- **LINE export scanning**: understands LINE headers and split messages while preserving speaker, time, evidence and reasons
- **Screenshot review**: extracts trade points and technical-analysis text clues, but never auto-fills low-confidence OCR
- **Beginner stage routing**: uses the full record to recommend stopping, paper trading, or at most tiny live validation
- **HTML report + shareable card**: black-and-white numbers you can save, screenshot and send to family
- Reverse-engineers your trading logic into a **backtestable strategy skeleton** (backtrader / vectorbt / generic)
- **Automated-trading project scaffold**: generates a self-contained strategy, risk controls, broker
  interface, data feed and charts; `PaperBroker` runs directly, with 13 live-broker/exchange templates
  for users to connect and validate themselves
- If the verdict is **not suited to long-term investing, it explicitly talks you out of it**

Supported markets: Taiwan stocks, Taiwan ETFs, **TAIEX futures / options (with contract multipliers)**, US stocks, crypto, **forex**.

## Why this exists

Most retail traders lose money because they mistake "good luck" for "skill" —
and fraud rings exploit exactly this cognitive weakness, harvesting you with fake groups, fake gurus and fake screenshots.
A stretch of paper profits is often statistically indistinguishable from "pure luck". The sole purpose of this tool
is to **let the math tell you the truth** before you lose more money or get harvested by a scam.

## Installation

The core engine is **pure Python standard library, zero external dependencies**.

```bash
git clone https://github.com/mars-tw/anti-gambling-trader-tw.git
cd anti-gambling-trader-tw
pip install -e .            # install the package and CLI

# Optional dependencies:
# pip install -e ".[excel]" # only for Excel (.xlsx), including safe XML parsing
# pip install -e ".[screenshot]"  # image OCR; Tesseract + language data also required
# pip install backtrader     # only to run a backtrader skeleton (or install vectorbt)
```

Requirements: Python 3.10+. Run all commands from the project root (the directory containing `core/`);
on macOS / Linux, if `python` points to Python 2, use `python3` instead.

## Quick start (30 seconds)

```bash
# 0. Show the shortest route for the data you already have
python -m core.cli start

# 1. No spreadsheet: record one closed trade in five short prompts
python -m core.cli record

# 2. Get a conservative stage recommendation from a complete record
python -m core.cli fit-check my_trades.csv

# 3. Scan a LINE export or review fields extracted from a screenshot
python -m core.cli scan-text --file LINE-chat.txt
python -m core.cli scan-screenshot broker.png

# 4. No data yet? Preview both outcomes with bundled examples
python -m core.cli demo               # gambling-like example
python -m core.cli demo --edge        # statistical-edge example

# 5. Prefer a spreadsheet? Create a blank template
python -m core.cli init-template      # creates trades_template.csv

# 6. Run the full analysis (market is inferred)
python -m core.cli analyze your-trades.csv

# 7. If column detection fails, map fields explicitly
python -m core.cli analyze your-trades.csv --field symbol=ticker --field entry_price=buy_price

# Advanced: export JSON and a strategy skeleton
python -m core.cli analyze --example us --json result.json --strategy my_strategy.py
```

After installing the package, every `python -m core.cli` above can be replaced with the shorter `anti-gambling-trader`.

> Screenshot parsing produces review candidates and text clues; it never saves them as trades automatically.
> OCR may misread a sign, decimal point, unit or direction, so compare every field with the original image.
> One screenshot cannot establish performance or trading skill.

For every workflow, decision threshold and review checklist, see the
[complete behavior and interpretation guide](docs/user-guide.md) (Traditional Chinese).

> If Chinese characters / emoji render incorrectly in a Windows terminal: in PowerShell run `$env:PYTHONUTF8=1` first, then run the command;
> on macOS / Linux, prefix the command with `PYTHONUTF8=1`.

## Input format

Column names are **auto-detected, in Chinese or English**. At minimum you need either
(symbol + side + entry price + exit price + quantity), or
(symbol + net-of-costs P&L + account settlement currency).

| Standard field | Accepted column names (partial examples) | Required? |
|----------|--------------------------|--------|
| symbol | 代號 / ticker / 股票代號 / pair | Required |
| side | 方向 / 買賣 / side / long_short | Required for price-derived P&L; may be absent for direct P&L, but no side profile is inferred |
| entry_time / exit_time | 進場時間 / 出場時間 / open_time | Recommended |
| entry_price / exit_price | 進場價 / 出場價 / 買價 / 賣價 | Either these or pnl |
| quantity | 數量 / 股數 / 張數 / qty | Either these or pnl |
| fees | 交易成本 / total_fee, or complete commission + tax fields | Estimated only for price-derived P&L; direct net P&L is not charged twice |
| pnl | pnl / 損益 / net_pnl / 淨損益 | Either this or prices; the standard-field contract is net of all costs |
| pnl_currency | 損益幣別 / 帳戶幣別 | Required for direct P&L; only price-derived P&L may use the instrument's native quote currency |
| tag | 策略 / strategy / 進場理由 | Optional (strongly recommended) |

### Fail-closed behavior for incomplete data

- Missing, invalid or identical exit times never fall back to file-row order as a fake timeline.
- Different or unknown P&L currencies are not added together; mixed timezone bases are not sorted.
- Unknown contract multipliers, notional bases or entry bases disable return and drawdown percentages.
- Ambiguous gross/net headers are not treated as net P&L without explicit confirmation.

The monetary P&L may still be retained when trustworthy, while only the dependent metrics are disabled.
See the [full fail-closed matrix](docs/user-guide.md#4-資料不完整時工具如何-fail-closed).

> Headers such as `盈虧`, `已實現損益`, and `realized_pnl` do not prove whether the
> amount is gross or net. Rename them to an explicitly net header, or confirm the
> mapping with `--field pnl=your_column`. Gross headers such as `profit` are accepted
> only when a trustworthy `total_fee` is present on the same row and can be deducted.

> **Strongly consider filling in `tag` (strategy label)**: the tool presents descriptive statistics for each approach,
> helping you find which one performed worse in this sample and deserves review. It does not certify any individual tag as an edge.

## The five verdict levels

| Level | Meaning | Discouragement |
|------|------|------|
| 🟥 `gambling` | Sample expectancy is negative — with the method unchanged, the long-run statistical expectation is a loss | ✅ Strongly discouraged |
| 🟧 `insufficient` | Sample too small to distinguish skill from luck | ✅ Discouraged from sizing up |
| 🟨 `luck_suspected` | Profitable on paper, but statistically looks like luck | ✅ Highly doubtful |
| 🟨 `fragile_edge` | A statistical signal exists but the structure is fragile and high-risk | ✅ Caution |
| 🟩 `statistical_edge` | Positive expectancy that passes significance testing (out-of-sample validation reported separately) | ❌ Not discouraged (still no guarantee) |

## How it separates "edge" from "gambling"

1. **Expectancy**: average win/loss per trade. **A negative *true* expectancy means a long-run loss in expectation (by mathematical definition);**
   **a negative *sample* expectancy is "treated as gambling until shown otherwise" (a conservative principle) — it is an estimate, so the verdict comes with uncertainty tests attached.**
2. **Significance testing**: t-test + centered bootstrap (shift method). Both p-values below 0.05
   are evidence against a non-positive mean under the stated null and model assumptions. A p-value is
   the tail probability of an equally or more extreme statistic under that model—not the probability
   that the null is true, that the result is “chance only,” or that an edge exists. It proves neither
   future edge nor causal skill, and two tests on the same records are not independent replications.
3. **Out-of-sample validation**: one real-time cut is used, with at least ten trades on each side and no same-timestamp group split.
   Both segments must have significant positive expectancy, later degradation must stay below 50%, and the rule must have been frozen before the later data was seen. This retrospective split tests temporal stability; it is not untouched prospective validation. Missing time, mixed timezones, incomparable currencies, rejected rows, or unresolved exact duplicates cause an explicit refusal.
4. **Integrity gate**: rejected rows and exact duplicates with known timestamps remain counted and disclosed rather than silently removed. Until resolved, the result stays `insufficient` and cannot certify positive edge.
5. **Gambling-pattern scan**: negative expectancy, results propped up by one outsized win, small wins / large losses, extreme drawdowns, long losing streaks, pure day trading…

## Build your own automated trading program

Beyond the single-file strategy skeleton, this tool can also use an **interactive scaffold**
to generate a complete, runnable personal automated-trading project. The generated project first
runs the strategy and simulated fills through `PaperBroker`. Real automation additionally requires
you to connect the broker API, provide a live-data runner that processes only the latest completed
bar, and preserve the four safety checks below in that custom live runner.

```bash
# 1. Preview four open-source chart styles side by side
python -m core.cli chart-preview          # creates chart_preview.html

# 2. List the available broker and chart templates
python -m core.cli brokers
python -m core.cli charts

# 3. Generate a project; --from-analysis embeds the conservative stage result
python -m core.cli scaffold --name my_bot --broker binance --chart lightweight \
    --market crypto --symbols "BTCUSDT,ETHUSDT" --from-analysis my_trades.csv

# 4. Run it in paper mode by default
cd my_bot && pip install -r requirements.txt && python main.py
```

**Available brokers** (every generated project defaults to `PaperBroker`; live-broker/exchange
connection completeness and prerequisites vary by template, and you must configure your own API key;
run `brokers` for the full list):

| key | Broker | Market |
|-----|------|------|
| `paper` | Paper trading (default; fully functional, no real money) | All |
| `shioaji` | SinoPac Shioaji | Taiwan stocks |
| `yuanta` | Yuanta SPARK API | Taiwan stocks / futures |
| `fubon` | Fubon Next-Gen API | Taiwan stocks / sub-brokerage |
| `kgi` | KGI SUPER PY | Taiwan + US stocks |
| `tw_futures` | Capital / President / Masterlink futures-style | Taiwan futures |
| `ibkr` | Interactive Brokers | US stocks / global |
| `alpaca` | Alpaca | US stocks |
| `tradier` | Tradier (REST API) | US stocks |
| `binance` | Binance | Crypto |
| `pionex` | [Pionex (official spot REST API)](https://www.pionex.com/docs/api-docs/zh-hant) | Crypto spot |
| `okx` | OKX | Crypto |
| `bybit` | Bybit | Crypto |
| `ccxt` | ccxt (one interface for 100+ exchanges) | Crypto |

> Most Taiwan broker APIs require signing a risk-disclosure statement in person and an application review
> (often several business days); some require installing certificates or components. Each template's notes flag the key
> prerequisites, but the broker's official documentation is authoritative.

The `pionex` template pins the official production host `https://api.pionex.com`.
The current official spot OpenAPI does not list a sandbox or testnet, so production must not be
treated as a simulation environment. Stay on `PaperBroker` first; begin with a read-only key and
an IP allowlist. Pionex market buys use quote-currency `amount`, which is not the same as the
base-asset meaning of the shared `Order.quantity`; the template therefore rejects market buys.
Strategy `client_tag` values are kept separate from broker `client_order_id` values; the latter
may be supplied explicitly and are safely generated when omitted. This avoids treating localized
strategy reasons as order IDs and supports lookup-first recovery after a timeout. Existing holdings
without a reliable cost basis are forced to `hold`, and cancellation is followed by order/fill
queries because an accepted cancel does not prove zero fills. Use explicit symbols such as
`BTC_USDT`.

**Available open-source chart libraries**: `lightweight` (TradingView, Apache-2.0), `plotly` (MIT),
`mplfinance` (BSD), `echarts` (Apache-2.0).

### Four-layer safety design (protecting your money)

1. **Paper trading by default**: generated projects default to `PaperBroker`, which fully simulates matching without touching real money.
2. **Full-stage linkage**: the scaffold does not rely on the in-sample verdict alone. Only reliable
   out-of-sample persistence, currency and risk bases at `tiny_live_validation` can produce both
   `stage_code: tiny_live_validation` and `allow_live_trading: true`.
3. **Runtime multi-gate enforcement**: generated `main.py` rechecks those two fields,
   `risk.i_have_read_disclaimer is true`, and the local `ALLOW_LIVE_TRADING=True` switch before it
   calls the broker's confirmation gate. Missing fields, wrong types, any other stage or broken YAML fail closed.
4. **Historical replay never touches live money**: the 120-bar historical/demo loop in `main.py`
   exits when it detects a live broker, preventing old signals from becoming a burst of real orders.
   Tiny live validation requires a separate runner that processes only the latest completed bar from
   a verifiably live data source.

`PaperBroker` is an immediate-fill simulator and has no pending-order queue. A buy limit below the
current quote or a sell limit above it is rejected; a marketable limit fills at the current quote
without violating its ceiling/floor. Before writing, the CLI rejects input/output aliases, duplicate
output paths, and existing report files. `scaffold` never overwrites a non-empty project and has no
`--force` option.

> The generated historical/demo runner **never submits live orders**. A live adapter can submit only
> after you implement a live runner, provide the keys and explicitly unlock the safety gates.
> Real financial trading must be performed by you, at your own full responsibility.
> Automating unverified gambling only loses money faster.

## Project structure

```
core/
  models.py            # unified Trade / TradeLog model, including contract multipliers
  markets.py           # market inference, multiplier allow-list and leverage metadata
  onboarding.py        # beginner recording and conservative stage routing
  analyzer.py          # high-level programmatic entry point
  cli.py               # command-line interface (18 commands)
  report.py            # Traditional Chinese text report
  report_html.py       # self-contained, XSS-safe HTML report and share card
  ingest/              # CSV/JSON/Excel import, beginner input and screenshot OCR review
  metrics/             # performance metrics and break-even requirements
  verdict/             # statistical verdict and significance tests
  strategy/            # profiling, skeletons, per-tag descriptions and counterfactuals
  backtest/            # chronological holdout validation
  trend/               # time summaries and one fixed early-vs-recent decay test
  montecarlo/          # ruin scenarios, drawdowns and losing-streak risk
  survivorship.py      # exact dynamic-programming survivorship calculation
  forensics/           # suspicious-performance diagnostics
  antiscam/            # scam patterns, checklist, text scanner and guru-claim checks
  broker/              # broker abstraction, PaperBroker and 13 live-broker templates (14 options)
  charts/              # four open-source chart templates and preview
  scaffold/            # self-contained personal trading-project generator
.claude/skills/anti-gambling-trader/SKILL.md   # Claude Code skill wrapper
.agents/skills/anti-gambling-trader/SKILL.md   # Codex/general agent skill wrapper
core/examples/         # bundled examples for three markets
tests/                 # automated regressions (use pytest collection for the current count)
```

> **Things we deliberately do not do**: no Benford's-law test (returns include negatives and do not span
> orders of magnitude, so the premise does not hold), no uncalibrated fake percentages like "87% likely a scam",
> no Kelly position-sizing advice, no "confidence" gauges, no future-return projections.
> See the [methodology](docs/methodology.md) (in Traditional Chinese).

## Tests

```bash
# Run the complete suite (recommended)
python -m pip install -e ".[dev]"
python -m pytest tests/ -v

# Without pytest, invoke each test module's built-in runner:
python tests/test_core.py
python tests/test_trading_tools.py
python tests/test_antiscam.py
python tests/test_usability.py
python tests/test_engine.py
python tests/test_debate_fixes.py
python tests/test_trend.py
python tests/test_expansion.py
python tests/test_skill_round.py
python tests/test_round7.py
python tests/test_round8.py
python tests/test_round9.py
python tests/test_round10.py
python tests/test_line_antiscam.py
python tests/test_missing_time_semantics.py
python tests/test_onboarding.py
python tests/test_screenshot_ingest.py
python tests/test_trade_logic_audit.py
```

## Author

好棒棒反詐協會 - 免費顧問 阿軒割割
(roughly: "the 'Awesome Anti-Fraud Association' — free consultant 'A-Hsuan the Cutter'", a tongue-in-cheek community handle)

> Honesty statement: the byline above is a community pseudonym, and the "好棒棒反詐協會" is **not** a registered
> legal entity or official organization. This project's credibility does not come from titles; it comes from:
> public source code, reproducible experiments (`experiments/`, fixed seeds), the
> [full methodology](docs/methodology.md), and continuously run automated regression tests.
> Anyone is welcome to examine and challenge it — which is exactly what this tool asks the "gurus" to do.

## License

MIT License — open source, free to use. See [LICENSE](LICENSE).

## ⚠️ Disclaimer

This tool is for **statistical analysis and education**; its output **does not constitute investment advice of any kind**.
Investing involves risk, and profits and losses are your own. Past performance does not indicate future results.
The author accepts no responsibility for decisions anyone makes based on this tool, or for their consequences.

For the full disclaimer, see [DISCLAIMER.md](DISCLAIMER.md).
