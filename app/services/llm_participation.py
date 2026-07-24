"""Share-based participation scoring.

This module reports **each member's share of the group's *tracked* activity** —
Git commits and changed lines, pull-request reviews, Google Docs edits and
comments, and meeting speaking/chat/attendance. Shares are computed by plain
arithmetic and sum to 100% across the group, so the numbers reconcile and can be
explained in an appeal:

    fair share = 100% / member_count      (e.g. 20% in a group of 5)

A member above the fair-share line did a larger-than-equal portion of what we
could measure; a member below it did less. The number is **not** "share of the
work" and **not** a grade: untracked work (whiteboarding, offline discussion,
thinking, tools we don't observe) is invisible to it, and it is decision-support
for a human instructor who makes the final call.

Design notes (why this looks different from a model-graded scorer):

* The **score is arithmetic**, derived from the per-channel shares the caller
  already computes in ``features``. It is fully deterministic and reproducible
  regardless of any model version. ``SCORING_METHOD_VERSION`` identifies it.
* Each active channel is **normalized to sum to 1 across members, then averaged
  with equal weight** (see ``CHANNEL_WEIGHTS`` to override). Equal weighting is
  the defensible default: every measured kind of contribution counts the same.
  Because each channel sums to 1, the averaged per-member shares also sum to 1.
* Channels the group did not use (group total 0) are **ignored**, so no one is
  penalized for a channel nobody used.
* Inactive members **stay in the denominator**: a member who did nothing shows a
  near-0% share and the others visibly absorb it. Fair share stays at
  100/member_count of the full roster.
* ``data_completeness`` replaces the old model "confidence". It answers *how far
  should the instructor trust this percentage as reflecting reality* — it drops
  when document attribution is unreliable or the evidence is thin. It is NOT
  band-fit confidence, and a clean-looking share can still have low completeness.
* The **LLM writes prose only** — a neutral per-member reasoning sentence and a
  group summary — and it is best-effort. If it is unavailable or fails, scoring
  still succeeds with templated reasoning built from the same numbers.
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
    """Raised when the LLM reasoning helper is disabled or not configured.

    NOTE: as of the share-based rewrite this no longer blocks scoring. Numeric
    scores are computed arithmetically and always available; the LLM only writes
    prose. ``score_group`` catches this and falls back to templated reasoning.
    """


class LLMScoringError(Exception):
    """Raised when the LLM call fails or returns an unusable response.

    Like the above, this is swallowed by ``score_group`` for prose only; it does
    not fail the numeric result.
    """


# ---------------------------------------------------------------------------
# Scoring configuration
# ---------------------------------------------------------------------------
# Identifies the deterministic scoring method for persistence/audit. Bump this
# if the channel set, weighting, or normalization rule changes. It is what makes
# a stored score reproducible — the LLM prose model is recorded separately.
SCORING_METHOD_VERSION = "participation-shares-v1"

# Channels that make up the contribution share, in glossary order. Order is the
# tie-breaker for ``top_area``. Every channel here is normalized to each
# member's share of that channel across the group before averaging, so a channel
# that is already a share (sums to 1) is unchanged, while a ratio such as
# ``attendance_ratio`` becomes "share of total attended meeting-time".
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

# Per-channel weights. Empty means EQUAL weight across active channels — the
# defensible default. To weight channels differently (e.g. de-emphasize meeting
# presence), set entries here; missing channels default to 1.0. Any weighting
# still yields per-member shares that sum to 1.0.
CHANNEL_WEIGHTS: dict[str, float] = {}

# Flag thresholds, expressed as multiples of the fair share (1 / member_count).
_LOW_ACTIVITY_FACTOR = 0.5       # below half the fair share -> low_measured_activity
_HIGH_CONTRIB_FACTOR = 1.5       # at/above 1.5x fair share -> high_relative_contribution
_UNEVEN_GROUP_FACTOR = 2.0       # any member at/above 2x fair share -> group is uneven
_UNEVEN_OUTLIER_FACTOR = 0.5     # deviation > 0.5x fair share -> outlier in an uneven group
_LOW_COMPLETENESS = 0.5          # at/below this -> recommend review


# ---------------------------------------------------------------------------
# Input data structures (unchanged — callers build these from their own data)
# ---------------------------------------------------------------------------
@dataclass
class MemberScoringInput:
    ref: str  # anonymized label, e.g. "Member A"
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


# ---------------------------------------------------------------------------
# Output data structures
# ---------------------------------------------------------------------------
@dataclass
class MemberScoringOutput:
    ref: str
    # Canonical share of tracked activity, 0..1. Percentage = score * 100.
    score: float
    # Convenience percentage for display (score * 100, rounded).
    share_pct: float
    # Per-active-channel normalized share (0..1), for showing the raw numbers
    # beside the headline so it reads as a summary, not a verdict.
    channel_shares: dict[str, float]
    top_area: str | None
    reasoning: str
    flags: list[str] = field(default_factory=list)
    # Trust in the measurement itself (0..1). Low = don't over-trust this number.
    data_completeness: float = 1.0


@dataclass
class GroupScoringOutput:
    members: list[MemberScoringOutput]
    # Equal-split reference line, 0..1 (fair_share_pct = fair_share * 100).
    # Draw this on screen so nobody panics at the number that means "balanced".
    fair_share: float
    fair_share_pct: float
    active_channels: list[str]
    group_observations: str
    # Deterministic scoring method that produced the numbers.
    model_version: str
    # Which LLM wrote the prose, or None if templated fallback was used.
    reasoning_model: str | None = None


# ---------------------------------------------------------------------------
# Structured-output schema — the model returns PROSE ONLY, never numbers.
# ---------------------------------------------------------------------------
class _MemberReasoning(BaseModel):
    ref: str
    reasoning: str = ""


class _GroupReasoning(BaseModel):
    members: list[_MemberReasoning] = Field(default_factory=list)
    group_observations: str = ""


_SYSTEM_INSTRUCTION = """\
You are a neutral technical writer. The contribution numbers have ALREADY been \
computed arithmetically and are given to you. Your ONLY job is to write short, \
neutral explanations for a human instructor. You do not score, rank, or judge.

Rules:
1. Use ONLY the numbers provided. Never invent, recompute, or contradict a \
share, fair-share, flag, or top_area value. Quote the given figures.
2. Describe the numbers as a "share of tracked activity", never as "share of the \
work", a grade, or a measure of effort. The numbers only cover measured activity \
on GitHub, Google Docs, and meeting platforms; untracked work is invisible.
3. Describe only what the data shows. NEVER infer or speculate about a student's \
intent, motivation, character, effort, health, or circumstances. Never call a \
student lazy, disengaged, or uncommitted. Use neutral phrasing such as "low \
measured activity on tracked platforms".
4. A value of 0 does not prove a student did nothing — it means nothing was \
measured on tracked platforms.
5. For any member carrying a flag (especially possible_data_issue or \
needs_instructor_review), recommend instructor follow-up rather than drawing a \
conclusion.
6. Per-member "reasoning": 1-3 sentences citing the specific share numbers that \
matter (e.g. "Tracked-activity share 55% vs a 50% fair share; highest relative \
activity on code_commits."). "group_observations": 1-3 sentences on how tracked \
activity was distributed across the group and whether follow-up is warranted.

Return one reasoning object per member ref provided, and a group_observations \
string. Output nothing else.\
"""


def _feature_glossary() -> str:
    return (
        "Feature meanings (each is the member's SHARE of the group unless noted):\n"
        "- code_commits: share of the group's Git commits\n"
        "- code_share: share of the group's changed lines of code\n"
        "- review_participation: share of the group's pull-request reviews\n"
        "- attendance_ratio: fraction of meeting time attended, normalized here "
        "into a share of total attended meeting-time\n"
        "- speaking_participation_ratio: share of speaking turns in meetings\n"
        "- chat_participation_ratio: share of chat messages in meetings\n"
        "- docs_contribution_share: share of the group's Google Docs edits\n"
        "- comment_activity: share of the group's Google Docs comments\n"
        "\n"
        "github_events: per-commit detail (message, lines_changed, additions, "
        "deletions, timestamp) for qualitative context in reasoning only.\n"
    )


def _serialize_github_events(events: list[dict] | None) -> list[dict] | None:
    """Keep commit events only, strip identity fields, sort for stable prompts."""
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
    """Round floats recursively so the serialized prompt is byte-stable."""
    if isinstance(value, float):
        return round(value, ndigits)
    if isinstance(value, dict):
        return {key: _round_numbers(item, ndigits) for key, item in value.items()}
    if isinstance(value, list):
        return [_round_numbers(item, ndigits) for item in value]
    return value


# ---------------------------------------------------------------------------
# Deterministic scoring — the number, top_area, flags, and completeness.
# ---------------------------------------------------------------------------
def _feature_value(member: MemberScoringInput, channel: str) -> float:
    """Non-negative float for a channel; missing/None/negative -> 0.0."""
    raw = (member.features or {}).get(channel)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return value if value > 0 else 0.0


def _active_channels(members: list[MemberScoringInput]) -> list[str]:
    """Channels the group actually used (member total > 0), in glossary order."""
    active: list[str] = []
    for channel in CONTRIBUTION_CHANNELS:
        if sum(_feature_value(member, channel) for member in members) > 0:
            active.append(channel)
    return active


def _channel_shares(
    members: list[MemberScoringInput], active: list[str]
) -> dict[str, dict[str, float]]:
    """Normalize each active channel to each member's share of it (sums to 1)."""
    shares: dict[str, dict[str, float]] = {member.ref: {} for member in members}
    for channel in active:
        total = sum(_feature_value(member, channel) for member in members)
        if total <= 0:
            continue
        for member in members:
            shares[member.ref][channel] = _feature_value(member, channel) / total
    return shares


def _overall_share(
    ref_shares: dict[str, float], active: list[str]
) -> float:
    """Weighted mean of a member's channel shares. Sums to 1 across members."""
    if not active:
        return 0.0
    weights = [CHANNEL_WEIGHTS.get(channel, 1.0) for channel in active]
    total_weight = sum(weights)
    if total_weight <= 0:
        return 0.0
    weighted = sum(
        CHANNEL_WEIGHTS.get(channel, 1.0) * ref_shares.get(channel, 0.0)
        for channel in active
    )
    return weighted / total_weight


def _top_area(ref_shares: dict[str, float], active: list[str]) -> str | None:
    """Active channel where the member's share is highest; glossary order breaks ties."""
    best_channel: str | None = None
    best_value = 0.0
    for channel in active:  # active is already in glossary order
        value = ref_shares.get(channel, 0.0)
        if value > best_value:
            best_value = value
            best_channel = channel
    return best_channel if best_value > 0 else None


def _data_completeness(
    member: MemberScoringInput,
    ref_shares: dict[str, float],
    active: list[str],
) -> float:
    """How much the instructor should trust this share as reflecting reality.

    Lowered by unreliable document attribution and by thin evidence (few active
    channels overall, or the member appearing on few of them). This is NOT about
    how cleanly the score fits a band — there are no bands.
    """
    if not active:
        return 0.2  # no tracked activity anywhere: the share is not meaningful
    completeness = 1.0
    # Document activity could not be reliably attributed to this student.
    if member.google_email_matched is False:
        completeness *= 0.5
    # Breadth of evidence: on how many active channels does the member appear?
    present = sum(1 for channel in active if ref_shares.get(channel, 0.0) > 0)
    breadth = present / len(active)
    completeness *= 0.5 + 0.5 * breadth  # 0.5 (nothing) .. 1.0 (every channel)
    # A single-channel group is a fragile basis for any share.
    if len(active) == 1:
        completeness *= 0.7
    return round(_clamp(completeness, 0.0, 1.0), 2)


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
    # Attribution issue only when the email match explicitly failed — connection
    # status alone never triggers this (activity is captured from linked
    # repos/docs regardless of whether the student connected an account).
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
    # Stable, de-duplicated order.
    order = [
        "possible_data_issue",
        "low_measured_activity",
        "high_relative_contribution",
        "uneven_contribution",
        "single_member_group",
        "needs_instructor_review",
    ]
    return [flag for flag in order if flag in flags]


@dataclass
class _ComputedMember:
    ref: str
    score: float
    channel_shares: dict[str, float]
    top_area: str | None
    flags: list[str]
    data_completeness: float


def compute_shares(group_input: GroupScoringInput) -> tuple[list[_ComputedMember], float, list[str]]:
    """Compute every member's share of tracked activity and its annotations.

    Pure arithmetic, no LLM. Returns (computed members, fair_share, active
    channels). Per-member ``score`` values sum to ~1.0 across the group.
    """
    members = group_input.members
    member_count = group_input.member_count or len(members)
    fair_share = 1.0 / member_count if member_count > 0 else 0.0
    active = _active_channels(members)
    per_channel = _channel_shares(members, active)

    if not active:
        # No tracked activity at all: shares default to the equal split so they
        # still sum to 100%, but completeness is floored and everything is
        # flagged so the number is never read as real contribution.
        computed = [
            _ComputedMember(
                ref=member.ref,
                score=round(fair_share, 4),
                channel_shares={},
                top_area=None,
                flags=_flags(
                    member, fair_share, fair_share, 0.2, member_count,
                    group_is_uneven=False, group_min_score=fair_share,
                ),
                data_completeness=0.2,
            )
            for member in members
        ]
        return computed, round(fair_share, 4), active

    raw_scores = {
        member.ref: _overall_share(per_channel[member.ref], active)
        for member in members
    }
    max_score = max(raw_scores.values(), default=0.0)
    min_score = min(raw_scores.values(), default=0.0)
    group_is_uneven = max_score >= _UNEVEN_GROUP_FACTOR * fair_share

    computed: list[_ComputedMember] = []
    for member in members:
        score = raw_scores[member.ref]
        completeness = _data_completeness(member, per_channel[member.ref], active)
        computed.append(
            _ComputedMember(
                ref=member.ref,
                score=round(score, 4),
                channel_shares={
                    channel: round(per_channel[member.ref].get(channel, 0.0), 4)
                    for channel in active
                },
                top_area=_top_area(per_channel[member.ref], active),
                flags=_flags(
                    member, score, fair_share, completeness, member_count,
                    group_is_uneven, min_score,
                ),
                data_completeness=completeness,
            )
        )
    return computed, round(fair_share, 4), active


# ---------------------------------------------------------------------------
# Templated reasoning (used when the LLM is unavailable or fails).
# ---------------------------------------------------------------------------
def _fallback_reasoning(member: _ComputedMember, fair_share: float) -> str:
    pct = round(member.score * 100, 1)
    fair_pct = round(fair_share * 100, 1)
    if not member.channel_shares:
        return (
            f"No tracked activity was recorded; the {pct}% share is the equal "
            f"split default and cannot be read as contribution. Instructor "
            f"follow-up recommended."
        )
    if member.top_area:
        area = f" Highest relative activity on {member.top_area}."
    else:
        area = ""
    review = (
        " Flags recommend instructor follow-up."
        if "needs_instructor_review" in member.flags
        else ""
    )
    return (
        f"Tracked-activity share {pct}% vs a {fair_pct}% fair share.{area}{review}"
    )


def _fallback_group_observations(
    computed: list[_ComputedMember], fair_share: float
) -> str:
    fair_pct = round(fair_share * 100, 1)
    if not computed:
        return ""
    scores = [member.score for member in computed]
    spread = (max(scores) - min(scores)) * 100
    even = "evenly" if spread < fair_pct * 0.5 else "unevenly"
    return (
        f"Tracked activity was distributed {even} across {len(computed)} "
        f"members (fair share {fair_pct}%). Percentages reflect measured "
        f"activity only and are decision-support for the instructor."
    )


# ---------------------------------------------------------------------------
# LLM prose helper (best-effort — never fails the numeric result).
# ---------------------------------------------------------------------------
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
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise LLMScoringUnavailableError(
            "google-genai is not installed. Run `pip install google-genai`."
        ) from exc

    timeout_ms = int(settings.LLM_TIMEOUT_SECONDS * 1000)
    return genai.Client(
        api_key=settings.GEMINI_API_KEY,
        http_options=types.HttpOptions(timeout=timeout_ms),
    )


def _build_prompt(
    group_input: GroupScoringInput,
    computed: list[_ComputedMember],
    fair_share: float,
    active: list[str],
) -> str:
    by_ref = {member.ref: member for member in computed}
    fair_pct = round(fair_share * 100, 2)
    payload = {
        "assignment_description": group_input.assignment_description
        or "No assignment description was provided.",
        "group_member_count": group_input.member_count,
        "fair_share_pct": fair_pct,
        "active_channels": active,
        "members": [
            {
                "ref": member.ref,
                "tracked_activity_share_pct": round(by_ref[member.ref].score * 100, 2),
                "fair_share_pct": fair_pct,
                "channel_shares_pct": {
                    channel: round(value * 100, 2)
                    for channel, value in by_ref[member.ref].channel_shares.items()
                },
                "top_area": by_ref[member.ref].top_area,
                "flags": by_ref[member.ref].flags,
                "data_completeness": by_ref[member.ref].data_completeness,
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
            for member in sorted(group_input.members, key=lambda m: m.ref)
        ],
    }
    return (
        f"{_feature_glossary()}\n"
        "The shares below were computed arithmetically. Write neutral reasoning "
        "for each member and a group summary, using ONLY these numbers.\n\n"
        f"{json.dumps(payload, indent=2, sort_keys=True, default=str)}"
    )


async def _request_reasoning(
    group_input: GroupScoringInput,
    computed: list[_ComputedMember],
    fair_share: float,
    active: list[str],
) -> tuple[dict[str, str], str, str]:
    """Ask the LLM for prose. Returns (reasoning_by_ref, group_observations, model)."""
    settings = get_settings()
    from google.genai import types

    client = _get_client()
    # Prose only, but still pinned/greedy so wording is stable run to run.
    config = types.GenerateContentConfig(
        system_instruction=_SYSTEM_INSTRUCTION,
        temperature=settings.LLM_TEMPERATURE,
        top_p=0.0,
        top_k=1,
        seed=42,
        candidate_count=1,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
        response_mime_type="application/json",
        response_schema=_GroupReasoning,
    )
    prompt = _build_prompt(group_input, computed, fair_share, active)

    last_error: Exception | None = None
    for attempt in range(settings.LLM_MAX_RETRIES + 1):
        try:
            response = await client.aio.models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=prompt,
                config=config,
            )
            parsed = _parse_reasoning(response)
            reasoning_by_ref = {item.ref: item.reasoning for item in parsed.members}
            return reasoning_by_ref, parsed.group_observations, settings.GEMINI_MODEL
        except Exception as exc:  # transient API/network/parse errors
            last_error = exc
            if attempt < settings.LLM_MAX_RETRIES:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
    raise LLMScoringError(f"Gemini reasoning request failed after retries: {last_error}")


async def score_group(group_input: GroupScoringInput) -> GroupScoringOutput:
    """Score a group. Numbers are arithmetic; LLM prose is best-effort.

    Unlike the previous version, this does NOT raise when the LLM is off — the
    scores are computed regardless and templated reasoning is used instead.
    """
    if not group_input.members:
        return GroupScoringOutput(
            members=[],
            fair_share=0.0,
            fair_share_pct=0.0,
            active_channels=[],
            group_observations="",
            model_version=SCORING_METHOD_VERSION,
            reasoning_model=None,
        )

    computed, fair_share, active = compute_shares(group_input)

    # Deterministic fallback prose first; override with the LLM if it succeeds.
    reasoning_by_ref = {
        member.ref: _fallback_reasoning(member, fair_share) for member in computed
    }
    group_observations = _fallback_group_observations(computed, fair_share)
    reasoning_model: str | None = None

    if is_llm_scoring_available():
        try:
            llm_reasoning, llm_observations, model = await _request_reasoning(
                group_input, computed, fair_share, active
            )
            for member in computed:
                text = (llm_reasoning.get(member.ref) or "").strip()
                if text:
                    reasoning_by_ref[member.ref] = text
            if llm_observations.strip():
                group_observations = llm_observations.strip()
            reasoning_model = model
        except (LLMScoringError, LLMScoringUnavailableError) as exc:
            logger.warning("LLM reasoning unavailable; using templated prose: %s", exc)

    members = [
        MemberScoringOutput(
            ref=member.ref,
            score=member.score,
            share_pct=round(member.score * 100, 1),
            channel_shares=member.channel_shares,
            top_area=member.top_area,
            reasoning=reasoning_by_ref[member.ref],
            flags=member.flags,
            data_completeness=member.data_completeness,
        )
        for member in computed
    ]
    return GroupScoringOutput(
        members=members,
        fair_share=fair_share,
        fair_share_pct=round(fair_share * 100, 1),
        active_channels=active,
        group_observations=group_observations,
        model_version=SCORING_METHOD_VERSION,
        reasoning_model=reasoning_model,
    )


def _parse_reasoning(response) -> _GroupReasoning:
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, _GroupReasoning):
        return parsed
    text = getattr(response, "text", None)
    if not text:
        raise LLMScoringError("Gemini returned an empty response.")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMScoringError(f"Gemini returned invalid JSON: {exc}") from exc
    try:
        return _GroupReasoning.model_validate(data)
    except Exception as exc:
        raise LLMScoringError(
            f"Gemini response did not match the expected schema: {exc}"
        ) from exc


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