# X Post Draft for #NansenCLI Hackathon

## Post 1 (Main Thread)

🔍 Built a Smart Money Signal Engine for @Polymarket using @nansen_ai CLI

It discovers whale wallets trading on Polymarket, labels them with Nansen's intelligence (Fund, Smart Trader, Token Millionaire), and generates actionable signals.

Here's what I found 🧵👇

#NansenCLI

## Post 2

The idea: Most Polymarket whale trackers just flag big trades ($10K+).

But a $50K bet from a Nansen-labeled "Fund" wallet means something very different than $50K from a fresh wallet.

So I built a pipeline: Nansen profiler → Polymarket markets → Smart Money Consensus

## Post 3

Step 1: Discover whales automatically

Using `nansen research profiler counterparties` on Polymarket's CTFExchange contract, I found the top wallets by volume:

• ProxyWallet: $56.5M volume, 47K trades
• Token Millionaire: $17.8M volume, 93K trades
• + more labeled wallets

## Post 4

Step 2: Profile each wallet with Nansen labels

`nansen research profiler labels --address 0x... --chain polygon`

Gets behavioral labels, entity names, Smart Money status, and Fund tags — intel no other Polymarket tracker has.

## Post 5

Step 3: Cross-reference with live Polymarket markets

Scan the top markets (MicroStrategy BTC, Macron, UK Election, etc.) and check which Nansen-labeled wallets are actively trading.

Smart Money Consensus = when multiple labeled wallets agree on a direction.

## Post 6

Step 4: Smart Money Flows on Polygon

`nansen research smart-money netflow --chain polygon`

Track where institutional capital is moving on-chain. When smart money flows into USDC on Polygon → potential Polymarket positioning.

## Post 7

Results from the analysis:
• 5 whale wallets profiled with Nansen labels
• Top Polymarket markets scanned for labeled activity
• Polygon smart money netflow tracked
• All via CLI — designed for AI agent integration

## Post 8

Tech stack:
• @nansen_ai CLI for wallet intelligence
• Polymarket Gamma/CLOB API for market data
• Python engine for signal generation
• Rich terminal dashboard

Code: [github link]

## Post 9

What's next:
• Real-time alerts when Nansen-labeled wallets take positions
• Historical accuracy tracking per wallet label category
• AI agent integration (Nansen CLI is agent-native!)

This is just the beginning of combining on-chain intelligence with prediction markets.

#NansenCLI @nansen_ai
