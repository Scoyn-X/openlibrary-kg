"""OpenAI-compatible LLM client."""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

from openlibrary_kg.llm.base import LLMClient
from openlibrary_kg.llm.rate_limiter import RateLimiter

logger = logging.getLogger("openlibrary_kg.llm")

RETRYABLE_STATUS = {429, 500, 502, 503}


class OpenAIClient(LLMClient):
    """LLM client for OpenAI-compatible APIs.

    Works with OpenAI, Azure OpenAI, local vLLM, Ollama, etc.
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        api_base: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 150,
        max_retries: int = 3,
        rate_limiter: RateLimiter | None = None,
    ):
        self.model = model
        api_key = api_key or os.environ.get(api_key_env, "")
        api_base = api_base or os.environ.get("OPENAI_API_BASE", None)
        if not api_base:
            api_base = "https://api.openai.com/v1"

        self._client = httpx.AsyncClient(
            base_url=api_base.rstrip("/"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(60.0),
        )
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.rate_limiter = rate_limiter or RateLimiter(rate=10, capacity=10)

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Single synchronous-style call (async underneath)."""
        results = await self.generate_batch([(system_prompt, user_prompt)])
        return results[0]

    async def generate_batch(
        self,
        prompts: list[tuple[str, str]],
    ) -> list[str]:
        """Send multiple prompts concurrently with rate limiting."""
        sem = asyncio.Semaphore(
            self.rate_limiter.capacity
        )

        async def _one(idx: int, sys_p: str, usr_p: str) -> tuple[int, str]:
            async with sem:
                for attempt in range(self.max_retries):
                    await self.rate_limiter.acquire()
                    try:
                        resp = await self._client.post(
                            "/chat/completions",
                            json={
                                "model": self.model,
                                "messages": [
                                    {"role": "system", "content": sys_p},
                                    {"role": "user", "content": usr_p},
                                ],
                                "temperature": self.temperature,
                                "max_tokens": self.max_tokens,
                            },
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            msg = data["choices"][0]["message"]
                            content = msg.get("content", "") or ""
                            # Deepseek reasoning models return content in reasoning_content
                            if not content.strip():
                                content = msg.get("reasoning_content", "") or ""
                            return (idx, content.strip())
                        elif resp.status_code in RETRYABLE_STATUS:
                            delay = 2 ** attempt
                            logger.warning(
                                "HTTP %d from LLM API, retrying in %ds (attempt %d/%d)",
                                resp.status_code, delay, attempt + 1, self.max_retries,
                            )
                            await asyncio.sleep(delay)
                        elif resp.status_code == 401:
                            logger.error("LLM API authentication failed (401)")
                            return (idx, "")
                        else:
                            logger.error(
                                "LLM API error %d: %s", resp.status_code, resp.text[:200]
                            )
                            return (idx, "")
                    except Exception as exc:
                        logger.error("LLM API exception: %s", exc)
                        if attempt < self.max_retries - 1:
                            await asyncio.sleep(2 ** attempt)
                        else:
                            return (idx, "")
                return (idx, "")

        tasks = [_one(i, sys, usr) for i, (sys, usr) in enumerate(prompts)]
        results_list = await asyncio.gather(*tasks)
        # Sort by index to preserve order
        results_list.sort(key=lambda x: x[0])
        return [r[1] for r in results_list]

    async def close(self) -> None:
        await self._client.aclose()
