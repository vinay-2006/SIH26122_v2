"""
Centralized shared LLM client for Member 2 and downstream members (M6).
Implements team Hard Restriction #16:
"Nobody makes a Groq/Gemini call outside shared/llm_client.py. If a feature
needs an LLM call, it imports the shared function."

Supports:
- Groq (primary, free tier)
- Gemini (fallback, free tier via OpenAI-compatible endpoint)
Controlled by LLM_PROVIDER, LLM_API_KEY, and LLM_MODEL env vars.
"""
import logging
import os
import pathlib
import re
import time
from typing import Optional

import httpx
import openai
from dotenv import load_dotenv
from openai import OpenAI

logger = logging.getLogger(__name__)

_client: Optional[OpenAI] = None


class LLMClientError(RuntimeError):
    """The shared client is misconfigured or the provider call failed for good."""


_CODE_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?```\s*$", re.DOTALL)


def strip_code_fences(text: str) -> str:
    """
    Some providers wrap JSON-mode output in a ```json fence even when a
    json_object response_format is requested. Callers that asked for JSON must
    get parseable JSON, so unwrap a single surrounding fence.
    """
    if not text:
        return text
    m = _CODE_FENCE_RE.match(text)
    return m.group(1).strip() if m else text.strip()

_PROVIDER_BASE_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
}

_PROVIDER_DEFAULT_MODELS = {
    "groq": "openai/gpt-oss-20b",
    "gemini": "gemini-3.6-flash",
}

_RETRYABLE_ERRORS = (
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
)
_MAX_RETRIES = 3
_RETRY_BACKOFF_SECONDS = 4.0


def _load_env_if_needed() -> None:
    if "LLM_API_KEY" not in os.environ:
        env_path = pathlib.Path(__file__).resolve().parents[2] / ".env"
        load_dotenv(dotenv_path=env_path, override=False)
        provider = os.environ.get("LLM_PROVIDER", "groq").lower()
        if not os.environ.get("LLM_API_KEY"):
            if provider == "groq" and os.environ.get("GROQ_API_KEY"):
                os.environ["LLM_API_KEY"] = os.environ["GROQ_API_KEY"]
            elif provider == "gemini" and os.environ.get("GEMINI_API_KEY"):
                os.environ["LLM_API_KEY"] = os.environ["GEMINI_API_KEY"]


def get_llm_provider() -> str:
    _load_env_if_needed()
    return os.environ.get("LLM_PROVIDER", "groq").lower()


def get_default_model(provider: Optional[str] = None) -> str:
    _load_env_if_needed()
    prov = (provider or get_llm_provider()).lower()
    return os.environ.get("LLM_MODEL", _PROVIDER_DEFAULT_MODELS.get(prov, _PROVIDER_DEFAULT_MODELS["groq"]))


def _get_client() -> Optional[OpenAI]:
    global _client
    _load_env_if_needed()
    provider = get_llm_provider()
    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        return None

    if _client is None:
        base_url = _PROVIDER_BASE_URLS.get(provider)
        if base_url is None:
            raise ValueError(f"Unknown LLM_PROVIDER '{provider}' — expected 'groq' or 'gemini'")
        http_client = httpx.Client(timeout=30.0)
        _client = OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)
    return _client


def get_client() -> OpenAI:
    """Strict accessor: the configured client, or LLMClientError if unusable."""
    _load_env_if_needed()
    provider = get_llm_provider()
    if provider not in _PROVIDER_BASE_URLS:
        raise LLMClientError(f"Unknown LLM_PROVIDER '{provider}' — expected 'groq' or 'gemini'")
    client = _get_client()
    if client is None:
        raise LLMClientError(
            "LLM_API_KEY is not set. Add it to .env or export it before starting the server."
        )
    return client


def reset_client() -> None:
    global _client
    _client = None


def create_completion(client: OpenAI, **kwargs):
    """
    The one place a chat-completion request is sent (with backoff on transient
    failures). Raises LLMClientError once retries are exhausted or on a
    non-retryable provider error.
    """
    last_error: Optional[Exception] = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return client.chat.completions.create(**kwargs)
        except _RETRYABLE_ERRORS as e:
            last_error = e
            if isinstance(e, openai.RateLimitError) and "per day" in str(e).lower():
                # A daily quota (RPD/TPD) will not clear within a retry window; fail fast so
                # callers can use their deterministic fallback instead of stalling ~25s per call.
                raise LLMClientError(f"LLM daily quota exhausted: {e}") from e
            if attempt < _MAX_RETRIES:
                logger.warning(
                    "LLM request failed (%s), retrying (%d/%d)...", e, attempt + 1, _MAX_RETRIES
                )
                time.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
        except Exception as e:  # non-retryable: bad request, auth, model-not-found
            raise LLMClientError(f"LLM request failed: {e}") from e
    raise LLMClientError(
        f"LLM request failed after {_MAX_RETRIES + 1} attempts: {last_error}"
    ) from last_error


def call_llm(
    messages: list[dict],
    temperature: float = 0.0,
    response_format: Optional[dict] = None,
    model: Optional[str] = None,
) -> str:
    """
    Central function to make an LLM chat completion call.
    Returns the string content of the response message.

    Includes retry backoff for rate limits, connection errors, and transient 5xx issues.
    If LLM_API_KEY is not configured or all retries fail in test mode, returns
    a graceful fallback string rather than crashing.
    """
    client = _get_client()
    if client is None:
        logger.warning("[llm_client] LLM_API_KEY not configured, returning mock fallback response")
        if response_format and response_format.get("type") == "json_object":
            return "{}"
        return "Could you please specify the activity discipline, event type, and progress percentage for this claim?"

    target_model = model or get_default_model()
    kwargs = {
        "model": target_model,
        "messages": messages,
        "temperature": temperature,
    }
    if response_format:
        kwargs["response_format"] = response_format

    is_json = bool(response_format and response_format.get("type") == "json_object")
    try:
        response = create_completion(client, **kwargs)
    except LLMClientError as e:
        logger.error(f"[llm_client] LLM call failed: {e}")
        if is_json:
            return "{}"
        return "Could you please clarify the event type, discipline, and progress for this claim?"

    content = response.choices[0].message.content or ""
    return strip_code_fences(content) if is_json else content
