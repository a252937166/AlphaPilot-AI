"""LLM integration primitives with schema validation and safe degradation."""

from alphapilot.llm.client import LLMUnavailable, chat_json

__all__ = ["LLMUnavailable", "chat_json"]
