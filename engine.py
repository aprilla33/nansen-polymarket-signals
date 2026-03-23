#!/usr/bin/env python3
"""Nansen × Polymarket Smart Money Signal Engine.

Discovers top Polymarket traders, labels them via Nansen,
tracks their positions, and generates Smart Money Consensus signals.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import requests
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.text import Text

console = Console()

# ── Config ──────────────────────────────────────────────────────
NANSEN_API_KEY = os.getenv("NANSEN_API_KEY", "")
POLYMARKET_GAMMA = "https://gamma-api.polymarket.com"
POLYMARKET_DATA = "https://data-api.polymarket.com"
POLYMARKET_CLOB = "https://clob.polymarket.com"

# Polymarket CTFExchange contract (used to discover traders)
POLYMARKET_CTF = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"

# Known Polymarket high-volume trader addresses (discovered via Nansen counterparties)
SEED_WALLETS = [
    "0x2a2c53bd278c04da9962fcf96490e17f3dfb9bc1",  # ProxyWallet, $56.5M vol
    "0xee613b3fc183ee44f9da9c05f53e2da107e3debf",  # Token Millionaire, $17.8M vol
]


# ── Nansen CLI Wrapper ──────────────────────────────────────────

def nansen_cli(command: str) -> dict:
    """Run a Nansen CLI command and return parsed JSON."""
    env = os.environ.copy()
    env["NANSEN_API_KEY"] = NANSEN_API_KEY

    try:
        result = subprocess.run(
            f"nansen {command}",
            shell=True, capture_output=True, text=True,
            env=env, timeout=30,
        )
        output = result.stdout.strip()
        if not output:
            output = result.stderr.strip()
        return json.loads(output)
    except (json.JSONDecodeError, subprocess.TimeoutExpired) as e:
        return {"success": False, "error": str(e)}


def nansen_get_labels(address: str) -> list[dict]:
    """Get Nansen labels for a wallet address."""
    resp = nansen_cli(f"research profiler labels --address {address} --chain polygon")
    if resp.get("success"):
        return resp.get("data", [])
    return []


def nansen_get_balance(address: str) -> list[dict]:
    """Get current token balances for a wallet."""
    resp = nansen_cli(f"research profiler balance --address {address} --chain polygon")
    if resp.get("success"):
        data = resp.get("data", {})
        if isinstance(data, dict):
            return data.get("data", [])
        return data
    return []


def nansen_get_pnl(address: str, days: int = 30) -> dict:
    """Get PnL summary for a wallet."""
    resp = nansen_cli(f"research profiler pnl-summary --address {address} --chain polygon --days {days}")
    if resp.get("success"):
        return resp.get("data", {})
    return {}


def nansen_get_counterparties(address: str) -> list[dict]:
    """Get top counterparties for a wallet."""
    resp = nansen_cli(f"research profiler counterparties --address {address} --chain polygon --days 30")
    if resp.get("success"):
        data = resp.get("data", {})
        if isinstance(data, dict):
            return data.get("data", [])
        return data
    return []


def nansen_get_transactions(address: str, days: int = 7) -> list[dict]:
    """Get recent transactions."""
    resp = nansen_cli(f"research profiler transactions --address {address} --chain polygon --days {days} --limit 20")
    if resp.get("success"):
        data = resp.get("data", {})
        if isinstance(data, dict):
            return data.get("data", [])
        return data
    return []


def nansen_smart_money_netflow() -> list[dict]:
    """Get smart money net flows on Polygon."""
    resp = nansen_cli("research smart-money netflow --chain polygon")
    if resp.get("success"):
        data = resp.get("data", {})
        if isinstance(data, dict):
            return data.get("data", [])
        return data
    return []


# ── Polymarket API ──────────────────────────────────────────────

def pm_get_top_markets(limit: int = 10) -> list[dict]:
    """Get top active Polymarket events by volume."""
    resp = requests.get(
        f"{POLYMARKET_GAMMA}/events",
        params={"closed": "false", "limit": limit},
        timeout=10,
    )
    events = resp.json() if resp.ok else []
    # Sort by volume descending
    events.sort(key=lambda e: float(e.get("volume", 0) or 0), reverse=True)
    return events


def pm_get_market_trades(condition_id: str, limit: int = 50) -> list[dict]:
    """Get recent trades for a market (from data API)."""
    try:
        resp = requests.get(
            f"{POLYMARKET_DATA}/trades",
            params={"market": condition_id, "limit": limit},
            timeout=10,
        )
        return resp.json() if resp.ok else []
    except Exception:
        return []


def pm_get_orderbook(token_id: str) -> dict:
    """Get orderbook for a token."""
    try:
        resp = requests.get(
            f"{POLYMARKET_CLOB}/order-book",
            params={"token_id": token_id},
            timeout=10,
        )
        return resp.json() if resp.ok else {}
    except Exception:
        return {}


# ── Data Models ─────────────────────────────────────────────────

@dataclass
class WalletProfile:
    address: str
    labels: list[str] = field(default_factory=list)
    entity_name: str = ""
    is_smart_money: bool = False
    is_fund: bool = False
    usdc_balance: float = 0.0
    total_value_usd: float = 0.0
    pnl_30d: float = 0.0
    polymarket_interaction: bool = False
    label_details: list[dict] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        if self.entity_name:
            return self.entity_name
        if self.labels:
            return self.labels[0]
        return f"{self.address[:6]}...{self.address[-4:]}"

    @property
    def trust_score(self) -> float:
        """0-100 score based on labels and history."""
        score = 30  # base
        if self.is_smart_money:
            score += 30
        if self.is_fund:
            score += 25
        if self.polymarket_interaction:
            score += 10
        if self.pnl_30d > 0:
            score += 5
        return min(score, 100)


@dataclass
class SmartMoneySignal:
    market_title: str
    market_slug: str
    wallet: WalletProfile
    direction: str  # "YES" or "NO"
    confidence: float
    timestamp: str


# ── Engine ──────────────────────────────────────────────────────

class SmartMoneyEngine:
    def __init__(self):
        self.profiles: dict[str, WalletProfile] = {}
        self.signals: list[SmartMoneySignal] = []
        self.api_calls = 0

    def profile_wallet(self, address: str) -> WalletProfile:
        """Build a complete wallet profile using Nansen."""
        if address in self.profiles:
            return self.profiles[address]

        console.print(f"  Profiling [cyan]{address[:10]}...[/]")

        # Get labels
        labels_data = nansen_get_labels(address)
        self.api_calls += 1

        labels = []
        entity_name = ""
        is_sm = False
        is_fund = False
        pm_interaction = False

        for item in labels_data:
            label = item.get("label", "")
            fullname = item.get("fullname", "")
            labels.append(label)

            if "Smart" in label:
                is_sm = True
            if "Fund" in label:
                is_fund = True
            if "Prediction Market" in label or "CTF" in label:
                pm_interaction = True
            if fullname and not fullname.startswith("​​"):
                entity_name = fullname

        # Get balance
        balances = nansen_get_balance(address)
        self.api_calls += 1

        usdc_bal = 0
        total_val = 0
        for b in balances:
            val = b.get("value_usd", 0) or 0
            total_val += val
            if b.get("token_symbol") == "USDC":
                usdc_bal = b.get("token_amount", 0) or 0

        profile = WalletProfile(
            address=address,
            labels=labels,
            entity_name=entity_name,
            is_smart_money=is_sm,
            is_fund=is_fund,
            usdc_balance=usdc_bal,
            total_value_usd=total_val,
            polymarket_interaction=pm_interaction,
            label_details=labels_data,
        )

        self.profiles[address] = profile
        return profile

    def scan_markets(self, limit: int = 5):
        """Scan top Polymarket markets and find smart money activity."""
        console.print(Panel("[bold]Scanning Top Polymarket Markets[/]"))

        markets = pm_get_top_markets(limit)
        console.print(f"Found {len(markets)} active markets\n")

        for event in markets:
            title = event.get("title", "?")
            slug = event.get("slug", "")
            volume = float(event.get("volume", 0) or 0)

            console.print(f"[bold]{title}[/]")
            console.print(f"  Volume: ${volume:,.0f} | Slug: {slug}")

            # Get markets within event
            for mkt in event.get("markets", []):
                cond_id = mkt.get("conditionId", "")
                if not cond_id:
                    continue

                # Get recent large trades
                trades = pm_get_market_trades(cond_id, limit=20)
                large_trades = [t for t in trades if float(t.get("size", 0) or 0) > 100]

                if large_trades:
                    console.print(f"  Found {len(large_trades)} large trades")

                    # Profile unique traders
                    unique_traders = set()
                    for trade in large_trades[:5]:
                        trader = trade.get("maker", "") or trade.get("taker", "")
                        if trader and trader not in unique_traders:
                            unique_traders.add(trader)
                            profile = self.profile_wallet(trader)
                            if profile.trust_score > 50:
                                side = trade.get("side", "?")
                                signal = SmartMoneySignal(
                                    market_title=title,
                                    market_slug=slug,
                                    wallet=profile,
                                    direction=side,
                                    confidence=profile.trust_score,
                                    timestamp=datetime.now().isoformat(),
                                )
                                self.signals.append(signal)

            console.print()

    def discover_whales(self, top_n: int = 8):
        """Auto-discover top Polymarket traders via Nansen counterparties."""
        console.print(Panel("[bold]Discovering Polymarket Whales via Nansen[/]"))
        console.print(f"  Querying counterparties of CTFExchange contract...")

        counterparties = nansen_get_counterparties(POLYMARKET_CTF)
        self.api_calls += 1

        # If no results (short default window), use cached high-volume wallets
        if not counterparties:
            console.print("  [dim]Using cached counterparty data from prior analysis[/]")
            counterparties = [
                {"counterparty_address": "0x2a2c53bd278c04da9962fcf96490e17f3dfb9bc1",
                 "counterparty_address_label": ["🤖 ProxyWallet [0x2a2c53]"],
                 "total_volume_usd": 56482770, "interaction_count": 47728,
                 "volume_in_usd": 56482770, "volume_out_usd": 0},
                {"counterparty_address": "0xee613b3fc183ee44f9da9c05f53e2da107e3debf",
                 "counterparty_address_label": ["🤖 Token Millionaire [0xee613b]"],
                 "total_volume_usd": 17851234, "interaction_count": 92930,
                 "volume_in_usd": 17839108, "volume_out_usd": 12125},
                {"counterparty_address": "0x4d97dcd97ec945f40cf65f87097ace5ea0476045",
                 "counterparty_address_label": ["🤖 Polymarket: Polymarket Conditional Tokens"],
                 "total_volume_usd": 1274274620, "interaction_count": 34755962,
                 "volume_in_usd": 68648184, "volume_out_usd": 1205626436},
            ]

        discovered = []
        for cp in counterparties:
            addr = cp.get("counterparty_address", "")
            label = cp.get("counterparty_address_label", [])
            vol = cp.get("total_volume_usd", 0)

            # Skip known contracts (Conditional Tokens, NegRiskAdapter, etc.)
            label_str = " ".join(label).lower()
            if "conditional" in label_str or "negrisk" in label_str:
                continue
            if vol < 100000:  # skip small wallets
                continue

            discovered.append({
                "address": addr,
                "labels": label,
                "volume": vol,
                "in": cp.get("volume_in_usd", 0),
                "out": cp.get("volume_out_usd", 0),
                "interactions": cp.get("interaction_count", 0),
            })

        discovered.sort(key=lambda x: x["volume"], reverse=True)
        discovered = discovered[:top_n]

        table = Table(title=f"Top {len(discovered)} Polymarket Counterparties (by Nansen)")
        table.add_column("Address", style="cyan", max_width=14)
        table.add_column("Nansen Label", style="green", max_width=35)
        table.add_column("Total Volume", justify="right")
        table.add_column("Trades", justify="right")

        for d in discovered:
            lbl = d["labels"][0] if d["labels"] else "—"
            table.add_row(
                f"{d['address'][:6]}...{d['address'][-4:]}",
                lbl[:35],
                f"${d['volume']:,.0f}",
                f"{d['interactions']:,}",
            )
            # Add to seed wallets for deeper profiling
            if d["address"] not in SEED_WALLETS:
                SEED_WALLETS.append(d["address"])

        console.print(table)
        console.print(f"  Discovered {len(discovered)} whale wallets\n")
        return discovered

    def profile_seed_wallets(self):
        """Profile known Polymarket whale wallets."""
        console.print(Panel("[bold]Profiling Known Polymarket Whales via Nansen[/]"))

        table = Table(title="Wallet Profiles")
        table.add_column("Address", style="cyan", max_width=14)
        table.add_column("Nansen Labels", style="green")
        table.add_column("Entity", style="yellow")
        table.add_column("USDC", justify="right")
        table.add_column("Total USD", justify="right")
        table.add_column("Smart $", justify="center")
        table.add_column("Fund", justify="center")
        table.add_column("PM Active", justify="center")
        table.add_column("Trust", justify="right", style="bold")

        for addr in SEED_WALLETS:
            profile = self.profile_wallet(addr)

            labels_str = ", ".join(profile.labels[:3]) if profile.labels else "—"
            table.add_row(
                f"{addr[:6]}...{addr[-4:]}",
                labels_str[:40],
                profile.entity_name[:20] or "—",
                f"${profile.usdc_balance:,.0f}",
                f"${profile.total_value_usd:,.0f}",
                "✅" if profile.is_smart_money else "—",
                "✅" if profile.is_fund else "—",
                "✅" if profile.polymarket_interaction else "—",
                f"{profile.trust_score:.0f}",
            )
            time.sleep(0.5)  # rate limit

        console.print(table)

    def display_signals(self):
        """Display generated smart money signals."""
        if not self.signals:
            console.print("[yellow]No smart money signals detected yet.[/]")
            return

        table = Table(title="Smart Money Signals")
        table.add_column("Market", style="bold", max_width=40)
        table.add_column("Wallet", style="cyan")
        table.add_column("Labels", style="green")
        table.add_column("Direction", style="bold")
        table.add_column("Confidence", justify="right")

        for sig in self.signals:
            dir_style = "green" if sig.direction == "BUY" else "red"
            table.add_row(
                sig.market_title[:40],
                sig.wallet.display_name,
                ", ".join(sig.wallet.labels[:2]),
                f"[{dir_style}]{sig.direction}[/{dir_style}]",
                f"{sig.confidence:.0f}%",
            )

        console.print(table)

    def display_summary(self):
        """Display overall summary."""
        console.print(Panel(
            f"[bold]Summary[/]\n"
            f"Wallets profiled: {len(self.profiles)}\n"
            f"Smart Money wallets: {sum(1 for p in self.profiles.values() if p.is_smart_money)}\n"
            f"Fund wallets: {sum(1 for p in self.profiles.values() if p.is_fund)}\n"
            f"Signals generated: {len(self.signals)}\n"
            f"Nansen API calls: {self.api_calls}",
            title="Nansen × Polymarket Engine",
        ))

    def run_full_analysis(self):
        """Run complete analysis pipeline."""
        console.print(Panel(
            "[bold green]Nansen × Polymarket: Smart Money Signal Engine[/]\n"
            f"Time: {datetime.now():%Y-%m-%d %H:%M:%S}\n"
            f"Nansen API Key: {'✅ Set' if NANSEN_API_KEY else '❌ Missing'}",
            title="🔍 Starting Analysis",
        ))

        # Step 0: Discover whales from Nansen counterparties
        self.discover_whales(top_n=5)

        # Step 1: Profile known whales
        self.profile_seed_wallets()

        # Step 2: Check smart money flows on Polygon
        console.print(Panel("[bold]Smart Money Flows on Polygon[/]"))
        flows = nansen_smart_money_netflow()
        self.api_calls += 1
        if flows:
            table = Table(title="Polygon Smart Money Netflow")
            table.add_column("Token", style="cyan")
            table.add_column("24h Flow", justify="right")
            table.add_column("7d Flow", justify="right")
            table.add_column("30d Flow", justify="right")
            table.add_column("Traders", justify="right")

            for f in flows[:10]:
                table.add_row(
                    f.get("token_symbol", "?"),
                    f"${f.get('net_flow_24h_usd', 0):,.0f}",
                    f"${f.get('net_flow_7d_usd', 0):,.0f}",
                    f"${f.get('net_flow_30d_usd', 0):,.0f}",
                    str(f.get("trader_count", 0)),
                )
            console.print(table)
        else:
            console.print("  No flow data available")

        # Step 3: Scan top markets
        self.scan_markets(limit=5)

        # Step 4: Display results
        self.display_signals()
        self.display_summary()

        # Save results
        self._save_results()

    def _save_results(self):
        """Save analysis results to JSON."""
        output = {
            "timestamp": datetime.now().isoformat(),
            "api_calls": self.api_calls,
            "profiles": {
                addr: {
                    "address": p.address,
                    "labels": p.labels,
                    "entity_name": p.entity_name,
                    "is_smart_money": p.is_smart_money,
                    "is_fund": p.is_fund,
                    "usdc_balance": p.usdc_balance,
                    "total_value_usd": p.total_value_usd,
                    "polymarket_interaction": p.polymarket_interaction,
                    "trust_score": p.trust_score,
                }
                for addr, p in self.profiles.items()
            },
            "signals": [
                {
                    "market": s.market_title,
                    "wallet": s.wallet.address,
                    "wallet_name": s.wallet.display_name,
                    "direction": s.direction,
                    "confidence": s.confidence,
                }
                for s in self.signals
            ],
        }

        outpath = os.path.join(os.path.dirname(__file__), "data", "analysis.json")
        with open(outpath, "w") as f:
            json.dump(output, f, indent=2)
        console.print(f"\n[dim]Results saved to {outpath}[/]")


# ── CLI ─────────────────────────────────────────────────────────

def main():
    engine = SmartMoneyEngine()

    if "--profile" in sys.argv:
        idx = sys.argv.index("--profile")
        if idx + 1 < len(sys.argv):
            addr = sys.argv[idx + 1]
            profile = engine.profile_wallet(addr)
            console.print(Panel(
                f"Address: {profile.address}\n"
                f"Labels: {', '.join(profile.labels) or '—'}\n"
                f"Entity: {profile.entity_name or '—'}\n"
                f"Smart Money: {'Yes' if profile.is_smart_money else 'No'}\n"
                f"Fund: {'Yes' if profile.is_fund else 'No'}\n"
                f"USDC: ${profile.usdc_balance:,.2f}\n"
                f"Total: ${profile.total_value_usd:,.2f}\n"
                f"PM Active: {'Yes' if profile.polymarket_interaction else 'No'}\n"
                f"Trust Score: {profile.trust_score:.0f}/100",
                title=f"Wallet Profile: {addr[:12]}...",
            ))
    elif "--scan" in sys.argv:
        engine.scan_markets()
        engine.display_signals()
        engine.display_summary()
    else:
        engine.run_full_analysis()


if __name__ == "__main__":
    main()
