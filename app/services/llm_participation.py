"""LLM-based participation scoring using Google Gemini.

This module is intentionally free of any database or ORM coupling. Callers build
a :class:`GroupScoringInput` from their own data and receive a
:class:`GroupScoringOutput` back. The LLM reasons about each member's
contribution *relative to the group* and against the assignment description,
returning a 0..1 score plus qualitative reasoning for the instructor.

Scoring is deliberately deterministic-leaning (temperature 0, pinned model) and
the score is meant to be generated once and persisted, not recomputed on read.
"""
from __future__ import annotations

import asyncio
import json
import string
from dataclasses import dataclass, field
from functools import lru_cache

from pydantic import BaseModel, Field

from app.core.config import get_settings


class LLMScoringUnavailableError(Exception):
    """Raised when LLM scoring is disabled or not configured (missing API key)."""


class LLMScoringError(Exception):
    """Raised when the LLM call fails or returns an unusable response."""


# ---------------------------------------------------------------------------
# Input / output data structures (framework-agnostic)
# ---------------------------------------------------------------------------
@dataclass
class MemberScoringInput:
    ref: str  # anonymized label, e.g. "Member A"
    features: dict[str, float]
    raw_github: dict[str, int] | None
    raw_google_docs: dict[str, int] | None
    meeting: dict[str, float] | None
    github_connected: bool
    google_connected: bool
    google_email_matched: bool | None
    account_status: str | None


@dataclass
class GroupScoringInput:
    assignment_description: str
    member_count: int
    group_totals: dict[str, float]
    members: list[MemberScoringInput]


@dataclass
class MemberScoringOutput:
    ref: str
    score: float
    top_area: str | None
    reasoning: str
    flags: list[str] = field(default_factory=list)
    confidence: float = 0.5


@dataclass
class GroupScoringOutput:
    members: list[MemberScoringOutput]
    group_observations: str
    model_version: str


# ---------------------------------------------------------------------------
# Structured-output schema the model must conform to
# ---------------------------------------------------------------------------
class _MemberResult(BaseModel):
    # Bounds are intentionally NOT enforced here: an out-of-range value from the
    # model should be clamped in _to_output, not fail the whole group.
    ref: str
    score: float = 0.0
    top_area: str | None = None
    reasoning: str = ""
    flags: list[str] = Field(default_factory=list)
    confidence: float = 0.5


class _GroupResult(BaseModel):
    members: list[_MemberResult]
    group_observations: str = ""


_SYSTEM_INSTRUCTION = """\
You are a deterministic scoring engine that helps a human instructor understand \
how much each student contributed to a group assignment, using activity data \
aggregated from GitHub, Google Docs, and meeting platforms.

You must follow these rules strictly:

1. Score ONLY observable, measured contribution on the tracked platforms. Assign \
each member a score between 0 and 1, where 1 means an outstanding relative \
contribution to this group and 0 means no measurable contribution.
2. Judge contribution ABSOLUTELY against what the assignment appears to require \
and the group's activity, NOT on a forced curve. It is valid for every member of \
a balanced team to score similarly high, or for a whole team to score low.
3. IGNORE any feature whose group total is 0 (the group did not use that channel, \
so do not penalize anyone for it). For example, do not lower a score for "no pull \
requests reviewed" if nobody reviewed pull requests.
4. A value of 0 does NOT necessarily mean the student did nothing. It may mean a \
platform was not connected, an account was not activated, or their identity could \
not be matched to the activity data. When 'connected' flags are false or an \
account is not active, treat low numbers as a POSSIBLE DATA GAP and add the flag \
"possible_data_issue" rather than assuming disengagement. Lower your confidence in \
these cases.
5. Do NOT infer or speculate about a student's intent, motivation, character, \
effort, health, or personal circumstances. Never describe a student as lazy, \
disengaged, or uncommitted. Describe only what the measured data shows, using \
neutral language such as "low measured activity on tracked platforms".
6. This output is decision-support for a human instructor who makes the final \
judgment. For anomalies, recommend instructor follow-up rather than drawing \
conclusions.

SCORING PROCEDURE (follow these steps in this exact order for every member; \
identical input data MUST always produce identical scores):

Step 1. List the ACTIVE channels: features whose value in group_totals is \
greater than 0. Ignore all other features entirely.
Step 2. Compute the FAIR SHARE = 1 / group_member_count (e.g. 0.25 for 4 \
members). For each member, compare their share on each active channel to the \
fair share.
Step 3. Summarize each member's overall standing across active channels, then \
assign the score using these fixed anchor bands:
- 0.00: no measurable activity on any active channel.
- 0.05-0.25: activity on at least one active channel, but shares are well below \
the fair share on most active channels (less than half the fair share).
- 0.30-0.45: shares are somewhat below the fair share on most active channels.
- 0.50-0.65: shares are approximately at the fair share (within about 25% of it) \
on most active channels.
- 0.70-0.85: shares are clearly above the fair share on most active channels.
- 0.90-1.00: dominant contributor: shares far exceed the fair share on most \
active channels.
Step 4. Pick the exact score within the band from how strongly the data supports \
it, and ROUND the score to exactly 2 decimal places.
Step 5. Set top_area to the active feature where the member's value is highest \
relative to the group; break ties by choosing the feature that appears first in \
the feature glossary. Use null only when there is no measurable contribution.
Step 6. Apply flags per the rules above, then set confidence: start at 0.9 when \
all platforms are connected and matched, subtract 0.15 for each data-gap concern \
(disconnected platform with zero activity, unmatched identity, or non-active \
account), round to 2 decimal places, and never go below 0.5 (if the result is \
lower, use exactly 0.5).

Write "reasoning" in 1-3 neutral sentences citing the specific numbers that \
determined the band (e.g. "Commit share 0.55 vs fair share 0.25"). Do not add \
commentary beyond the data.

Allowed values for "flags": "possible_data_issue", "low_measured_activity", \
"high_relative_contribution", "uneven_contribution", "single_member_group", \
"needs_instructor_review".

Allowed values for "top_area" are exactly one of the provided feature names, or \
null if there is no measurable contribution.

Return one result object per member ref provided, and nothing else.\
"""


def _feature_glossary() -> str:
    return (
        "Feature meanings (all per-student values are the student's SHARE of the "
        "group unless noted):\n"
        "- code_commits: share of the group's Git commits\n"
        "- code_share: share of the group's changed lines of code\n"
        "- review_participation: share of the group's pull-request reviews\n"
        "- attendance_ratio: average fraction of meeting time attended (already a "
        "0..1 ratio, not a share)\n"
        "- speaking_participation_ratio: average share of speaking turns in meetings\n"
        "- chat_participation_ratio: average share of chat messages in meetings\n"
        "- docs_contribution_share: share of the group's Google Docs edits\n"
        "- comment_activity: share of the group's Google Docs comments\n"
    )


def _round_numbers(value, ndigits: int = 4):
    """Round floats recursively so the serialized prompt is byte-stable.

    Tiny float noise (e.g. 0.33333333334 vs 0.33333333339) would otherwise
    change the prompt text between runs and shift the model's output.
    """
    if isinstance(value, float):
        return round(value, ndigits)
    if isinstance(value, dict):
        return {key: _round_numbers(item, ndigits) for key, item in value.items()}
    if isinstance(value, list):
        return [_round_numbers(item, ndigits) for item in value]
    return value


def _build_prompt(group_input: GroupScoringInput) -> str:
    payload = {
        "assignment_description": group_input.assignment_description
        or "No assignment description was provided.",
        "group_member_count": group_input.member_count,
        "group_totals": _round_numbers(group_input.group_totals),
        "members": [
            {
                "ref": member.ref,
                "contribution_shares": _round_numbers(member.features),
                "raw_github": member.raw_github,
                "raw_google_docs": member.raw_google_docs,
                "meeting_engagement": _round_numbers(member.meeting),
                "data_status": {
                    "github_connected": member.github_connected,
                    "google_connected": member.google_connected,
                    "google_email_matched": member.google_email_matched,
                    "account_status": member.account_status,
                },
            }
            for member in sorted(group_input.members, key=lambda m: m.ref)
        ],
    }
    return (
        f"{_feature_glossary()}\n"
        "Here is the group data as JSON. Score each member per the rules and "
        "the scoring procedure.\n\n"
        f"{json.dumps(payload, indent=2, sort_keys=True, default=str)}"
    )


def is_llm_scoring_available() -> bool:
    settings = get_settings()
    return bool(settings.LLM_SCORING_ENABLED and settings.GEMINI_API_KEY)


@lru_cache
def _get_client():
    settings = get_settings()
    if not is_llm_scoring_available():
        raise LLMScoringUnavailableError(
            "Gemini API is not configured. Set GEMINI_API_KEY and "
            "LLM_SCORING_ENABLED=true."
        )
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise LLMScoringUnavailableError(
            "google-genai is not installed. Run `pip install google-genai`."
        ) from exc

    timeout_ms = int(settings.LLM_TIMEOUT_SECONDS * 1000)
    return genai.Client(
        api_key=settings.GEMINI_API_KEY,
        http_options=types.HttpOptions(timeout=timeout_ms),
    )


async def score_group(group_input: GroupScoringInput) -> GroupScoringOutput:
    """Call Gemini once for the whole group and return validated results."""
    if not is_llm_scoring_available():
        raise LLMScoringUnavailableError(
            "Gemini API is not configured. Set GEMINI_API_KEY and "
            "LLM_SCORING_ENABLED=true."
        )
    if not group_input.members:
        return GroupScoringOutput(members=[], group_observations="", model_version="")

    settings = get_settings()
    from google.genai import types

    client = _get_client()
    # Determinism: greedy decoding (temperature 0, top_p 0, top_k 1), a fixed
    # seed, and no "thinking" tokens. Thinking is the main source of run-to-run
    # variance on Gemini 2.5 models, so it is disabled explicitly.
    config = types.GenerateContentConfig(
        system_instruction=_SYSTEM_INSTRUCTION,
        temperature=settings.LLM_TEMPERATURE,
        top_p=0.0,
        top_k=1,
        seed=42,
        candidate_count=1,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
        response_mime_type="application/json",
        response_schema=_GroupResult,
    )
    prompt = _build_prompt(group_input)

    last_error: Exception | None = None
    for attempt in range(settings.LLM_MAX_RETRIES + 1):
        try:
            response = await client.aio.models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=prompt,
                config=config,
            )
            parsed = _parse_response(response)
            return _to_output(parsed, group_input, settings.GEMINI_MODEL)
        except (LLMScoringError, LLMScoringUnavailableError):
            raise
        except Exception as exc:  # transient API/network errors
            last_error = exc
            if attempt < settings.LLM_MAX_RETRIES:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
    raise LLMScoringError(
        f"Gemini scoring request failed after retries: {last_error}"
    )


def _parse_response(response) -> _GroupResult:
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, _GroupResult):
        return parsed

    text = getattr(response, "text", None)
    if not text:
        raise LLMScoringError("Gemini returned an empty response.")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMScoringError(f"Gemini returned invalid JSON: {exc}") from exc
    try:
        return _GroupResult.model_validate(data)
    except Exception as exc:
        raise LLMScoringError(
            f"Gemini response did not match the expected schema: {exc}"
        ) from exc


def _to_output(
    parsed: _GroupResult,
    group_input: GroupScoringInput,
    model_version: str,
) -> GroupScoringOutput:
    result_by_ref = {item.ref: item for item in parsed.members}
    expected_refs = [member.ref for member in group_input.members]
    missing = [ref for ref in expected_refs if ref not in result_by_ref]
    if missing:
        raise LLMScoringError(
            f"Gemini response is missing results for members: {', '.join(missing)}"
        )

    members = [
        MemberScoringOutput(
            ref=ref,
            score=_clamp(result_by_ref[ref].score),
            top_area=result_by_ref[ref].top_area,
            reasoning=result_by_ref[ref].reasoning,
            flags=list(result_by_ref[ref].flags),
            # Confidence is floored at 0.5: below that the score is not useful
            # as decision support, so treat 0.5 as the minimum reportable value.
            confidence=_clamp(result_by_ref[ref].confidence, low=0.5),
        )
        for ref in expected_refs
    ]
    return GroupScoringOutput(
        members=members,
        group_observations=parsed.group_observations,
        model_version=model_version,
    )


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return low


def ref_for_index(index: int) -> str:
    """Anonymized, stable member label: Member A, Member B, ... Member AA."""
    letters = string.ascii_uppercase
    label = ""
    index += 1
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        label = letters[remainder] + label
    return f"Member {label}"
