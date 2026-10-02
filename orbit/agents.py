"""The five worker agents. Each one writes files under outputs/ and returns a short result."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

from . import rules
from .builders import build_product, check_product_spec
from .config import Config, load_prompt
from .llm import AgentError, ask_json
from .state import State


@dataclass
class Context:
    cfg: Config
    llm: Any  # orbit.llm.Claude, or a stand-in with the same .chat() in tests
    state: State
    today: date


@dataclass
class Result:
    agent: str
    summary: str
    files: list[Path] = field(default_factory=list)
    todo: list[str] = field(default_factory=list)


def slugify(text: str, limit: int = 50) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:limit].rstrip("-") or "untitled"


def _out(ctx: Context, agent: str, name: str) -> Path:
    path = ctx.cfg.outputs_dir / agent / f"{ctx.today.isoformat()}-{name}"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _bullets(items: list[Any]) -> str:
    return "\n".join(f"- {i}" for i in items) if items else "- (none)"


def _sources(items: list[dict]) -> str:
    return "\n".join(f"- {i.get('claim', '')} ([source]({i.get('source_url', '')}))" for i in items) or "- (none)"


# ---------------------------------------------------------------- SCOUT

def _check_scout(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return ["reply must be a JSON object"]
    errors = []
    cands = data.get("candidates")
    if not isinstance(cands, list) or len(cands) < 3:
        errors.append("list at least 3 candidates")
    pick = data.get("pick")
    if not isinstance(pick, dict):
        return errors + ["pick is missing"]
    for key in ("name", "product_type", "why", "target_buyer"):
        if not pick.get(key):
            errors.append(f"pick.{key} is missing")
    if pick.get("product_type") not in ("spreadsheet", "printable", "other"):
        errors.append('pick.product_type must be "spreadsheet", "printable", or "other"')
    evidence = pick.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        errors.append("pick.evidence needs at least one sourced claim")
    else:
        errors += rules.check_sources(evidence)
    errors += rules.check_trademarks(pick, "pick")
    return errors


def scout(ctx: Context) -> Result:
    already = [i["name"] for i in ctx.state.ideas]
    chat = ctx.llm.chat(load_prompt(ctx.cfg, "scout"), web_search=True)
    data = ask_json(
        chat,
        f"Niche: {ctx.cfg.etsy_niche}\nToday: {ctx.today.isoformat()}\n"
        f"Ideas already picked (don't repeat): {json.dumps(already)}\n\n"
        "Research and pick the best next Etsy digital product. Reply with the JSON described in your instructions.",
        _check_scout,
    )
    pick = data["pick"]
    ctx.state.ideas.append({**pick, "picked_on": ctx.today.isoformat(), "forged": False})
    path = _out(ctx, "scout", slugify(pick["name"]) + ".md")
    cand_lines = "\n".join(
        f"| {c.get('name', '')} | {c.get('product_type', '')} | {c.get('score', '')} | {c.get('note', '')} |"
        for c in data["candidates"]
    )
    path.write_text(
        f"# SCOUT pick: {pick['name']}\n\n_{ctx.today.isoformat()} · niche: {ctx.cfg.etsy_niche}_\n\n"
        f"**Product type:** {pick['product_type']}  \n**Target buyer:** {pick['target_buyer']}\n\n"
        f"## Why this one\n\n{pick['why']}\n\n## Evidence (sourced)\n\n{_sources(pick.get('evidence') or [])}\n\n"
        f"## Risks\n\n{_bullets(pick.get('risks') or [])}\n\n"
        f"## All candidates\n\n| Idea | Type | Score | Note |\n|---|---|---|---|\n{cand_lines}\n"
    )
    return Result("scout", f"Picked **{pick['name']}** ({pick['product_type']}).", [path],
                  [f"Skim the SCOUT pick in `{path.relative_to(ctx.cfg.root)}` and delete the idea from data/state.json if you don't like it."])


# ---------------------------------------------------------------- FORGE

def _check_forge(data: Any) -> list[str]:
    if not isinstance(data, dict) or not isinstance(data.get("listing"), dict):
        return ['reply must be a JSON object with "listing" and "product"']
    errors = rules.check_etsy_listing(data["listing"])
    errors += check_product_spec(data.get("product"))
    errors += rules.check_trademarks(data, "listing or product")
    return errors


def forge(ctx: Context) -> Result:
    pending = ctx.state.pending_ideas()
    if not pending:
        raise AgentError("No SCOUT idea is waiting to be built. Run SCOUT first.")
    idea = pending[0]
    chat = ctx.llm.chat(load_prompt(ctx.cfg, "forge"))
    data = ask_json(
        chat,
        f"Build this product and its Etsy listing.\n\nIdea from SCOUT:\n```json\n{json.dumps(idea, indent=2)}\n```\n"
        "Reply with the JSON described in your instructions.",
        _check_forge,
    )
    listing, product = data["listing"], data["product"]
    folder = ctx.cfg.outputs_dir / "forge" / f"{ctx.today.isoformat()}-{slugify(idea['name'])}"
    folder.mkdir(parents=True, exist_ok=True)
    files = []
    built = build_product(product, folder)
    if built:
        files.append(built)
    spec_path = folder / "product-spec.json"
    spec_path.write_text(json.dumps(product, indent=2) + "\n")
    tags = "\n".join(f"{n}. `{t}` ({len(t)} chars)" for n, t in enumerate(listing["tags"], 1))
    listing_path = folder / "listing.md"
    listing_path.write_text(
        f"# Etsy listing: {idea['name']}\n\n_DRAFT for review · {ctx.today.isoformat()}_\n\n"
        f"## Title ({rules.word_count(listing['title'])} words)\n\n{listing['title']}\n\n"
        f"## Price\n\n${listing['price_usd']:.2f}\n\n"
        f"## Tags (13, each 20 characters or fewer)\n\n{tags}\n\n"
        f"## Description\n\n{listing['description']}\n\n"
        f"## Photo plan\n\n{_bullets(listing['photo_plan'])}\n\n"
        f"## Product file\n\n{('`' + built.name + '` in this folder') if built else product.get('build_notes', 'Not auto-built: make it by hand from product-spec.json.')}\n"
    )
    files[:0] = [listing_path]
    files.append(spec_path)
    idea["forged"] = True
    idea["forged_on"] = ctx.today.isoformat()
    todo = [f"Review `{listing_path.relative_to(ctx.cfg.root)}`" + (f" and open `{built.name}`" if built else "")
            + ", make the listing photos, then list it on Etsy yourself."]
    return Result("forge", f"Built listing for **{idea['name']}**" + (f" plus `{built.name}`." if built else "."), files, todo)


# ---------------------------------------------------------------- PIXEL

def _check_pixel(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return ["reply must be a JSON object"]
    return rules.check_fiverr_gig(data) + rules.check_trademarks(data, "gig")


def pixel(ctx: Context) -> Result:
    chat = ctx.llm.chat(load_prompt(ctx.cfg, "pixel"))
    gig = ask_json(chat, f"Service: {ctx.cfg.fiverr_niche}\nWrite the Fiverr gig and thumbnail concepts as JSON.", _check_pixel)
    path = _out(ctx, "pixel", "fiverr-gig.md")
    pkgs = "\n".join(
        f"| {p.get('name', '')} | ${p.get('price_usd', '')} | {p.get('delivery_days', '')} days | {p.get('includes', '')} |"
        for p in gig["packages"]
    )
    concepts = "\n\n".join(
        f"### {n}. {c.get('name', '')}\n\n- Layout: {c.get('layout', '')}\n- Colors: {c.get('colors', '')}\n- Text on image: {c.get('text', '')}"
        for n, c in enumerate(gig["thumbnail_concepts"], 1)
    )
    faq = "\n\n".join(f"**{q.get('q', '')}**  \n{q.get('a', '')}" for q in gig.get("faq") or [])
    path.write_text(
        f"# Fiverr gig: {ctx.cfg.fiverr_niche}\n\n_DRAFT for review · {ctx.today.isoformat()}_\n\n"
        f"## Title ({len(gig['title'])}/80 chars)\n\n{gig['title']}\n\n## Tags\n\n{_bullets(gig['tags'])}\n\n"
        f"## Packages\n\n| Tier | Price | Delivery | Includes |\n|---|---|---|---|\n{pkgs}\n\n"
        f"## Description\n\n{gig.get('description', '')}\n\n## FAQ\n\n{faq}\n\n"
        f"## Gig thumbnail concepts\n\n{concepts}\n"
    )
    return Result("pixel", "Wrote Fiverr gig copy and thumbnail concepts.", [path],
                  ["Make 3 sample thumbnails from PIXEL's concepts for your portfolio before publishing the gig."])


# ---------------------------------------------------------------- QUILL

def _check_quill(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return ["reply must be a JSON object"]
    errors = rules.check_affiliate_post(data)
    if not data.get("title"):
        errors.append("title is missing")
    return errors


def quill(ctx: Context) -> Result:
    chat = ctx.llm.chat(load_prompt(ctx.cfg, "quill"))
    post = ask_json(
        chat,
        f"Niche: {ctx.cfg.affiliate_niche}\nTopics already covered (pick a different one): "
        f"{json.dumps(ctx.state.affiliate_topics)}\nWrite one affiliate post as JSON.",
        _check_quill,
    )
    ctx.state.affiliate_topics.append(post["title"])
    path = _out(ctx, "quill", slugify(post["title"]) + ".md")
    path.write_text(f"<!-- DRAFT for review. Replace every [AFFILIATE LINK] with your own link. -->\n\n{post['markdown'].strip()}\n")
    links = post["markdown"].count(rules.AFFILIATE_PLACEHOLDER)
    return Result("quill", f"Wrote affiliate post **{post['title']}** ({links} link placeholders).", [path],
                  [f"Fact-check `{path.relative_to(ctx.cfg.root)}`, swap in your affiliate links, then publish it yourself."])


# ---------------------------------------------------------------- PITCH

def _check_pitch(data: Any) -> list[str]:
    if not isinstance(data, dict) or not isinstance(data.get("brands"), list) or not data["brands"]:
        return ['reply must be a JSON object with a non-empty "brands" list']
    errors = []
    for b in data["brands"]:
        name = b.get("name", "?")
        if not str(b.get("source_url", "")).startswith("http"):
            errors.append(f"brand {name!r} needs the source_url where you found it")
        if not b.get("dm") or not b.get("email_subject") or not b.get("email_body"):
            errors.append(f"brand {name!r} needs dm, email_subject, and email_body")
        if "@" in str(b.get("contact", "")) and not str(b.get("contact_source", "")).startswith("http"):
            errors.append(f"brand {name!r}: only list an email you found on a page, with contact_source set to that URL")
    return errors


def pitch(ctx: Context) -> Result:
    chat = ctx.llm.chat(load_prompt(ctx.cfg, "pitch"), web_search=True)
    data = ask_json(
        chat,
        f"UGC niche: {ctx.cfg.ugc_niche}\nBrands already pitched (skip them): {json.dumps(ctx.state.pitched_brands)}\n"
        "Find brands and write draft outreach as JSON.",
        _check_pitch,
    )
    files = []
    for b in data["brands"]:
        ctx.state.pitched_brands.append(b["name"])
        path = _out(ctx, "pitch", f"DRAFT-{slugify(b['name'])}.md")
        path.write_text(
            f"# DRAFT outreach: {b['name']}\n\n> **Not sent.** ORBIT never sends messages. Review, edit, and send it yourself if you want to.\n\n"
            f"- Website / where found: {b['source_url']}\n- Why they fit: {b.get('why', '')}\n"
            f"- Contact: {b.get('contact') or '[find contact on their site]'}"
            + (f" (from {b['contact_source']})" if b.get("contact_source") else "")
            + f"\n\n## DM\n\n{b['dm']}\n\n## Email\n\n**Subject:** {b['email_subject']}\n\n{b['email_body']}\n"
        )
        files.append(path)
    return Result("pitch", f"Drafted outreach for {len(files)} brands (not sent).", files,
                  [f"Review {len(files)} PITCH drafts in outputs/pitch/ and send the ones you like yourself."])


WORKERS: dict[str, Callable[[Context], Result]] = {
    "scout": scout,
    "forge": forge,
    "pixel": pixel,
    "quill": quill,
    "pitch": pitch,
}
