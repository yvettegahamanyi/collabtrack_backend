"""LLM participation scoring.

Gemini assigns each member a relative contribution score (0..1) from anonymized
activity data: normalized feature shares, **raw commit/doc counts**, per-commit
volume in ``github_events``, and meeting engagement. The model weighs commit
count and lines changed alongside other channels — not share averages alone.

``compute_shares`` helpers still derive ``channel_shares``, ``top_area``, and
``flags`` for display; the **score** itself comes from the LLM response.
"""
from __future__ import annotations

import asyncio
import json
import logging
import string
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class LLMScoringUnavailableError(Exception):
    """Raised when LLM scoring is disabled or Gemini is not configured."""


class LLMScoringError(Exception):
    """Raised when the LLM call fails or returns an unusable response."""


SCORING_METHOD_VERSION = "llm-participation-v2"

CONTRIBUTION_CHANNELS: tuple[str, ...] = (
    "code_commits",
    "code_share",
    "review_participation",
    "attendance_ratio",
    "speaking_participation_ratio",
    "chat_participation_ratio",
    "docs_contribution_share",
    "comment_activity",
)

_LOW_ACTIVITY_FACTOR = 0.5
_HIGH_CONTRIB_FACTOR = 1.5
_UNEVEN_GROUP_FACTOR = 2.0
_UNEVEN_OUTLIER_FACTOR = 0.5
_LOW_COMPLETENESS = 0.5


@dataclass
class MemberScoringInput:
    ref: str
    features: dict[str, float]
    raw_github: dict[str, int] | None
    github_events: list[dict] | None
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
    share_pct: float
    channel_shares: dict[str, float]
    top_area: str | None
    reasoning: str
    flags: list[str] = field(default_factory=list)
    data_completeness: float = 1.0

    @property
    def confidence(self) -> float:
        return self.data_completeness


@dataclass
class GroupScoringOutput:
    members: list[MemberScoringOutput]
    fair_share: float
    fair_share_pct: float
    active_channels: list[str]
    group_observations: str
    model_version: str
    reasoning_model: str | None = None


class _MemberResult(BaseModel):
    ref: str
    score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    reasoning: str = ""
    top_area: str | None = None


class _GroupResult(BaseModel):
    members: list[_MemberResult] = Field(default_factory=list)
    group_observations: str = ""


_SYSTEM_INSTRUCTION = """\
You are an impartial group-work analyst. Score each member's RELATIVE tracked \
contribution on a 0..1 scale for the group described.

Use ALL evidence provided:
1. **Normalized features** (0..1 shares per channel).
2. **Raw counts** in raw_github (total_commits, lines_changed, prs_reviewed) and \
raw_google_docs (edits, comments). Commit **volume** matters: weigh \
total_commits and aggregate lines_changed, not just the code_commits share alone.
3. **github_events**: per-commit message and lines_changed/additions/deletions. \
Many substantive commits should score higher than few trivial ones with similar \
share percentages.
4. **Meeting engagement** (attendance, speaking, chat, facilitator role).
5. **data_status**: lower confidence when google_email_matched is false or \
platforms are disconnected.

Scoring rules:
- Scores are RELATIVE within the group. In a balanced group of N members, \
expect scores near 1/N (~0.2 for five members). Higher/lower when activity is \
clearly uneven.
- A 0 on a channel means nothing was measured there, not proof of zero effort.
- Do not infer character, laziness, or intent. Base scores on measured activity.
- **confidence** (0..1): how well the data supports your score (thin or \
mis-attributed data -> lower confidence).
- **top_area**: the channel where this member was strongest (use feature names \
from the glossary), or null if unclear.
- **reasoning**: 1-3 neutral sentences citing specific numbers (commits, lines, \
shares) that drove the score.

Return one result per member ref and a brief group_observations summary.\
"""


def _feature_glossary() -> str:
    return (
        "Feature meanings (normalized 0..1 shares unless noted):\n"
        "- code_commits: share of group Git commits\n"
        "- code_share: share of group changed lines of code\n"
        "- review_participation: share of group PR reviews\n"
        "- attendance_ratio: fraction of meeting time attended\n"
        "- speaking_participation_ratio: share of speaking turns\n"
        "- chat_participation_ratio: share of chat messages\n"
        "- docs_contribution_share: share of Google Docs edits\n"
        "- comment_activity: share of Google Docs comments\n"
        "\n"
        "raw_github.total_commits and raw_github.lines_changed are absolute counts — "
        "use them together with code_commits/code_share and github_events.\n"
        "github_events lists each commit's message and line volume.\n"
    )


def _serialize_github_events(events: list[dict] | None) -> list[dict] | None:
    if not events:
        return None
    commits = [event for event in events if event.get("type") == "commit"]
    if not commits:
        return None
    return [
        {
            "type": event.get("type"),
            "message": event.get("message"),
            "additions": event.get("additions"),
            "deletions": event.get("deletions"),
            "lines_changed": event.get("lines_changed"),
            "timestamp": event.get("timestamp"),
        }
        for event in sorted(
            commits,
            key=lambda item: (item.get("timestamp") or "", item.get("message") or ""),
        )
    ]


def _round_numbers(value, ndigits: int = 4):
    if isinstance(value, float):
        return round(value, ndigits)
    if isinstance(value, dict):
        return {key: _round_numbers(item, ndigits) for key, item in value.items()}
    if isinstance(value, list):
        return [_round_numbers(item, ndigits) for item in value]
    return value


def _feature_value(member: MemberScoringInput, channel: str) -> float:
    raw = (member.features or {}).get(channel)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return value if value > 0 else 0.0


def _active_channels(members: list[MemberScoringInput]) -> list[str]:
    active: list[str] = []
    for channel in CONTRIBUTION_CHANNELS:
        if sum(_feature_value(member, channel) for member in members) > 0:
            active.append(channel)
    return active


def _channel_shares(
    members: list[MemberScoringInput], active: list[str]
) -> dict[str, dict[str, float]]:
    shares: dict[str, dict[str, float]] = {member.ref: {} for member in members}
    for channel in active:
        total = sum(_feature_value(member, channel) for member in members)
        if total <= 0:
            continue
        for member in members:
            shares[member.ref][channel] = _feature_value(member, channel) / total
    return shares


def _top_area(ref_shares: dict[str, float], active: list[str]) -> str | None:
    best_channel: str | None = None
    best_value = 0.0
    for channel in active:
        value = ref_shares.get(channel, 0.0)
        if value > best_value:
            best_value = value
            best_channel = channel
    return best_channel if best_value > 0 else None


def _flags(
    member: MemberScoringInput,
    score: float,
    fair_share: float,
    completeness: float,
    member_count: int,
    group_is_uneven: bool,
    group_min_score: float,
) -> list[str]:
    flags: list[str] = []
    if member_count <= 1:
        flags.append("single_member_group")
    if member.google_email_matched is False:
        flags.append("possible_data_issue")
    if score <= _LOW_ACTIVITY_FACTOR * fair_share:
        flags.append("low_measured_activity")
    if score >= _HIGH_CONTRIB_FACTOR * fair_share:
        flags.append("high_relative_contribution")
    if group_is_uneven and abs(score - fair_share) > _UNEVEN_OUTLIER_FACTOR * fair_share:
        flags.append("uneven_contribution")
    needs_review = (
        "possible_data_issue" in flags
        or completeness <= _LOW_COMPLETENESS
        or (group_is_uneven and group_min_score <= _LOW_ACTIVITY_FACTOR * fair_share)
    )
    if needs_review:
        flags.append("needs_instructor_review")
    order = [
        "possible_data_issue",
        "low_measured_activity",
        "high_relative_contribution",
        "uneven_contribution",
        "single_member_group",
        "needs_instructor_review",
    ]
    return [flag for flag in order if flag in flags]


def _build_prompt(group_input: GroupScoringInput) -> str:
    fair_share = (
        1.0 / group_input.member_count if group_input.member_count > 0 else 0.0
    )
    fair_pct = round(fair_share * 100, 2)
    payload = {
        "assignment_description": group_input.assignment_description
        or "No assignment description was provided.",
        "group_member_count": group_input.member_count,
        "fair_share_reference": fair_share,
        "fair_share_pct": fair_pct,
        "group_totals": _round_numbers(group_input.group_totals),
        "members": [
            {
                "ref": member.ref,
                "features": _round_numbers(member.features or {}),
                "raw_github": member.raw_github,
                "github_events": _serialize_github_events(member.github_events),
                "raw_google_docs": member.raw_google_docs,
                "meeting_engagement": _round_numbers(member.meeting),
                "data_status": {
                    "github_connected": member.github_connected,
                    "google_connected": member.google_connected,
                    "google_email_matched": member.google_email_matched,
                    "account_status": member.account_status,
                },
            }
            for member in sorted(group_input.members, key=lambda item: item.ref)
        ],
    }
    return (
        f"{_feature_glossary()}\n"
        "Score each member's relative contribution. Weight commit volume "
        "(total_commits, lines_changed, github_events) alongside other channels.\n\n"
        f"{json.dumps(payload, indent=2, sort_keys=True, default=str)}"
    )


def is_llm_scoring_available() -> bool:
    settings = get_settings()
    return bool(settings.LLM_SCORING_ENABLED and settings.GEMINI_API_KEY)


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
    except ImportError as exc:  # pragma: no cover
        raise LLMScoringUnavailableError(
            "google-genai is not installed. Run `pip install google-genai`."
        ) from exc

    timeout_ms = int(settings.LLM_TIMEOUT_SECONDS * 1000)
    return genai.Client(
        api_key=settings.GEMINI_API_KEY,
        http_options=types.HttpOptions(timeout=timeout_ms),
    )


async def _request_scoring(group_input: GroupScoringInput) -> _GroupResult:
    settings = get_settings()
    from google.genai import types

    client = _get_client()
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
            return _parse_response(response)
        except Exception as exc:
            last_error = exc
            if attempt < settings.LLM_MAX_RETRIES:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
    raise LLMScoringError(f"Gemini scoring request failed after retries: {last_error}")


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
    members = group_input.members
    member_count = group_input.member_count or len(members)
    fair_share = 1.0 / member_count if member_count > 0 else 0.0
    active = _active_channels(members)
    per_channel = _channel_shares(members, active)

    parsed_by_ref = {item.ref: item for item in parsed.members}
    for member in members:
        if member.ref not in parsed_by_ref:
            raise LLMScoringError(
                f"Scoring model returned missing results for {member.ref}"
            )

    llm_scores = {
        member.ref: round(_clamp(parsed_by_ref[member.ref].score), 4)
        for member in members
    }
    max_score = max(llm_scores.values(), default=0.0)
    min_score = min(llm_scores.values(), default=0.0)
    group_is_uneven = max_score >= _UNEVEN_GROUP_FACTOR * fair_share

    output_members: list[MemberScoringOutput] = []
    for member in members:
        result = parsed_by_ref[member.ref]
        score = llm_scores[member.ref]
        ref_shares = {
            channel: round(per_channel[member.ref].get(channel, 0.0), 4)
            for channel in active
        }
        completeness = round(_clamp(result.confidence, 0.5, 1.0), 2)
        top_area = result.top_area or _top_area(ref_shares, active)
        output_members.append(
            MemberScoringOutput(
                ref=member.ref,
                score=score,
                share_pct=round(score * 100, 1),
                channel_shares=ref_shares,
                top_area=top_area,
                reasoning=(result.reasoning or "").strip(),
                flags=_flags(
                    member,
                    score,
                    fair_share,
                    completeness,
                    member_count,
                    group_is_uneven,
                    min_score,
                ),
                data_completeness=completeness,
            )
        )

    return GroupScoringOutput(
        members=output_members,
        fair_share=round(fair_share, 4),
        fair_share_pct=round(fair_share * 100, 1),
        active_channels=active,
        group_observations=(parsed.group_observations or "").strip(),
        model_version=model_version,
        reasoning_model=model_version,
    )


async def score_group(group_input: GroupScoringInput) -> GroupScoringOutput:
    """Score a group via Gemini. Raises if the LLM is unavailable or fails."""
    if not group_input.members:
        return GroupScoringOutput(
            members=[],
            fair_share=0.0,
            fair_share_pct=0.0,
            active_channels=[],
            group_observations="",
            model_version="",
            reasoning_model=None,
        )

    if not is_llm_scoring_available():
        raise LLMScoringUnavailableError(
            "Gemini API is not configured. Set GEMINI_API_KEY and "
            "LLM_SCORING_ENABLED=true."
        )

    settings = get_settings()
    parsed = await _request_scoring(group_input)
    return _to_output(parsed, group_input, settings.GEMINI_MODEL)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return low


def ref_for_index(index: int) -> str:
    letters = string.ascii_uppercase
    label = ""
    index += 1
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        label = letters[remainder] + label
    return f"Member {label}"
