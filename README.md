# Nansen × Polymarket: Smart Money Signal Engine

> Combining Nansen's wallet intelligence with Polymarket prediction market data to generate actionable trading signals.

## What it does

1. **Discovers** top Polymarket traders from on-chain data
2. **Labels** them using Nansen's profiler (Fund, Smart Trader, entity names)
3. **Tracks** their current Polymarket positions and historical accuracy
4. **Generates** a Smart Money Consensus score per market
5. **Alerts** when labeled Smart Money wallets take new positions

## Why it matters

Existing Polymarket whale trackers use crude heuristics (trade size > $10K = whale). This tool leverages **Nansen's 500M+ wallet labels** to identify *who* is trading — hedge funds, known DeFi traders, institutional wallets — not just *how much*.

A $50K bet from a labeled Alameda-era fund wallet means something different than $50K from a fresh wallet.

## Tech Stack

- **Nansen CLI** — wallet profiling, labels, smart money flows
- **Polymarket Gamma/CLOB API** — market data, positions
- **Python 3** — analysis engine
- **Rich** — terminal dashboard

## Usage

```bash
# Install
pip install -r requirements.txt
npm install -g nansen-cli

# Set API keys
export NANSEN_API_KEY=your_key
export POLYMARKET_CLOB=https://clob.polymarket.com

# Run analysis
python engine.py --scan          # Scan top markets for smart money signals
python engine.py --profile 0x... # Profile a specific wallet
python engine.py --dashboard     # Live terminal dashboard
```

## #NansenCLI Hackathon

Built for [Nansen CLI Week 2 Hackathon](https://nansen.ai) — March 2026.

