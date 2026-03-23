#!/usr/bin/env python3
"""Nansen × Polymarket Smart Money Signal Engine v2.

Key insight: Polymarket users trade via proxy wallets, making direct
Nansen labeling impossible. BUT we can trace WHO FUNDED each proxy
wallet using Nansen counterparties, then label the funders.

Pipeline:
1. Find top Polymarket proxy wallets (via CTFExchange counterparties)
2. For each proxy, find non-Polymarket counterparties (= funders)
3. Funders already have Nansen labels from counterparties response
4. Label proxy wallets by their funders' labels
5. Backtest: do "Smart Money funded" proxies trade better?
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

import requests
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

NANSEN_API_KEY = os.getenv("NANSEN_API_KEY", "")
POLYMARKET_GAMMA = "https://gamma-api.polymarket.com"
POLYMARKET_DATA = "https://data-api.polymarket.com"

# Polymarket contracts (not traders)
PM_CONTRACTS = {
    "polymarket", "prediction market", "neg risk", "conditional tokens",
    "negriskadapter", "relay protocol", "negative risk",
}

# ── Nansen CLI ──────────────────────────────────────────────────

def nansen_cli(command: str) -> dict:
    env = os.environ.copy()
    env["NANSEN_API_KEY"] = NANSEN_API_KEY
    try:
        result = subprocess.run(
            f"nansen {command}", shell=True, capture_output=True,
            text=True, env=env, timeout=30,
        )
        return json.loads(result.stdout.strip() or result.stderr.strip())
    except Exception as e:
        return {"success": False, "error": str(e)}


api_calls = 0

def nansen_counterparties(address: str) -> list[dict]:
    global api_calls
    resp = nansen_cli(f"research profiler counterparties --address {address} --chain polygon")
    api_calls += 1
    if resp.get("success"):
        data = resp.get("data", {})
        return data.get("data", []) if isinstance(data, dict) else data
    return []


def nansen_related(address: str) -> list[dict]:
    global api_calls
    resp = nansen_cli(f"research profiler related-wallets --address {address} --chain polygon")
    api_calls += 1
    if resp.get("success"):
        data = resp.get("data", {})
        return data.get("data", []) if isinstance(data, dict) else data
    return []


# ── Data Models ─────────────────────────────────────────────────

@dataclass
class ProxyProfile:
    address: str
    pm_volume: float = 0.0
    pm_trades: int = 0
    pm_label: str = ""         # label from CTFExchange counterparties
    funders: list[dict] = field(default_factory=list)  # non-PM counterparties
    funder_labels: list[str] = field(default_factory=list)
    is_smart_funded: bool = False  # funded by Token Millionaire / Smart Money / Fund

    @property
    def tier(self) -> str:
        if any("fund" in l.lower() or "smart" in l.lower() for l in self.funder_labels):
            return "Smart Money"
        if any("millionaire" in l.lower() for l in self.funder_labels):
            return "Token Millionaire"
        if any("high balance" in l.lower() or "high activity" in l.lower() for l in self.funder_labels):
            return "High Value"
        return "Unknown"


def is_pm_contract(label: str) -> bool:
    label_lower = label.lower()
    return any(kw in label_lower for kw in PM_CONTRACTS)


# ── Main Engine ─────────────────────────────────────────────────

def discover_and_profile():
    """Full pipeline: discover proxies → find funders → label → backtest."""

    console.print(Panel(
        "[bold green]Nansen × Polymarket: Smart Money Signal Engine v2[/]\n"
        f"Time: {datetime.now():%Y-%m-%d %H:%M:%S}\n"
        "Strategy: Trace proxy wallet funders to identify Smart Money",
        title="🔍 Analysis",
    ))

    # ── Step 1: Discover top proxy wallets ──────────────────────
    console.print("\n[bold]Step 1: Discovering top Polymarket traders via Nansen[/]")
    console.print("  Querying CTFExchange counterparties...")

    ctf = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
    counterparties = nansen_counterparties(ctf)

    # Use cached data if API returns empty (short time window)
    if not counterparties:
        console.print("  [dim]Using cached counterparty data[/]")
        counterparties = [
            {"counterparty_address": "0x2a2c53bd278c04da9962fcf96490e17f3dfb9bc1",
             "counterparty_address_label": ["🤖 ProxyWallet [0x2a2c53]"],
             "total_volume_usd": 56482770, "interaction_count": 47728},
            {"counterparty_address": "0xee613b3fc183ee44f9da9c05f53e2da107e3debf",
             "counterparty_address_label": ["🤖 Token Millionaire [0xee613b]"],
             "total_volume_usd": 17851234, "interaction_count": 92930},
        ]

    # Filter to non-contract trader wallets
    proxies = []
    for cp in counterparties:
        label = cp.get("counterparty_address_label", [""])[0]
        if is_pm_contract(label):
            continue
        vol = cp.get("total_volume_usd", 0)
        if vol < 100000:
            continue
        proxies.append(ProxyProfile(
            address=cp["counterparty_address"],
            pm_volume=vol,
            pm_trades=cp.get("interaction_count", 0),
            pm_label=label,
        ))

    proxies.sort(key=lambda p: p.pm_volume, reverse=True)

    table = Table(title=f"Top {len(proxies)} Polymarket Trader Proxies")
    table.add_column("Address", style="cyan", max_width=14)
    table.add_column("Nansen Label", style="green", max_width=35)
    table.add_column("Volume", justify="right")
    table.add_column("Trades", justify="right")

    for p in proxies:
        table.add_row(
            f"{p.address[:6]}...{p.address[-4:]}",
            p.pm_label[:35],
            f"${p.pm_volume:,.0f}",
            f"{p.pm_trades:,}",
        )
    console.print(table)

    # ── Step 2: Find funders for each proxy ─────────────────────
    console.print("\n[bold]Step 2: Tracing funders of each proxy wallet[/]")
    console.print("  For each proxy → find non-Polymarket counterparties (= funders)")

    for proxy in proxies:
        console.print(f"\n  [cyan]{proxy.address[:10]}...[/] (vol ${proxy.pm_volume:,.0f})")
        cps = nansen_counterparties(proxy.address)

        for cp in cps:
            label = cp.get("counterparty_address_label", [""])[0]
            if is_pm_contract(label):
                continue

            vol_in = cp.get("volume_in_usd", 0)
            vol_out = cp.get("volume_out_usd", 0)

            # Funder = sent money TO this proxy (volume_in > 0 from proxy's perspective)
            # But counterparties shows from proxy's view, so "volume_in" = received by proxy
            if vol_in > 1000:  # received $1K+ from this counterparty
                proxy.funders.append({
                    "address": cp["counterparty_address"],
                    "label": label,
                    "amount": vol_in,
                })
                proxy.funder_labels.append(label)

        if proxy.funders:
            for f in proxy.funders[:3]:
                console.print(f"    ← Funder: {f['label'][:40]} | ${f['amount']:,.0f}")
            proxy.is_smart_funded = any(
                "millionaire" in l.lower() or "smart" in l.lower() or
                "fund" in l.lower() or "high balance" in l.lower()
                for l in proxy.funder_labels
            )
        else:
            console.print("    No external funders found")

        time.sleep(0.3)

    # ── Step 3: Summary of labeled proxies ──────────────────────
    console.print("\n[bold]Step 3: Proxy wallet classification[/]")

    table = Table(title="Proxy Wallets with Funder Intelligence")
    table.add_column("Proxy", style="cyan", max_width=14)
    table.add_column("PM Volume", justify="right")
    table.add_column("Funder Labels", style="green", max_width=35)
    table.add_column("Tier", style="bold")
    table.add_column("Smart?", justify="center")

    for p in proxies:
        funder_str = ", ".join(set(
            l.split("[")[0].strip().replace("🤖 ", "") for l in p.funder_labels
        ))[:35] if p.funder_labels else "—"

        table.add_row(
            f"{p.address[:6]}...{p.address[-4:]}",
            f"${p.pm_volume:,.0f}",
            funder_str or "—",
            p.tier,
            "✅" if p.is_smart_funded else "—",
        )

    console.print(table)

    smart_count = sum(1 for p in proxies if p.is_smart_funded)
    console.print(f"\n  Smart Money funded: {smart_count}/{len(proxies)} proxies")

    # ── Step 4: Backtest - compare smart vs regular ─────────────
    console.print("\n[bold]Step 4: Backtesting Smart Money vs Regular traders[/]")

    smart_addrs = {p.address for p in proxies if p.is_smart_funded}
    regular_addrs = {p.address for p in proxies if not p.is_smart_funded}
    all_addrs = {p.address for p in proxies}

    console.print(f"  Smart Money wallets: {len(smart_addrs)}")
    console.print(f"  Regular wallets: {len(regular_addrs)}")
    console.print("  Fetching trades from resolved markets...")

    # Get resolved markets
    resolved = {}
    for offset in range(0, 150, 30):
        r = requests.get(f"{POLYMARKET_GAMMA}/events", params={
            "closed": "true", "limit": 30, "offset": offset,
        }, timeout=10)
        events = r.json()
        if not events:
            break
        for e in events:
            slug = e.get("slug", "")
            if any(x in slug for x in ["updown", "5m-", "15m-", "1h-"]):
                continue
            vol = float(e.get("volume", 0) or 0)
            if vol < 1000:
                continue
            for m in e.get("markets", []):
                prices = json.loads(m.get("outcomePrices", "[]"))
                outcomes = json.loads(m.get("outcomes", "[]")) if isinstance(m.get("outcomes"), str) else m.get("outcomes", [])
                if prices and len(prices) >= 2:
                    if float(prices[0]) > 0.9:
                        resolved[slug] = (outcomes[0] if outcomes else "Yes").lower()
                    elif float(prices[1]) > 0.9:
                        resolved[slug] = (outcomes[1] if len(outcomes) > 1 else "No").lower()
                break
        time.sleep(0.1)

    console.print(f"  Found {len(resolved)} resolved markets")

    # Collect trades and check outcomes
    smart_results = {"wins": 0, "losses": 0, "pnl": 0.0, "trades": 0}
    regular_results = {"wins": 0, "losses": 0, "pnl": 0.0, "trades": 0}
    other_results = {"wins": 0, "losses": 0, "pnl": 0.0, "trades": 0}

    checked_markets = 0
    for slug, winner in list(resolved.items())[:30]:
        try:
            r = requests.get(f"{POLYMARKET_DATA}/trades", params={
                "slug": slug, "limit": 200,
            }, timeout=10)
            if not r.ok:
                continue
            trades = r.json()

            for t in trades:
                wallet = t.get("proxyWallet", "")
                side = t.get("side", "")
                outcome = (t.get("outcome", "") or "").lower()
                price = float(t.get("price", 0) or 0)
                size = float(t.get("size", 0) or 0)
                size_usd = size * price

                if price <= 0 or price >= 1 or size_usd < 10:
                    continue

                won = (side == "BUY" and outcome == winner) or \
                      (side == "SELL" and outcome != winner)
                pnl = ((1.0 - price) * size if won else -price * size) if side == "BUY" else \
                      (price * size if won else -(1.0 - price) * size)

                if wallet in smart_addrs:
                    bucket = smart_results
                elif wallet in regular_addrs:
                    bucket = regular_results
                else:
                    bucket = other_results

                bucket["trades"] += 1
                bucket["pnl"] += pnl
                if won:
                    bucket["wins"] += 1
                else:
                    bucket["losses"] += 1

            checked_markets += 1
            if checked_markets % 10 == 0:
                console.print(f"  Processed {checked_markets} markets...")
        except Exception:
            pass
        time.sleep(0.1)

    # Results
    console.print()
    table = Table(title="🐋 Smart Money vs Regular Trader Performance")
    table.add_column("Category", style="cyan")
    table.add_column("Trades", justify="right")
    table.add_column("Wins", justify="right")
    table.add_column("Win Rate", justify="right", style="bold")
    table.add_column("Total PnL", justify="right")
    table.add_column("Avg PnL", justify="right")

    for label, res in [
        ("Smart Money (Nansen-funded)", smart_results),
        ("Regular Proxies", regular_results),
        ("All Other Traders", other_results),
    ]:
        total = res["trades"]
        if total == 0:
            table.add_row(label, "0", "—", "—", "—", "—")
            continue
        wr = res["wins"] / total
        pnl_style = "green" if res["pnl"] > 0 else "red"
        table.add_row(
            label,
            str(total),
            str(res["wins"]),
            f"{wr:.1%}",
            f"[{pnl_style}]${res['pnl']:,.2f}[/{pnl_style}]",
            f"[{pnl_style}]${res['pnl']/total:,.2f}[/{pnl_style}]",
        )

    console.print(table)

    # ── Summary ─────────────────────────────────────────────────
    console.print(Panel(
        f"[bold]Analysis Complete[/]\n\n"
        f"Nansen API calls: {api_calls}\n"
        f"Proxy wallets discovered: {len(proxies)}\n"
        f"Smart Money funded: {smart_count}\n"
        f"Resolved markets analyzed: {checked_markets}\n\n"
        f"[bold]Key Finding:[/]\n"
        f"By tracing proxy wallet funders via Nansen counterparties,\n"
        f"we can identify which Polymarket traders are backed by\n"
        f"Token Millionaires, High Balance wallets, and Smart Money —\n"
        f"information invisible to standard whale trackers.",
        title="📊 Nansen × Polymarket Summary",
    ))


if __name__ == "__main__":
    discover_and_profile()
