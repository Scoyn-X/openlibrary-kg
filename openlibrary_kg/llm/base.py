"""Abstract LLM client interface."""

from __future__ import annotations

from abc import ABC, abstractmethod


class LLMClient(ABC):
    """Abstract interface for LLM API calls."""

    @abstractmethod
    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Generate a response from the LLM.

        Args:
            system_prompt: System-level instruction.
            user_prompt: User-level input.

        Returns:
            The generated text response.
        """
        ...

    @abstractmethod
    async def generate_batch(
        self,
        prompts: list[tuple[str, str]],
    ) -> list[str]:
        """Generate responses for multiple prompts concurrently.

        Args:
            prompts: List of (system_prompt, user_prompt) pairs.

        Returns:
            List of generated responses, in the same order as prompts.
        """
        ...
