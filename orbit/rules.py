"""Hard rules checked in code, not just asked for in prompts."""

from __future__ import annotations

import re
from typing import Any

ETSY_TITLE_MAX_WORDS = 14  # "under 15 words"
ETSY_TAG_COUNT = 13
ETSY_TAG_MAX_CHARS = 20
FIVERR_TITLE_MAX_CHARS = 80
FIVERR_TAG_MAX = 5
AFFILIATE_PLACEHOLDER = "[AFFILIATE LINK]"

# Brands, characters, and franchises that must never appear in products or listings.
# Not exhaustive: the prompts forbid all trademarks; this list catches common slips.
TRADEMARKS = [
    "disney", "pixar", "marvel", "star wars", "pokemon", "pokémon", "nintendo", "super mario", "zelda",
    "harry potter", "hogwarts", "barbie", "lego", "hello kitty", "sanrio", "taylor swift",
    "swiftie", "nfl", "nba", "mlb", "nhl", "fifa", "olympic", "nike", "adidas", "iphone",
    "ipad", "google", "excel", "microsoft", "canva", "notion", "goodnotes", "cricut", "stanley",
    "starbucks", "coca-cola", "coke", "mcdonald", "chanel", "gucci", "louis vuitton", "bluey",
    "peppa pig", "paw patrol", "minecraft", "fortnite", "roblox", "sesame street", "dr. seuss",
    "stranger things", "taylor's version", "dave ramsey",
]


def word_count(text: str) -> int:
    return len(text.split())


def find_trademarks(*texts: str) -> list[str]:
    blob = " ".join(texts).lower()
    return sorted({t for t in TRADEMARKS if re.search(rf"(?<![a-z]){re.escape(t)}(?![a-z])", blob)})


def _flatten(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_flatten(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(_flatten(v) for v in value)
    return str(value)


def check_trademarks(value: Any, where: str) -> list[str]:
    hits = find_trademarks(_flatten(value))
    if not hits:
        return []
    return [f"{where} mentions trademarked names {hits}; remove them and use generic wording"]


def check_etsy_listing(listing: dict) -> list[str]:
    errors: list[str] = []
    title = str(listing.get("title", "")).strip()
    if not title:
        errors.append("title is missing")
    elif word_count(title) > ETSY_TITLE_MAX_WORDS:
        errors.append(f"title has {word_count(title)} words; it must be under 15 (max {ETSY_TITLE_MAX_WORDS})")

    tags = listing.get("tags")
    if not isinstance(tags, list):
        errors.append("tags must be a list of strings")
    else:
        if len(tags) != ETSY_TAG_COUNT:
            errors.append(f"there are {len(tags)} tags; there must be exactly {ETSY_TAG_COUNT}")
        for tag in tags:
            if not isinstance(tag, str) or not tag.strip():
                errors.append(f"tag {tag!r} is empty or not a string")
            elif len(tag) > ETSY_TAG_MAX_CHARS:
                errors.append(f"tag {tag!r} is {len(tag)} characters; max is {ETSY_TAG_MAX_CHARS}")
        lowered = [t.strip().lower() for t in tags if isinstance(t, str)]
        if len(set(lowered)) != len(lowered):
            errors.append("tags must not repeat")

    if not str(listing.get("description", "")).strip():
        errors.append("description is missing")
    price = listing.get("price_usd")
    if not isinstance(price, (int, float)) or isinstance(price, bool) or price <= 0:
        errors.append("price_usd must be a positive number")
    photos = listing.get("photo_plan")
    if not isinstance(photos, list) or not photos:
        errors.append("photo_plan must be a non-empty list of shot descriptions")
    return errors


def check_fiverr_gig(gig: dict) -> list[str]:
    errors: list[str] = []
    title = str(gig.get("title", "")).strip()
    if not title.lower().startswith("i will"):
        errors.append('Fiverr title must start with "I will"')
    if len(title) > FIVERR_TITLE_MAX_CHARS:
        errors.append(f"Fiverr title is {len(title)} characters; max is {FIVERR_TITLE_MAX_CHARS}")
    tags = gig.get("tags")
    if not isinstance(tags, list) or not 1 <= len(tags) <= FIVERR_TAG_MAX:
        errors.append(f"tags must be a list of 1 to {FIVERR_TAG_MAX} strings")
    packages = gig.get("packages")
    if not isinstance(packages, list) or len(packages) != 3:
        errors.append("packages must list exactly 3 tiers (Basic, Standard, Premium)")
    if not isinstance(gig.get("thumbnail_concepts"), list) or not gig.get("thumbnail_concepts"):
        errors.append("thumbnail_concepts must be a non-empty list")
    return errors


def has_ftc_disclosure(markdown: str) -> bool:
    head = markdown[:1500].lower()
    return "affiliate" in head and ("commission" in head or "earn" in head)


def check_affiliate_post(post: dict) -> list[str]:
    errors: list[str] = []
    body = str(post.get("markdown", ""))
    if not body.strip():
        errors.append("markdown is missing")
        return errors
    if not has_ftc_disclosure(body):
        errors.append(
            "an FTC affiliate disclosure (says it contains affiliate links and you may earn a "
            "commission) must appear near the top, before the first link"
        )
    if AFFILIATE_PLACEHOLDER not in body:
        errors.append(f"use the literal placeholder {AFFILIATE_PLACEHOLDER} wherever a link goes")
    if re.search(r"https?://(?:www\.)?(amzn\.|amazon\.[a-z.]+/(?:dp|gp))", body):
        errors.append(f"do not include real affiliate/product URLs; use {AFFILIATE_PLACEHOLDER}")
    return errors


def check_sources(items: list[dict], key: str = "source_url") -> list[str]:
    """Any stated fact must carry a URL the agent actually found."""
    errors = []
    for item in items:
        url = str(item.get(key, ""))
        if not url.startswith("http"):
            errors.append(f"claim {str(item.get('claim', item))[:60]!r} has no source URL; cite one or drop it")
    return errors
