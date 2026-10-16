"""Optional chat model. The app works without one (deterministic composer)."""
from __future__ import annotations

import logging
from functools import lru_cache

from .config import settings

log = logging.getLogger(__name__)

DEFAULT_ANTHROPIC = "anthropic:claude-sonnet-5-5"
DEFAULT_OPENAI = "openai:gpt-5-mini"


def model_spec() -> str:
    s = settings()
    if s.llm_model:
        return s.llm_model
    if s.anthropic_api_key:
        return DEFAULT_ANTHROPIC
    if s.openai_api_key:
        return DEFAULT_OPENAI
    return ""


@lru_cache(maxsize=1)
def get_llm():
    """Return a LangChain chat model, or None when no key is configured."""
    spec = model_spec()
    if not spec:
        return None
    provider, _, name = spec.partition(":")
    s = settings()
    try:
        if provider == "anthropic" and s.anthropic_api_key:
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(model=name, api_key=s.anthropic_api_key, max_tokens=4096, timeout=90)
        if provider == "openai" and s.openai_api_key:
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(model=name, api_key=s.openai_api_key, timeout=90)
    except Exception as exc:  # a bad model config must not take the app down
        log.warning("LLM disabled: %s", exc)
    return None


def llm_label() -> str:
    return model_spec().partition(":")[2] if get_llm() is not None else "deterministic (no LLM key)"
