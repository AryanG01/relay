"""
Anthropic async client wrapper.

All LLM calls in Relay go through call_llm(). Never instantiate
AsyncAnthropic directly in a service.

Key behaviours:
  - Exponential backoff retry on 429 / transient errors (max retry_count attempts)
  - If expect_json=True: strips markdown fences, validates JSON, retries if invalid
  - Logs every call with prompt hash + token usage (never logs raw prompt text)
"""
from __future__ import annotations

import asyncio
import json
import time
from functools import lru_cache

import anthropic

from backend.utils.hashing import short_hash
from backend.utils.logging import get_logger

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def _get_client() -> anthropic.AsyncAnthropic:
    from backend.config import get_settings
    return anthropic.AsyncAnthropic(api_key=get_settings().anthropic_api_key)


def _strip_fences(text: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` wrappers LLMs sometimes emit."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]  # drop opening fence line (```json or ```)
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


async def call_llm(
    prompt: str,
    system: str | None = None,
    max_tokens: int = 1500,
    expect_json: bool = False,
    retry_count: int = 3,
) -> str:
    """
    Call Claude and return the text response.

    Args:
        prompt: User message content.
        system: Optional system prompt.
        max_tokens: Maximum tokens in the response.
        expect_json: If True, strip fences and validate JSON; retry on invalid.
        retry_count: How many attempts before raising.

    Returns:
        Raw text (or cleaned JSON string if expect_json=True).

    Raises:
        anthropic.RateLimitError: After retry_count exhausted.
        json.JSONDecodeError: If expect_json=True and JSON never valid after retries.
    """
    from backend.config import get_settings
    settings = get_settings()
    prompt_hash = short_hash(prompt)

    for attempt in range(retry_count):
        try:
            t0 = time.monotonic()
            kwargs: dict = {
                "model": settings.llm_model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system:
                kwargs["system"] = system

            response = await _get_client().messages.create(**kwargs)
            latency = round(time.monotonic() - t0, 3)
            content = response.content[0].text

            logger.info(
                "llm_call_ok",
                extra={
                    "prompt_hash": prompt_hash,
                    "in_tokens": response.usage.input_tokens,
                    "out_tokens": response.usage.output_tokens,
                    "latency_s": latency,
                    "attempt": attempt + 1,
                },
            )

            if expect_json:
                content = _strip_fences(content)
                try:
                    json.loads(content)  # validate only — return string
                except json.JSONDecodeError:
                    if attempt < retry_count - 1:
                        logger.warning(
                            "llm_invalid_json",
                            extra={"prompt_hash": prompt_hash, "attempt": attempt + 1},
                        )
                        continue
                    raise

            return content

        except anthropic.RateLimitError:
            if attempt < retry_count - 1:
                wait = 2 ** attempt
                logger.warning(
                    "llm_rate_limited",
                    extra={"wait_s": wait, "attempt": attempt + 1},
                )
                await asyncio.sleep(wait)
            else:
                raise

        except anthropic.APIError:
            if attempt < retry_count - 1:
                await asyncio.sleep(2 ** attempt)
            else:
                raise

    raise RuntimeError("call_llm exhausted retries without returning or raising")
