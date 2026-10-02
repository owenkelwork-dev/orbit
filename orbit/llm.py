"""Thin wrapper around the Anthropic SDK: multi-turn chats, web search, JSON replies, spend tracking."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import anthropic

# Server-side refusal fallback: if the main model declines, the API reruns the request on a
# recommended fallback model inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# USD per million tokens (input, output). Used only to estimate spend for the budget guard.
PRICES = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
WEB_SEARCH_USD = 0.01  # $10 per 1,000 searches
MAX_PAUSE_RESUMES = 5


class AgentError(RuntimeError):
    """An agent could not produce valid output."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    web_searches: int = 0
    usd: float = 0.0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.web_searches += other.web_searches
        self.usd += other.usd


def _usage_of(resp: Any, default_model: str) -> Usage:
    u = resp.usage
    cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
    server = getattr(u, "server_tool_use", None)
    searches = (getattr(server, "web_search_requests", 0) or 0) if server else 0
    price_in, price_out = PRICES.get(resp.model, PRICES.get(default_model, (5.0, 25.0)))
    usd = (
        u.input_tokens * price_in
        + cache_read * price_in * 0.1
        + cache_write * price_in * 1.25
        + u.output_tokens * price_out
    ) / 1_000_000 + searches * WEB_SEARCH_USD
    return Usage(u.input_tokens + cache_read + cache_write, u.output_tokens, searches, usd)


def _echo_content(blocks: list[dict]) -> list[dict]:
    """Content to send back as the assistant turn.

    After a mid-output fallback, blocks of the declined attempt that the next model can't use
    (thinking, tool calls, unpaired server tool calls) must be dropped from before the
    last `fallback` marker; everything else echoes unchanged.
    """
    last_fallback = max((i for i, b in enumerate(blocks) if b.get("type") == "fallback"), default=-1)
    if last_fallback < 0:
        return blocks
    result_ids = {b.get("tool_use_id") for b in blocks if b.get("type", "").endswith("_tool_result")}
    kept = []
    for i, b in enumerate(blocks):
        t = b.get("type")
        if i < last_fallback and (
            t in ("thinking", "redacted_thinking", "tool_use")
            or (t == "server_tool_use" and b.get("id") not in result_ids)
        ):
            continue
        kept.append(b)
    return kept


class Chat:
    """One multi-turn conversation with a fixed system prompt."""

    def __init__(self, llm: "Claude", system: str, web_search: bool):
        self.llm = llm
        self.system = system
        self.web_search = web_search
        self.messages: list[dict] = []

    def send(self, text: str) -> str:
        self.messages.append({"role": "user", "content": text})
        texts: list[str] = []
        for _ in range(MAX_PAUSE_RESUMES + 1):
            resp = self.llm._create(self.system, self.messages, self.web_search)
            blocks = [b.to_dict() for b in resp.content]
            if resp.stop_reason == "refusal":
                self.messages.pop()
                raise AgentError("The model declined this request (refusal).")
            self.messages.append({"role": "assistant", "content": _echo_content(blocks)})
            texts.extend(b["text"] for b in blocks if b.get("type") == "text")
            # A long web-search loop pauses; resending the conversation resumes it.
            if resp.stop_reason != "pause_turn":
                break
        if resp.stop_reason == "max_tokens":
            raise AgentError("Reply was cut off at max_tokens.")
        return "\n".join(texts).strip()


@dataclass
class Claude:
    model: str
    effort: str
    max_web_searches: int
    max_tokens: int = 32000
    usage: Usage = field(default_factory=Usage)
    client: Any = None

    def __post_init__(self) -> None:
        if self.client is None:
            self.client = anthropic.Anthropic()

    def chat(self, system: str, web_search: bool = False) -> Chat:
        return Chat(self, system, web_search)

    def _create(self, system: str, messages: list[dict], web_search: bool) -> Any:
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
            output_config={"effort": self.effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        if web_search:
            kwargs["tools"] = [
                {"type": "web_search_20260209", "name": "web_search", "max_uses": self.max_web_searches}
            ]
        with self.client.beta.messages.stream(**kwargs) as stream:
            resp = stream.get_final_message()
        self.usage.add(_usage_of(resp, self.model))
        return resp


def extract_json(text: str) -> Any:
    """Parse the last ```json fenced block in a reply, or the outermost {...} if there is none."""
    fenced = re.findall(r"```(?:json)?\s*\n(.*?)\n```", text, flags=re.DOTALL)
    candidates = list(reversed(fenced))
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise ValueError("no JSON object found in reply")


def ask_json(
    chat: Chat,
    prompt: str,
    validate: Callable[[Any], list[str]],
    retries: int = 2,
) -> Any:
    """Ask for a JSON reply and keep asking for fixes until `validate` returns no errors."""
    reply = chat.send(prompt)
    for attempt in range(retries + 1):
        try:
            data = extract_json(reply)
            errors = validate(data)
        except ValueError as e:
            data, errors = None, [f"Could not parse JSON: {e}"]
        if not errors:
            return data
        if attempt == retries:
            raise AgentError("Output still invalid after retries: " + "; ".join(errors))
        reply = chat.send(
            "Your JSON failed these checks. Fix every one and reply with the complete corrected "
            "JSON in a ```json block:\n- " + "\n- ".join(errors)
        )
    raise AssertionError("unreachable")
