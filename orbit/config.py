"""Paths and settings loaded from orbit.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    target_usd: float
    model: str
    effort: str
    max_web_searches: int
    monthly_budget_usd: float
    max_agents_per_run: int
    etsy_niche: str
    affiliate_niche: str
    ugc_niche: str
    fiverr_niche: str
    root: Path = ROOT

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def outputs_dir(self) -> Path:
        return self.root / "outputs"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def prompts_dir(self) -> Path:
        return self.root / "prompts"


def load_config(root: Path = ROOT) -> Config:
    raw = tomllib.loads((root / "orbit.toml").read_text())
    return Config(
        target_usd=float(raw["goal"]["target_usd"]),
        model=raw["model"]["name"],
        effort=raw["model"]["effort"],
        max_web_searches=int(raw["model"]["max_web_searches"]),
        monthly_budget_usd=float(raw["budget"]["monthly_usd"]),
        max_agents_per_run=int(raw["budget"]["max_agents_per_run"]),
        etsy_niche=raw["niches"]["etsy"],
        affiliate_niche=raw["niches"]["affiliate"],
        ugc_niche=raw["niches"]["ugc"],
        fiverr_niche=raw["niches"]["fiverr"],
        root=root,
    )


def load_prompt(cfg: Config, name: str) -> str:
    """System prompt for an agent: the shared rules followed by the agent's own prompt."""
    rules = (cfg.prompts_dir / "rules.md").read_text()
    own = (cfg.prompts_dir / f"{name}.md").read_text()
    return f"{rules.strip()}\n\n---\n\n{own.strip()}\n"
