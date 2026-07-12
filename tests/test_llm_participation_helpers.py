import json
from types import SimpleNamespace

import pytest

from app.services import llm_participation as llm


def test_ref_for_index_labels():
    assert llm.ref_for_index(0) == "Member A"
    assert llm.ref_for_index(25) == "Member Z"
    assert llm.ref_for_index(26) == "Member AA"


def test_clamp_bounds_values():
    assert llm._clamp(1.5) == 1.0
    assert llm._clamp(-0.2) == 0.0
    assert llm._clamp(0.42) == 0.42
    assert llm._clamp("bad", low=0.25) == 0.25


def test_round_numbers_recursively():
    payload = {"score": 0.123456789, "items": [0.9999999, {"x": 1.23456789}]}
    rounded = llm._round_numbers(payload)

    assert rounded["score"] == 0.1235
    assert rounded["items"][0] == 1.0
    assert rounded["items"][1]["x"] == 1.2346


def test_serialize_github_events_filters_and_sorts():
    events = [
        {
            "type": "commit",
            "message": "b",
            "timestamp": "2026-01-02T00:00:00Z",
            "author": "secret",
        },
        {"type": "review", "message": "ignored"},
        {
            "type": "commit",
            "message": "a",
            "timestamp": "2026-01-01T00:00:00Z",
        },
    ]

    serialized = llm._serialize_github_events(events)

    assert serialized is not None
    assert len(serialized) == 2
    assert serialized[0]["message"] == "a"
    assert "author" not in serialized[0]


def test_serialize_github_events_returns_none_for_empty_input():
    assert llm._serialize_github_events(None) is None
    assert llm._serialize_github_events([]) is None


def test_feature_glossary_lists_feature_names():
    glossary = llm._feature_glossary()
    assert "code_commits" in glossary
    assert "docs_contribution_share" in glossary


def test_build_prompt_includes_member_refs():
    group_input = llm.GroupScoringInput(
        assignment_description="Build a capstone project",
        member_count=1,
        group_totals={"code_commits": 1.0},
        members=[
            llm.MemberScoringInput(
                ref="Member A",
                features={"code_commits": 1.0},
                raw_github=None,
                github_events=None,
                raw_google_docs=None,
                meeting=None,
                github_connected=True,
                google_connected=False,
                google_email_matched=None,
                account_status="ACTIVE",
            )
        ],
    )

    prompt = llm._build_prompt(group_input)

    assert "Member A" in prompt
    assert "Build a capstone project" in prompt


def test_parse_response_accepts_parsed_model():
    parsed = llm._GroupResult(
        members=[
            llm._MemberResult(ref="Member A", score=0.7, reasoning="Solid work")
        ],
        group_observations="Balanced team",
    )
    response = SimpleNamespace(parsed=parsed, text=None)

    assert llm._parse_response(response) == parsed


def test_parse_response_parses_json_text():
    payload = {
        "members": [{"ref": "Member A", "score": 0.6, "reasoning": "Good"}],
        "group_observations": "OK",
    }
    response = SimpleNamespace(parsed=None, text=json.dumps(payload))

    parsed = llm._parse_response(response)

    assert parsed.members[0].ref == "Member A"
    assert parsed.group_observations == "OK"


def test_parse_response_rejects_empty_response():
    with pytest.raises(llm.LLMScoringError, match="empty response"):
        llm._parse_response(SimpleNamespace(parsed=None, text=None))


def test_to_output_clamps_scores_and_preserves_order():
    parsed = llm._GroupResult(
        members=[
            llm._MemberResult(
                ref="Member A",
                score=1.5,
                confidence=2.0,
                reasoning="High",
            ),
            llm._MemberResult(
                ref="Member B",
                score=-0.1,
                confidence=0.1,
                reasoning="Low",
            ),
        ],
        group_observations="Wide spread",
    )
    group_input = llm.GroupScoringInput(
        assignment_description="Test",
        member_count=2,
        group_totals={},
        members=[
            llm.MemberScoringInput(
                ref="Member A",
                features={},
                raw_github=None,
                github_events=None,
                raw_google_docs=None,
                meeting=None,
                github_connected=False,
                google_connected=False,
                google_email_matched=None,
                account_status=None,
            ),
            llm.MemberScoringInput(
                ref="Member B",
                features={},
                raw_github=None,
                github_events=None,
                raw_google_docs=None,
                meeting=None,
                github_connected=False,
                google_connected=False,
                google_email_matched=None,
                account_status=None,
            ),
        ],
    )

    output = llm._to_output(parsed, group_input, "gemini-test")

    assert output.members[0].score == 1.0
    assert output.members[1].score == 0.0
    assert output.members[0].confidence == 1.0
    assert output.members[1].confidence == 0.5
    assert output.model_version == "gemini-test"


def test_to_output_raises_when_member_missing_from_response():
    parsed = llm._GroupResult(
        members=[llm._MemberResult(ref="Member A", score=0.7, reasoning="Only A")],
        group_observations="Missing B",
    )
    group_input = llm.GroupScoringInput(
        assignment_description="Test",
        member_count=2,
        group_totals={},
        members=[
            llm.MemberScoringInput(
                ref="Member A",
                features={},
                raw_github=None,
                github_events=None,
                raw_google_docs=None,
                meeting=None,
                github_connected=False,
                google_connected=False,
                google_email_matched=None,
                account_status=None,
            ),
            llm.MemberScoringInput(
                ref="Member B",
                features={},
                raw_github=None,
                github_events=None,
                raw_google_docs=None,
                meeting=None,
                github_connected=False,
                google_connected=False,
                google_email_matched=None,
                account_status=None,
            ),
        ],
    )

    with pytest.raises(llm.LLMScoringError, match="missing results"):
        llm._to_output(parsed, group_input, "gemini-test")


@pytest.mark.asyncio
async def test_score_group_returns_empty_output_without_members(monkeypatch):
    monkeypatch.setattr(llm, "is_llm_scoring_available", lambda: True)

    output = await llm.score_group(
        llm.GroupScoringInput(
            assignment_description="Test",
            member_count=0,
            group_totals={},
            members=[],
        )
    )

    assert output.members == []
    assert output.model_version == ""


