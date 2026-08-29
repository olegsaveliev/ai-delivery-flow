import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.models.enums import Archetype, TurnStatus
from app.repositories import debates as repo
from app.schemas.verdict import Verdict
from app.services.judge import JudgeSchemaError, judge, persist_verdict

VALID_JSON = json.dumps(
    {
        "recommendation": "Adopt SQLite for the MVP",
        "cases": [
            {"option": "SQLite", "argument": "Zero-config and fast enough for a single user."},
            {"option": "Postgres", "argument": "Scales further but adds operational cost now."},
        ],
        "tradeoffs": ["Simplicity vs future scale", "No concurrency vs zero setup"],
    }
)


class StubJudgeLLM:
    """Stub exposing ``complete`` — records calls and pops queued responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, *, tier, messages, system=None, max_tokens=2048):
        self.calls.append(SimpleNamespace(tier=tier, messages=messages, system=system))
        return self.responses.pop(0)


def _build_debate(session, *, decision="Adopt SQLite?", context="dev tooling"):
    """create -> 3 personas -> 2 rounds of OK turns. Returns the reloaded Debate."""
    debate = repo.create_debate(session, decision=decision, context=context)
    advocate = repo.add_persona(session, debate.id, Archetype.ADVOCATE, "The Advocate", "Seize it")
    skeptic = repo.add_persona(session, debate.id, Archetype.SKEPTIC, "The Skeptic", "Hold on")
    pragmatist = repo.add_persona(
        session, debate.id, Archetype.PRAGMATIST, "The Pragmatist", "It depends"
    )
    for round_no in (1, 2):
        for persona in (advocate, skeptic, pragmatist):
            repo.add_turn(
                session,
                debate.id,
                persona.id,
                round=round_no,
                content=f"{persona.name} argues in round {round_no}",
            )
    session.commit()
    return repo.get_debate(session, debate.id)


def test_valid_verdict_passes(session):
    debate = _build_debate(session)
    stub = StubJudgeLLM([VALID_JSON])

    verdict = judge(debate, llm=stub)

    assert isinstance(verdict, Verdict)
    assert verdict.recommendation == "Adopt SQLite for the MVP"
    assert len(verdict.cases) == 2
    assert verdict.tradeoffs
    assert len(stub.calls) == 1  # no repair needed


def test_non_json_then_valid_triggers_one_repair(session):
    debate = _build_debate(session)
    stub = StubJudgeLLM(["this is not json at all", VALID_JSON])

    verdict = judge(debate, llm=stub)

    assert isinstance(verdict, Verdict)
    assert len(stub.calls) == 2  # exactly one repair
    # The repair (2nd) call carries the bad output + a fix instruction.
    repair_text = stub.calls[1].messages[-1].content
    assert "not a valid verdict" in repair_text
    assert "this is not json at all" in repair_text


def test_schema_invalid_then_valid_triggers_one_repair(session):
    debate = _build_debate(session)
    # JSON-parseable but fails the schema (empty recommendation, missing cases/tradeoffs).
    stub = StubJudgeLLM(['{"recommendation": ""}', VALID_JSON])

    verdict = judge(debate, llm=stub)

    assert isinstance(verdict, Verdict)
    assert len(stub.calls) == 2


def test_empty_tradeoff_element_is_rejected():
    with pytest.raises(ValidationError):
        Verdict.model_validate(
            {
                "recommendation": "Adopt SQLite",
                "cases": [{"option": "SQLite", "argument": "simple"}],
                "tradeoffs": [""],  # empty element must fail, not just an empty list
            }
        )


def test_fenced_json_is_stripped_single_and_multiline(session):
    debate = _build_debate(session)
    single = f"```json {VALID_JSON}```"
    multi = f"```json\n{VALID_JSON}\n```"

    # Both fence styles parse on the first call — no needless repair.
    for fenced in (single, multi):
        stub = StubJudgeLLM([fenced])
        verdict = judge(debate, llm=stub)
        assert isinstance(verdict, Verdict)
        assert len(stub.calls) == 1


def test_malformed_twice_raises(session):
    debate = _build_debate(session)
    stub = StubJudgeLLM(["not json", "still not json"])

    with pytest.raises(JudgeSchemaError):
        judge(debate, llm=stub)

    assert len(stub.calls) == 2  # no third call after the single repair


def test_judge_uses_opus_tier(session):
    debate = _build_debate(session)
    stub = StubJudgeLLM(["not json", VALID_JSON])

    judge(debate, llm=stub)

    assert stub.calls  # sanity
    assert all(c.tier == "judge" for c in stub.calls)


def test_transcript_and_decision_in_prompt(session):
    debate = _build_debate(session, decision="Migrate to Postgres?")
    stub = StubJudgeLLM([VALID_JSON])

    judge(debate, llm=stub)

    user_message = stub.calls[0].messages[0].content
    assert "Migrate to Postgres?" in user_message
    assert "The Advocate argues in round 1" in user_message
    assert "[Round 2]" in user_message


def test_skipped_turns_excluded_from_prompt(session):
    debate = repo.create_debate(session, decision="Adopt SQLite?")
    persona = repo.add_persona(session, debate.id, Archetype.ADVOCATE, "The Advocate", "Seize it")
    repo.add_turn(session, debate.id, persona.id, round=1, content="I make the case.")
    repo.add_turn(session, debate.id, persona.id, round=2, content="", status=TurnStatus.SKIPPED)
    session.commit()
    reloaded = repo.get_debate(session, debate.id)
    stub = StubJudgeLLM([VALID_JSON])

    judge(reloaded, llm=stub)

    user_message = stub.calls[0].messages[0].content
    assert "I make the case." in user_message
    assert "[Round 2]" not in user_message  # skipped/empty turn excluded


def test_persist_verdict_round_trips(session):
    debate = _build_debate(session)
    verdict = Verdict.model_validate(json.loads(VALID_JSON))

    persist_verdict(session, debate, verdict)
    session.commit()
    # A real request reads in a fresh session; expire so get_debate re-loads.
    session.expire_all()

    reloaded = repo.get_debate(session, debate.id)
    assert reloaded.verdict is not None
    assert reloaded.verdict.debate_id == debate.id  # linkable to its transcript
    assert reloaded.verdict.recommendation == "Adopt SQLite for the MVP"
    assert reloaded.verdict.cases == [
        {"option": "SQLite", "argument": "Zero-config and fast enough for a single user."},
        {"option": "Postgres", "argument": "Scales further but adds operational cost now."},
    ]
    assert reloaded.verdict.tradeoffs == [
        "Simplicity vs future scale",
        "No concurrency vs zero setup",
    ]
