"""Persistent state kept in data/: income log, API spend log, and the agent pipeline."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .config import Config

INCOME_FIELDS = ["date", "channel", "amount", "note"]
SPEND_FIELDS = ["date", "model", "input_tokens", "output_tokens", "web_searches", "usd"]


@dataclass
class IncomeSummary:
    total: float
    by_channel: dict[str, float]
    last_30_days: float
    entries: int
    bad_rows: list[str]


def read_income(path: Path, today: date) -> IncomeSummary:
    by_channel: dict[str, float] = {}
    total = last_30 = 0.0
    entries = 0
    bad: list[str] = []
    if path.exists():
        with path.open(newline="") as f:
            for n, row in enumerate(csv.DictReader(f), start=2):
                try:
                    d = date.fromisoformat((row.get("date") or "").strip())
                    amount = float((row.get("amount") or "").replace("$", "").replace(",", ""))
                except ValueError:
                    bad.append(f"line {n}: {row}")
                    continue
                channel = (row.get("channel") or "other").strip().lower() or "other"
                by_channel[channel] = by_channel.get(channel, 0.0) + amount
                total += amount
                entries += 1
                if 0 <= (today - d).days < 30:
                    last_30 += amount
    return IncomeSummary(round(total, 2), {k: round(v, 2) for k, v in by_channel.items()}, round(last_30, 2), entries, bad)


def month_spend(path: Path, today: date) -> float:
    if not path.exists():
        return 0.0
    prefix = today.strftime("%Y-%m")
    with path.open(newline="") as f:
        return round(sum(float(r["usd"]) for r in csv.DictReader(f) if r["date"].startswith(prefix)), 4)


def log_spend(path: Path, today: date, model: str, input_tokens: int, output_tokens: int, web_searches: int, usd: float) -> None:
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(SPEND_FIELDS)
        w.writerow([today.isoformat(), model, input_tokens, output_tokens, web_searches, f"{usd:.4f}"])


class State:
    """data/state.json: when each agent last ran, and SCOUT's ideas waiting for FORGE."""

    def __init__(self, cfg: Config):
        self.path = cfg.data_dir / "state.json"
        data = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.last_run: dict[str, str] = data.get("last_run", {})
        self.ideas: list[dict] = data.get("ideas", [])
        self.affiliate_topics: list[str] = data.get("affiliate_topics", [])
        self.pitched_brands: list[str] = data.get("pitched_brands", [])

    def save(self) -> None:
        self.path.write_text(
            json.dumps(
                {
                    "last_run": self.last_run,
                    "ideas": self.ideas,
                    "affiliate_topics": self.affiliate_topics,
                    "pitched_brands": self.pitched_brands,
                },
                indent=2,
            )
            + "\n"
        )

    def pending_ideas(self) -> list[dict]:
        return [i for i in self.ideas if not i.get("forged")]

    def days_since(self, agent: str, today: date) -> int | None:
        last = self.last_run.get(agent)
        return (today - date.fromisoformat(last)).days if last else None
