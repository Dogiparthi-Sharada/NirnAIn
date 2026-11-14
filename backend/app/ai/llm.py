"""Thin wrapper around the Anthropic SDK.

Purpose: Give every AI feature one way to call Claude (structured or plain chat) with token usage,
and a clear "not configured" state so features fall back instead of failing.
How it fits: The SDK's sync client blocks, so callers run these methods with `asyncio.to_thread`;
tests monkeypatch `LLM.parse` / `LLM.text`; nothing is called without an API key; layer: ai.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import anthropic
from anthropic.types import MessageParam
from pydantic import BaseModel

from app.core.config import Settings


class LLMNotConfiguredError(RuntimeError):
    """No ANTHROPIC_API_KEY: AI calls are unavailable."""


@dataclass(frozen=True, slots=True)
class Usage:
    """Tokens a call used, as Anthropic reports them."""

    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """One turn of a chat conversation."""

    role: Literal["user", "assistant"]
    content: str


def make_client(settings: Settings) -> anthropic.Anthropic | None:
    """Return an Anthropic client for the configured key, or None without one."""
    if settings.anthropic_api_key is None:
        return None
    return anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value())


class LLM:
    """A Claude model behind an optional client."""

    def __init__(self, client: anthropic.Anthropic | None, model: str) -> None:
        """Keep the client (None: not configured) and the model id to call."""
        self._client = client
        self.model = model

    @property
    def configured(self) -> bool:
        """Tell whether an API key is configured."""
        return self._client is not None

    def _require(self) -> anthropic.Anthropic:
        """Return the client; raises `LLMNotConfiguredError` without one."""
        if self._client is None:
            raise LLMNotConfiguredError
        return self._client

    def parse[ModelT: BaseModel](
        self,
        *,
        system: str,
        prompt: str,
        output: type[ModelT],
        max_tokens: int = 16000,
        timeout_s: float | None = None,
    ) -> tuple[ModelT, Usage]:
        """One structured call: returns the validated `output` model and token usage."""
        response = self._require().messages.parse(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_format=output,
            timeout=anthropic.NOT_GIVEN if timeout_s is None else timeout_s,
        )
        parsed = response.parsed_output
        if parsed is None:  # refusal or truncated output
            raise ValueError("the model returned no structured output")
        return parsed, Usage(response.usage.input_tokens, response.usage.output_tokens)

    def text(
        self,
        *,
        system: str,
        messages: list[ChatMessage],
        max_tokens: int = 1500,
        timeout_s: float | None = None,
    ) -> tuple[str, Usage]:
        """A plain chat completion: returns the concatenated text blocks and token usage."""
        params: list[MessageParam] = [
            {"role": "user", "content": message.content}
            if message.role == "user"
            else {"role": "assistant", "content": message.content}
            for message in messages
        ]
        response = self._require().messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=params,
            timeout=anthropic.NOT_GIVEN if timeout_s is None else timeout_s,
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        return text.strip(), Usage(response.usage.input_tokens, response.usage.output_tokens)
