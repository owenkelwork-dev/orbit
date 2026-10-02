"""Command line: `python -m orbit run`, `python -m orbit status`, `python -m orbit log-income`."""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date

from .agents import WORKERS
from .config import load_config
from .coordinator import run
from .llm import Claude
from .state import read_income


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orbit", description="Autonomy 10K agent runner")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="let ORBIT pick agents and run them (or name them with --agents)")
    p_run.add_argument("--agents", help=f"comma-separated subset of: {','.join(WORKERS)}")

    sub.add_parser("status", help="show income progress")

    p_log = sub.add_parser("log-income", help="append a row to data/income.csv")
    p_log.add_argument("channel", help="e.g. etsy, fiverr, affiliate, ugc")
    p_log.add_argument("amount", type=float)
    p_log.add_argument("note", nargs="?", default="")
    p_log.add_argument("--date", default=date.today().isoformat())

    args = parser.parse_args(argv)
    cfg = load_config()

    if args.cmd == "run":
        only = None
        if args.agents:
            only = [a.strip().lower() for a in args.agents.split(",") if a.strip()]
            unknown = [a for a in only if a not in WORKERS]
            if unknown:
                parser.error(f"unknown agents {unknown}; choose from {list(WORKERS)}")
        report = run(cfg, lambda: Claude(cfg.model, cfg.effort, cfg.max_web_searches), only=only)
        print(f"Report written to {report.relative_to(cfg.root)}")
        return 0

    if args.cmd == "log-income":
        date.fromisoformat(args.date)
        with (cfg.data_dir / "income.csv").open("a", newline="") as f:
            csv.writer(f).writerow([args.date, args.channel.lower(), f"{args.amount:.2f}", args.note])
        print(f"Logged ${args.amount:.2f} from {args.channel}.")

    income = read_income(cfg.data_dir / "income.csv", date.today())
    print(f"Total: ${income.total:,.2f} of ${cfg.target_usd:,.0f} ({income.total / cfg.target_usd:.1%})")
    for channel, amount in sorted(income.by_channel.items(), key=lambda kv: -kv[1]):
        print(f"  {channel:<12} ${amount:,.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
