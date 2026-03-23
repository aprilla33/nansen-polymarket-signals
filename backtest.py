#!/usr/bin/env python3
"""Backtest: Would following Polymarket whales have been profitable?

Uses Polymarket Data API (free, no key) to:
1. Collect trades from resolved markets
2. Identify whale trades (large size)
3. Check if following whales → profit
4. Generate stats by whale tier
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

import requests
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

POLYMARKET_DATA = "https://data-api.polymarket.com"
POLYMARKET_GAMMA = "https://gamma-api.polymarket.com"

# Whale thresholds
WHALE_MIN_SIZE = 100      # $100+ per trade
BIG_WHALE_MIN_SIZE = 500  # $500+
MEGA_WHALE_MIN_SIZE = 1000  # $1000+


@dataclass
class TradeRecord:
    wallet: str
    side: str        # BUY or SELL
    outcome: str     # "Yes"/"No" or specific outcome
    size: float
    price: float
    timestamp: int
    market_title: str
    market_slug: str
    resolved_price: float = 0.0  # 1.0 if won, 0.0 if lost
    pnl: float = 0.0


@dataclass
class WalletStats:
    address: str
    name: str = ""
    total_trades: int = 0
    total_volume: float = 0.0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0

    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        return self.wins / total if total > 0 else 0


def fetch_recent_trades(offset: int = 0, limit: int = 1000) -> list[dict]:
    """Fetch recent trades from Polymarket data API."""
    try:
        resp = requests.get(
            f"{POLYMARKET_DATA}/trades",
            params={"limit": limit, "offset": offset},
            timeout=15,
        )
        if resp.ok:
            return resp.json()
    except Exception as e:
        console.print(f"  [red]Error fetching trades: {e}[/]")
    return []


def fetch_resolved_markets(limit: int = 20) -> list[dict]:
    """Fetch recently resolved markets."""
    try:
        resp = requests.get(
            f"{POLYMARKET_GAMMA}/events",
            params={"closed": "true", "limit": limit},
            timeout=10,
        )
        if resp.ok:
            return resp.json()
    except Exception as e:
        console.print(f"  [red]Error fetching markets: {e}[/]")
    return []


def run_backtest():
    console.print(Panel(
        "[bold green]Polymarket Whale Following Backtest[/]\n"
        "Would copying whale trades have been profitable?",
        title="🐋 Backtest",
    ))

    # Step 1: Collect trades
    console.print("\n[bold]Step 1: Collecting recent trades...[/]")
    all_trades = []
    for offset in range(0, 5000, 1000):
        trades = fetch_recent_trades(offset=offset, limit=1000)
        if not trades:
            break
        all_trades.extend(trades)
        console.print(f"  Fetched {len(all_trades)} trades...")
        time.sleep(0.3)

    console.print(f"  Total: {len(all_trades)} trades collected")

    if not all_trades:
        console.print("[red]No trades found[/]")
        return

    # Step 2: Analyze trade sizes
    console.print("\n[bold]Step 2: Analyzing trade distribution...[/]")

    sizes = [float(t.get("size", 0) or 0) * float(t.get("price", 0) or 0) for t in all_trades]
    sizes = [s for s in sizes if s > 0]

    if sizes:
        table = Table(title="Trade Size Distribution")
        table.add_column("Category", style="cyan")
        table.add_column("Count", justify="right")
        table.add_column("% of Total", justify="right")
        table.add_column("Total Volume", justify="right")

        total = len(sizes)
        for label, threshold in [("All trades", 0), ("$100+", 100), ("$500+", 500), ("$1K+", 1000), ("$5K+", 5000)]:
            count = sum(1 for s in sizes if s >= threshold)
            vol = sum(s for s in sizes if s >= threshold)
            table.add_row(label, str(count), f"{count/total:.1%}", f"${vol:,.0f}")

        console.print(table)

    # Step 3: Group by wallet and find whales
    console.print("\n[bold]Step 3: Identifying whale wallets...[/]")

    wallet_trades: dict[str, list[dict]] = defaultdict(list)
    for t in all_trades:
        wallet = t.get("proxyWallet", "")
        if wallet:
            wallet_trades[wallet].append(t)

    # Rank wallets by volume
    wallet_volumes = {}
    for wallet, trades in wallet_trades.items():
        vol = sum(float(t.get("size", 0) or 0) * float(t.get("price", 0) or 0) for t in trades)
        wallet_volumes[wallet] = vol

    sorted_wallets = sorted(wallet_volumes.items(), key=lambda x: x[1], reverse=True)

    table = Table(title="Top 15 Wallets by Volume")
    table.add_column("Rank", style="dim", justify="right")
    table.add_column("Wallet", style="cyan", max_width=14)
    table.add_column("Name", style="green", max_width=20)
    table.add_column("Trades", justify="right")
    table.add_column("Volume", justify="right")
    table.add_column("Avg Size", justify="right")

    for i, (wallet, vol) in enumerate(sorted_wallets[:15]):
        trades = wallet_trades[wallet]
        name = trades[0].get("name", "") or trades[0].get("pseudonym", "")
        avg = vol / len(trades) if trades else 0
        table.add_row(
            str(i + 1),
            f"{wallet[:6]}...{wallet[-4:]}",
            name[:20] or "—",
            str(len(trades)),
            f"${vol:,.0f}",
            f"${avg:,.0f}",
        )

    console.print(table)

    # Step 4: Backtest - check resolved outcomes
    console.print("\n[bold]Step 4: Backtesting whale trade outcomes...[/]")

    # Group trades by market to check resolution
    market_trades: dict[str, list[dict]] = defaultdict(list)
    for t in all_trades:
        slug = t.get("eventSlug", "") or t.get("slug", "")
        if slug:
            market_trades[slug].append(t)

    # Check resolved markets
    resolved_slugs = set()
    resolved_outcomes = {}  # slug -> winning outcome

    console.print("  Checking market resolutions...")
    checked = 0
    for slug in list(market_trades.keys())[:50]:
        try:
            resp = requests.get(
                f"{POLYMARKET_GAMMA}/events",
                params={"slug": slug},
                timeout=5,
            )
            if resp.ok:
                events = resp.json()
                if events:
                    event = events[0]
                    if event.get("closed"):
                        resolved_slugs.add(slug)
                        for m in event.get("markets", []):
                            prices = json.loads(m.get("outcomePrices", "[]"))
                            outcomes = json.loads(m.get("outcomes", "[]")) if isinstance(m.get("outcomes"), str) else m.get("outcomes", [])
                            if prices and len(prices) >= 2:
                                # Winner is the outcome with price = 1 (or closest to 1)
                                if float(prices[0]) > 0.9:
                                    resolved_outcomes[slug] = outcomes[0] if outcomes else "Yes"
                                elif float(prices[1]) > 0.9:
                                    resolved_outcomes[slug] = outcomes[1] if len(outcomes) > 1 else "No"
            checked += 1
            if checked % 10 == 0:
                console.print(f"  Checked {checked} markets, {len(resolved_slugs)} resolved")
                time.sleep(0.2)
        except Exception:
            pass

    console.print(f"  Found {len(resolved_slugs)} resolved markets with {len(resolved_outcomes)} outcomes")

    # Step 5: Calculate PnL for whale trades on resolved markets
    console.print("\n[bold]Step 5: Calculating whale PnL on resolved markets...[/]")

    whale_results = {"all": [], "$100+": [], "$500+": [], "$1K+": []}

    for slug, winning_outcome in resolved_outcomes.items():
        trades = market_trades.get(slug, [])
        for t in trades:
            size_usd = float(t.get("size", 0) or 0) * float(t.get("price", 0) or 0)
            outcome = t.get("outcome", "")
            side = t.get("side", "")
            price = float(t.get("price", 0) or 0)

            if price <= 0 or price >= 1:
                continue

            # Determine if trade was on winning side
            bought_winner = (side == "BUY" and outcome.lower() == winning_outcome.lower())
            sold_loser = (side == "SELL" and outcome.lower() != winning_outcome.lower())
            won = bought_winner or sold_loser

            if side == "BUY":
                pnl = (1.0 - price) * float(t.get("size", 0) or 0) if won else -price * float(t.get("size", 0) or 0)
            else:
                pnl = price * float(t.get("size", 0) or 0) if won else -(1.0 - price) * float(t.get("size", 0) or 0)

            result = {"won": won, "pnl": pnl, "size": size_usd, "wallet": t.get("proxyWallet", "")}

            whale_results["all"].append(result)
            if size_usd >= 100:
                whale_results["$100+"].append(result)
            if size_usd >= 500:
                whale_results["$500+"].append(result)
            if size_usd >= 1000:
                whale_results["$1K+"].append(result)

    # Display results
    table = Table(title="🐋 Whale Following Backtest Results")
    table.add_column("Tier", style="cyan")
    table.add_column("Trades", justify="right")
    table.add_column("Wins", justify="right")
    table.add_column("Win Rate", justify="right", style="bold")
    table.add_column("Total PnL", justify="right")
    table.add_column("Avg PnL/Trade", justify="right")
    table.add_column("Verdict", justify="center")

    for tier, results in whale_results.items():
        if not results:
            table.add_row(tier, "0", "—", "—", "—", "—", "—")
            continue

        wins = sum(1 for r in results if r["won"])
        total = len(results)
        wr = wins / total if total > 0 else 0
        total_pnl = sum(r["pnl"] for r in results)
        avg_pnl = total_pnl / total if total > 0 else 0

        pnl_style = "green" if total_pnl > 0 else "red"
        verdict = "✅ PROFIT" if total_pnl > 0 else "❌ LOSS"

        table.add_row(
            tier,
            str(total),
            str(wins),
            f"{wr:.1%}",
            f"[{pnl_style}]${total_pnl:,.2f}[/{pnl_style}]",
            f"[{pnl_style}]${avg_pnl:,.2f}[/{pnl_style}]",
            verdict,
        )

    console.print(table)

    # Top whale wallet performance
    if whale_results["$100+"]:
        console.print("\n[bold]Top Whale Wallets by PnL:[/]")
        wallet_pnl: dict[str, dict] = defaultdict(lambda: {"pnl": 0, "trades": 0, "wins": 0})
        for r in whale_results["$100+"]:
            w = r["wallet"]
            wallet_pnl[w]["pnl"] += r["pnl"]
            wallet_pnl[w]["trades"] += 1
            if r["won"]:
                wallet_pnl[w]["wins"] += 1

        sorted_pnl = sorted(wallet_pnl.items(), key=lambda x: x[1]["pnl"], reverse=True)

        table = Table(title="Top 10 Whale Wallets (Resolved Markets)")
        table.add_column("Rank", style="dim", justify="right")
        table.add_column("Wallet", style="cyan", max_width=14)
        table.add_column("Trades", justify="right")
        table.add_column("Win Rate", justify="right")
        table.add_column("PnL", justify="right", style="bold")
        table.add_column("Follow?", justify="center")

        for i, (wallet, stats) in enumerate(sorted_pnl[:10]):
            wr = stats["wins"] / stats["trades"] if stats["trades"] > 0 else 0
            pnl_style = "green" if stats["pnl"] > 0 else "red"
            follow = "✅ YES" if wr > 0.55 and stats["pnl"] > 0 else "❌ NO"
            table.add_row(
                str(i + 1),
                f"{wallet[:6]}...{wallet[-4:]}",
                str(stats["trades"]),
                f"{wr:.0%}",
                f"[{pnl_style}]${stats['pnl']:,.2f}[/{pnl_style}]",
                follow,
            )

        console.print(table)

    # Summary
    console.print(Panel(
        f"[bold]Backtest Complete[/]\n"
        f"Total trades analyzed: {len(all_trades)}\n"
        f"Unique wallets: {len(wallet_trades)}\n"
        f"Resolved markets checked: {len(resolved_slugs)}\n"
        f"Markets with outcomes: {len(resolved_outcomes)}",
        title="📊 Summary",
    ))


if __name__ == "__main__":
    run_backtest()
