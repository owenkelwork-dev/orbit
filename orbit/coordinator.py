"""ORBIT, the coordinator: reads income, picks which agents run, runs them, writes the daily report."""

from __future__ import annotations

import json
import os
import traceback
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

from .agents import WORKERS, Context, Result
from .config import Config, load_prompt
from .llm import AgentError, Usage, ask_json
from .state import IncomeSummary, State, log_spend, month_spend, read_income


@dataclass
class Plan:
    agents: list[str]
    reasoning: str
    notes: list[str] = field(default_factory=list)
    source: str = "orbit"


def default_plan(state: State, today: date, limit: int) -> Plan:
    """Rule-based plan, used when ORBIT's own call fails."""
    def stale(agent: str, days: int) -> bool:
        since = state.days_since(agent, today)
        return since is None or since >= days

    order = []
    if state.pending_ideas():
        order.append("forge")
    elif stale("scout", 2):
        order.append("scout")
    if stale("pixel", 14):
        order.append("pixel")
    if stale("quill", 3):
        order.append("quill")
    if stale("pitch", 4):
        order.append("pitch")
    return Plan(order[:limit] or ["scout"], "Fallback rule-based plan.", source="fallback")


def fix_order(agents: list[str], state: State, limit: int) -> list[str]:
    """FORGE needs a SCOUT idea; run SCOUT first if none is waiting."""
    if "forge" in agents and not state.pending_ideas():
        if "scout" not in agents or agents.index("scout") > agents.index("forge"):
            agents = ["scout"] + [a for a in agents if a != "scout"]
            agents = agents[:limit]
    return agents


def _facts(cfg: Config, state: State, income: IncomeSummary, today: date, spent: float) -> dict[str, Any]:
    return {
        "today": today.isoformat(),
        "weekday": today.strftime("%A"),
        "goal_usd": cfg.target_usd,
        "income_total_usd": income.total,
        "income_last_30_days_usd": income.last_30_days,
        "income_by_channel_usd": income.by_channel,
        "income_entries": income.entries,
        "days_since_agent_last_ran": {a: state.days_since(a, today) for a in WORKERS},
        "scout_ideas_waiting_for_forge": [i["name"] for i in state.pending_ideas()],
        "products_built": [i["name"] for i in state.ideas if i.get("forged")],
        "affiliate_posts_written": len(state.affiliate_topics),
        "brands_pitched": len(state.pitched_brands),
        "api_spend_this_month_usd": spent,
        "monthly_budget_usd": cfg.monthly_budget_usd,
        "max_agents_this_run": cfg.max_agents_per_run,
    }


def plan_with_orbit(ctx: Context, facts: dict) -> Plan:
    limit = ctx.cfg.max_agents_per_run

    def check(data: Any) -> list[str]:
        if not isinstance(data, dict) or not isinstance(data.get("agents"), list):
            return ['reply must be a JSON object with an "agents" list']
        names = data["agents"]
        errors = [f"unknown agent {n!r}; choose from {sorted(WORKERS)}" for n in names if n not in WORKERS]
        if not 1 <= len(names) <= limit:
            errors.append(f"choose between 1 and {limit} agents")
        if len(set(names)) != len(names):
            errors.append("don't repeat agents")
        return errors

    chat = ctx.llm.chat(load_prompt(ctx.cfg, "orbit"))
    data = ask_json(chat, f"Current facts:\n```json\n{json.dumps(facts, indent=2)}\n```\nDecide today's plan as JSON.", check)
    return Plan(data["agents"], str(data.get("reasoning", "")), [str(n) for n in data.get("notes_for_owner") or []])


def _rel(path: Path, cfg: Config) -> str:
    return "../" + path.relative_to(cfg.root).as_posix()


def _progress_bar(fraction: float, width: int = 20) -> str:
    filled = max(0, min(width, round(fraction * width)))
    return "█" * filled + "░" * (width - filled)


def write_report(
    cfg: Config, today: date, income: IncomeSummary, plan: Plan, results: list[Result],
    failures: list[tuple[str, str]], usage: Usage, spent_before: float, state: State, skipped: str | None = None,
) -> Path:
    cfg.reports_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.reports_dir / f"{today.isoformat()}.md"
    n = 2
    while path.exists():
        path = cfg.reports_dir / f"{today.isoformat()}-{n}.md"
        n += 1

    frac = income.total / cfg.target_usd if cfg.target_usd else 0
    channels = "\n".join(f"| {c} | ${v:,.2f} |" for c, v in sorted(income.by_channel.items(), key=lambda kv: -kv[1]))
    lines = [
        f"# ORBIT daily report: {today.isoformat()}",
        "",
        f"## Progress to ${cfg.target_usd:,.0f}",
        "",
        f"`{_progress_bar(frac)}` **${income.total:,.2f}** ({frac:.1%}) · last 30 days: ${income.last_30_days:,.2f}",
        "",
        ("| Channel | Total |\n|---|---|\n" + channels) if channels else "_No income logged yet. Add rows to `data/income.csv`._",
    ]
    if income.bad_rows:
        lines += ["", "**Couldn't read these income rows (fix them in data/income.csv):**", *[f"- `{r}`" for r in income.bad_rows]]

    if skipped:
        lines += ["", "## Run skipped", "", skipped]
    else:
        lines += ["", "## Today's plan", "", f"Agents: {', '.join(a.upper() for a in plan.agents)}"
                  + (" _(fallback plan)_" if plan.source == "fallback" else ""), "", plan.reasoning or "_No reasoning given._"]
        lines += ["", "## What the agents did", ""]
        for r in results:
            lines.append(f"### {r.agent.upper()}\n\n{r.summary}\n")
            lines += [f"- [{f.name}]({_rel(f, cfg)})" for f in r.files]
            lines.append("")
        for agent, err in failures:
            lines.append(f"### {agent.upper()} (failed)\n\n```\n{err}\n```\n")

        todo = [t for r in results for t in r.todo]
        lines += ["## Your review queue", ""] + ([f"- [ ] {t}" for t in todo] or ["- Nothing new to review."])
        if plan.notes:
            lines += ["", "## Notes from ORBIT", "", *[f"- {n}" for n in plan.notes]]

    waiting = state.pending_ideas()
    lines += ["", "## Pipeline", "", f"- SCOUT ideas waiting for FORGE: {', '.join(i['name'] for i in waiting) or 'none'}",
              f"- Products built: {sum(1 for i in state.ideas if i.get('forged'))}",
              f"- Affiliate posts drafted: {len(state.affiliate_topics)}",
              f"- Brands with draft pitches: {len(state.pitched_brands)}"]
    lines += ["", "## API spend", "",
              f"- This run: ${usage.usd:.2f} ({usage.input_tokens:,} input / {usage.output_tokens:,} output tokens, {usage.web_searches} web searches)",
              f"- This month: ${spent_before + usage.usd:.2f} of ${cfg.monthly_budget_usd:.2f} budget",
              "", "---", "_ORBIT created files only. Nothing was posted, sent, bought, or signed up for._", ""]
    path.write_text("\n".join(lines))
    return path


def run(cfg: Config, make_llm: Callable[[], Any], today: date | None = None, only: list[str] | None = None) -> Path:
    today = today or date.today()
    state = State(cfg)
    income = read_income(cfg.data_dir / "income.csv", today)
    spend_path = cfg.data_dir / "spend.csv"
    spent = month_spend(spend_path, today)

    if spent >= cfg.monthly_budget_usd:
        msg = (f"Estimated API spend this month is ${spent:.2f}, at or over the ${cfg.monthly_budget_usd:.2f} budget "
               "in orbit.toml. Raise `[budget] monthly_usd` to keep running this month.")
        return write_report(cfg, today, income, Plan([], ""), [], [], Usage(), spent, state, skipped=msg)

    llm = make_llm()
    ctx = Context(cfg, llm, state, today)
    if only:
        plan = Plan(fix_order(only, state, len(only) + 1), "Agents chosen manually for this run.", source="manual")
    else:
        try:
            plan = plan_with_orbit(ctx, _facts(cfg, state, income, today, spent))
            plan.agents = fix_order(plan.agents, state, cfg.max_agents_per_run)
        except Exception as e:  # noqa: BLE001 - any planning failure falls back to rules
            plan = default_plan(state, today, cfg.max_agents_per_run)
            plan.reasoning += f" ORBIT's planning call failed: {e}"

    results: list[Result] = []
    failures: list[tuple[str, str]] = []
    for name in plan.agents:
        try:
            results.append(WORKERS[name](ctx))
            state.last_run[name] = today.isoformat()
        except AgentError as e:
            failures.append((name, str(e)))
        except Exception:  # noqa: BLE001 - one agent crashing shouldn't stop the others
            failures.append((name, traceback.format_exc(limit=3)))
        state.save()

    usage: Usage = getattr(llm, "usage", Usage())
    if usage.usd or usage.input_tokens:
        log_spend(spend_path, today, cfg.model, usage.input_tokens, usage.output_tokens, usage.web_searches, usage.usd)
    report = write_report(cfg, today, income, plan, results, failures, usage, spent, state)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(report.read_text())
    return report
