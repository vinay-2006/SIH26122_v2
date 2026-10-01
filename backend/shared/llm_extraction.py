"""
M2's extraction engine. Takes raw claim text (any language, any messiness)
and returns validated ExtractedClaimFields. Used by every intake path (text,
voice, file, scanned-diary/image) — build and test this once, in isolation,
before wiring it into any endpoint.

PRD v5 change: default provider is Groq's free tier (OpenAI-compatible
endpoint), not paid OpenAI. Gemini free tier is the documented fallback,
also reachable via an OpenAI-compatible endpoint, so both providers use the
exact same `openai` SDK client code — only base_url/api_key/model differ.
Switch providers with the LLM_PROVIDER env var, no code change needed.

Two extraction shapes are exposed:
  - extract_claim_fields(text) -> ExtractedClaimFields
        Single-claim contract. Used by typed-text/voice intake, where one
        submission is always exactly one claim. Unchanged from the original
        M2 contract/prompt.
  - extract_claim_fields_batch(text) -> list[ExtractedClaimFields]
        Multi-claim contract. Used by file intake (PDF/TXT/OCR'd images),
        where a single uploaded document commonly reports progress on
        several distinct schedule activities at once (e.g. a daily report
        with one row per discipline) and collapsing it into a single claim
        would silently discard every activity but one.
  - extract_claim_fields_from_image_batch(image_bytes, mime_type) -> list[...]
        Vision variant of the batch contract: sends the image directly to a
        vision-capable model instead of OCR'd text, per the same JSON
        contract.

Error-handling contract (all three functions): a genuine extraction failure
(missing/invalid API key, network error, malformed or empty model output)
raises LLMExtractionError — it is never silently swallowed into an all-null
result. An all-null/sparse result is only ever returned when the model
itself completed successfully and genuinely found nothing to extract; that
is a legitimate "insufficient information" outcome, not a failure, and the
caller creates the claim as EXTRACTED same as always so a human reviewer can
see it. Callers that want the old best-effort/never-raise behavior should
catch LLMExtractionError explicitly and decide what to do (e.g. surface a
502 rather than fabricate a claim — see routers/intake.py).
"""
import base64
import json
import logging
import os
from typing import Optional

from openai import OpenAI

from backend.shared import llm_client
from backend.shared.llm_client import strip_code_fences
from backend.shared.rule_extraction import extract_with_rules, extract_with_rules_batch, fallback_enabled
from backend.shared.schemas import ExtractedClaimFields

logger = logging.getLogger(__name__)

# Vision-capable model per provider. Gemini Flash is natively
# multimodal, so it reuses the same default as text extraction. Groq's
# vision-capable model lineup changes over time and isn't guaranteed present
# on every account/key (verified against this project's live key: none of
# the currently available Groq models advertise vision support) -- rather
# than hardcode a model name that may 404 or silently ignore the image, this
# is deliberately left unset for Groq so a missing/wrong model surfaces as a
# clear, actionable LLMExtractionError instead of a guessed default that
# quietly does the wrong thing. Set LLM_VISION_MODEL to override/enable it.
_PROVIDER_DEFAULT_VISION_MODELS = {
    "gemini": "gemini-3.6-flash",
}


class LLMExtractionError(Exception):
    """
    A genuine extraction failure: missing/invalid configuration, a network
    or provider-side error, or a model response that could not be parsed
    into the expected JSON contract. Distinct from a successful call that
    legitimately found nothing to extract (which is not an error).
    """


def _load_env_if_needed() -> None:
    llm_client._load_env_if_needed()


def _get_client() -> OpenAI:
    """The shared llm_client's provider client (single Groq/Gemini implementation)."""
    try:
        return llm_client.get_client()
    except llm_client.LLMClientError as e:
        raise LLMExtractionError(str(e)) from e


def _get_model() -> str:
    return llm_client.get_default_model()


def _get_vision_model() -> str:
    _load_env_if_needed()
    provider = llm_client.get_llm_provider()
    model = os.environ.get("LLM_VISION_MODEL") or _PROVIDER_DEFAULT_VISION_MODELS.get(provider)
    if not model:
        raise LLMExtractionError(
            f"No vision-capable model configured for LLM_PROVIDER='{provider}'. "
            "Set LLM_VISION_MODEL to a vision-capable model id for this provider "
            "(e.g. switch LLM_PROVIDER=gemini, which is natively multimodal, or "
            "set LLM_VISION_MODEL once your Groq account has a vision model available)."
        )
    return model


def _create_completion(client: OpenAI, **kwargs):
    """Send a completion through the shared client's retrying sender."""
    try:
        return llm_client.create_completion(client, **kwargs)
    except llm_client.LLMClientError as e:
        raise LLMExtractionError(str(e)) from e


SYSTEM_PROMPT = """You are a field-report extraction engine for a construction \
project tracking system. You will receive raw text from a site report — it may \
be in English, Hindi, or a mix of both, and may be informal or messy.

Your job: read and understand the text (in whatever language it's in), then \
output ONLY a single JSON object with these fields, with all string values in \
English/normalized form:

{
  "event_date": "YYYY-MM-DD or null",
  "reported_activity_id": "string or null — ONLY populate if the text explicitly \
names or strongly implies a specific schedule activity ID (e.g. 'Activity A-1042 \
started'). If no ID is mentioned, leave this null — do not guess.",
  "discipline": "one of CIVIL, PIPING, STATIC_ROTATING_EQUIPMENT, ELECTRICAL, \
INSTRUMENTATION, HSE, or null",
  "action": "short string describing what was done",
  "event_type": "one of ACTUAL_START, ACTUAL_FINISH, PROGRESS_UPDATE, DELAY, \
BLOCKER",
  "claim_mode": "CUMULATIVE_PCT if this reports the activity's total completion \
so far, or INCREMENTAL_QUANTITY if this reports one partial/countable \
contribution (e.g. 'welded joint 3 of 10'). Default to CUMULATIVE_PCT unless \
the text clearly describes a partial/countable contribution.",
  "asset_tag": "string or null",
  "location": "string or null",
  "claimed_quantity": "number or null — only if claim_mode is INCREMENTAL_QUANTITY",
  "claimed_uom": "string or null — only if claim_mode is INCREMENTAL_QUANTITY",
  "claimed_pct": "number 0-100 or null — only if claim_mode is CUMULATIVE_PCT",
  "delay_reason": "one of MATERIAL, EQUIPMENT, LABOUR, ACCESS, WEATHER, REWORK, \
OTHER, or null — only if event_type is DELAY or BLOCKER",
  "language_detected": "the language of the original input, e.g. 'English', \
'Hindi', 'Hindi-English mixed'"
}

Rules:
- Output ONLY the JSON object. No preamble, no markdown fences, no explanation.
- If a field cannot be determined from the text, use null (except claim_mode,
  which defaults to CUMULATIVE_PCT).
- claimed_pct and claimed_quantity/claimed_uom are mutually exclusive based on
  claim_mode — never populate both.
- event_date: if the text doesn't state a date, use null (the caller will fall
  back to today's date).
"""

BATCH_SYSTEM_PROMPT = """You are a field-report extraction engine for a construction \
project tracking system. You will receive raw text from a site report — it may \
be in English, Hindi, or a mix of both, may be informal or messy, and may be a \
flattened dump of a table or multiple report sections.

IMPORTANT: the text may describe progress on ONE activity, or on SEVERAL \
distinct schedule activities at once (e.g. a daily report with one row/section \
per discipline or activity, such as "Civil: ... Piping: ... Electrical: ..."). \
You must identify EVERY distinct activity/claim described and return one JSON \
object per activity — do not merge multiple activities into a single object, \
and do not invent activities that aren't actually described.

Output ONLY a single JSON object of the form:
{"claims": [ <claim object>, <claim object>, ... ]}

If the text describes exactly one activity, return an array with exactly one \
object. If the text contains no extractable claim at all, return {"claims": []}.

Each <claim object> has exactly these fields, with all string values in \
English/normalized form:

{
  "event_date": "YYYY-MM-DD or null",
  "reported_activity_id": "string or null — ONLY populate if the text explicitly \
names or strongly implies a specific schedule activity ID (e.g. 'Activity A-1042 \
started' or a table row's Activity ID column). If no ID is mentioned, leave this \
null — do not guess.",
  "discipline": "one of CIVIL, PIPING, STATIC_ROTATING_EQUIPMENT, ELECTRICAL, \
INSTRUMENTATION, HSE, or null",
  "action": "short string describing what was done for THIS activity",
  "event_type": "one of ACTUAL_START, ACTUAL_FINISH, PROGRESS_UPDATE, DELAY, \
BLOCKER",
  "claim_mode": "CUMULATIVE_PCT if this reports the activity's total completion \
so far, or INCREMENTAL_QUANTITY if this reports one partial/countable \
contribution (e.g. 'welded joint 3 of 10' or a quantity/unit column). Default \
to CUMULATIVE_PCT unless the text clearly describes a partial/countable \
contribution.",
  "asset_tag": "string or null",
  "location": "string or null",
  "claimed_quantity": "number or null — only if claim_mode is INCREMENTAL_QUANTITY",
  "claimed_uom": "string or null — only if claim_mode is INCREMENTAL_QUANTITY",
  "claimed_pct": "number 0-100 or null — only if claim_mode is CUMULATIVE_PCT",
  "delay_reason": "one of MATERIAL, EQUIPMENT, LABOUR, ACCESS, WEATHER, REWORK, \
OTHER, or null — only if event_type is DELAY or BLOCKER",
  "language_detected": "the language of the original input, e.g. 'English', \
'Hindi', 'Hindi-English mixed'"
}

Rules:
- Output ONLY the {"claims": [...]} JSON object. No preamble, no markdown \
fences, no explanation.
- If a field cannot be determined for a given activity, use null (except \
claim_mode, which defaults to CUMULATIVE_PCT).
- claimed_pct and claimed_quantity/claimed_uom are mutually exclusive based on \
claim_mode — never populate both.
- event_date: if the text doesn't state a date for an activity, use null (the \
caller will fall back to today's date).
"""

VISION_SYSTEM_PROMPT = BATCH_SYSTEM_PROMPT + """
The report is provided as an IMAGE (e.g. a photographed or scanned site diary \
page), not as text. Read the handwritten or printed content directly from the \
image and extract claims from it using the same rules above.
"""


def _parse_batch_response(raw: Optional[str]) -> list[ExtractedClaimFields]:
    if not raw or not raw.strip():
        raise LLMExtractionError("LLM returned an empty response")
    try:
        data = json.loads(strip_code_fences(raw))
    except json.JSONDecodeError as e:
        raise LLMExtractionError(f"LLM response was not valid JSON: {e}") from e

    claims_raw = data.get("claims") if isinstance(data, dict) else None
    if claims_raw is None:
        # Tolerate a bare object (single claim, no "claims" wrapper) as a
        # one-item batch -- some models omit the wrapper for a single result
        # despite the prompt, and treating it as a 1-claim batch is strictly
        # more useful than raising.
        if isinstance(data, dict) and data:
            claims_raw = [data]
        else:
            raise LLMExtractionError(
                "LLM response JSON did not contain a 'claims' array"
            )
    if not isinstance(claims_raw, list):
        raise LLMExtractionError("LLM response 'claims' field was not a JSON array")

    results: list[ExtractedClaimFields] = []
    for item in claims_raw:
        if not isinstance(item, dict):
            continue
        try:
            results.append(ExtractedClaimFields(**item))
        except Exception as e:
            raise LLMExtractionError(f"LLM returned a claim with an invalid shape: {e}") from e
    return results


def extract_claim_fields(raw_text: str) -> ExtractedClaimFields:
    """
    Single-claim extraction — the original M2 contract, used by typed-text
    and voice intake where one submission is always exactly one claim.

    Raises LLMExtractionError on a genuine failure (bad/missing config,
    network error, malformed model output). Does NOT raise, and does NOT
    return an all-null object, just because the model determined there's
    nothing to extract from valid input -- that's ExtractedClaimFields()'s
    normal all-default state, not an error.
    """
    try:
        client = _get_client()
        response = _create_completion(
            client,
            model=_get_model(),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": raw_text},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
    except Exception as e:
        err = e if isinstance(e, LLMExtractionError) else LLMExtractionError(f"LLM request failed: {e}")
        if fallback_enabled():
            logger.warning("LLM extraction unavailable (%s); using rule-based fallback", err)
            return extract_with_rules(raw_text)
        if err is e:
            raise
        raise err from e

    raw = response.choices[0].message.content
    if not raw or not raw.strip():
        raise LLMExtractionError("LLM returned an empty response")
    try:
        data = json.loads(strip_code_fences(raw))
    except json.JSONDecodeError as e:
        raise LLMExtractionError(f"LLM response was not valid JSON: {e}") from e
    try:
        return ExtractedClaimFields(**data)
    except Exception as e:
        raise LLMExtractionError(f"LLM returned a claim with an invalid shape: {e}") from e


def extract_claim_fields_batch(raw_text: str) -> list[ExtractedClaimFields]:
    """
    Multi-claim extraction for file intake. A single uploaded document
    (daily report PDF, discipline spreadsheet dump, OCR'd diary, ...)
    commonly reports progress on several distinct activities; this returns
    one ExtractedClaimFields per activity actually described, rather than
    collapsing them into one and silently discarding the rest.

    Raises LLMExtractionError on a genuine failure. Returns an empty list
    only when the model ran successfully and found no extractable claim in
    the text (e.g. a blank or irrelevant document) -- the caller decides how
    to treat "valid file, no claims" vs. "extraction failed".
    """
    try:
        client = _get_client()
        response = _create_completion(
            client,
            model=_get_model(),
            messages=[
                {"role": "system", "content": BATCH_SYSTEM_PROMPT},
                {"role": "user", "content": raw_text},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
    except Exception as e:
        err = e if isinstance(e, LLMExtractionError) else LLMExtractionError(f"LLM request failed: {e}")
        if fallback_enabled():
            logger.warning("LLM batch extraction unavailable (%s); using rule-based fallback", err)
            return extract_with_rules_batch(raw_text)  # one claim per reported item, not one per document
        if err is e:
            raise
        raise err from e

    return _parse_batch_response(response.choices[0].message.content)


def extract_claim_fields_from_image_batch(
    image_bytes: bytes, mime_type: str = "image/png"
) -> list[ExtractedClaimFields]:
    """
    Vision variant of extract_claim_fields_batch: sends the image directly
    to a vision-capable model (see _get_vision_model) instead of routing
    through OCR text. Same JSON contract and error semantics as the text
    batch function above.

    Raises LLMExtractionError if no vision-capable model is configured for
    the active provider, or on any other genuine extraction failure -- never
    silently falls back to a null/empty result.
    """
    vision_model = _get_vision_model()
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime_type};base64,{b64}"

    try:
        client = _get_client()
        response = _create_completion(
            client,
            model=vision_model,
            messages=[
                {"role": "system", "content": VISION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Extract every claim described in this site report image.",
                        },
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
    except LLMExtractionError:
        raise
    return _parse_batch_response(response.choices[0].message.content)


def check_missing_required_fields(extracted: ExtractedClaimFields) -> list[str]:
    """
    Feature #29 Adaptive Field Copilot:
    Strictly checks for missing-but-required fields:
    - event_type
    - discipline
    - at least one of claimed_pct / claimed_quantity

    asset_tag, location, and delay_reason being null is normal and does NOT trigger follow-up.
    """
    missing = []
    if not extracted.event_type:
        missing.append("event_type")
    if not extracted.discipline:
        missing.append("discipline")
    if extracted.claimed_pct is None and extracted.claimed_quantity is None:
        missing.append("claimed_progress")
    return missing


def generate_clarification_question(
    raw_text: str,
    missing_fields: list[str],
    language_detected: Optional[str] = None,
) -> str:
    """
    Prompts the LLM to generate a single, specific follow-up question in the same
    language the claim was submitted in.
    """
    from backend.shared.llm_client import call_llm

    lang = language_detected or "English"
    fields_desc = ", ".join(missing_fields)
    prompt = (
        f"You are a helpful construction site supervisor copilot. A field engineer reported:\n"
        f"\"{raw_text}\"\n\n"
        f"The following required information is missing from the report: {fields_desc}.\n"
        f"Generate a single, polite, concise follow-up question asking the engineer to clarify these missing details.\n"
        f"IMPORTANT: Phrase the question in the same language as the report (Detected language: {lang}).\n"
        f"Ask ONLY ONE question. Do not include greetings, explanations, or multiple options."
    )

    try:
        question = call_llm(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        question = question.strip().strip('"')
        if not question or question == "{}":
            return f"Could you please specify the {fields_desc.replace('_', ' ')} for this activity?"
        return question
    except Exception:
        return f"Could you please clarify the {fields_desc.replace('_', ' ')} for this activity?"
