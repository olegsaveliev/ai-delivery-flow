import asyncio
from types import SimpleNamespace

from app.core.guardrails import DebateGuardrails
from app.models.enums import Archetype, DebateStatus, TurnStatus
from app.repositories import debates as repo
from app.services.llm import TurnResult
from app.services.orchestrator import run_debate


class StubLLM:
    """Synchronous ``run_turn`` stub — no network. Records calls; can force skips."""

    def __init__(self, skip=frozenset()):
        self.calls = []
        self.skip = set(skip)  # set of (round_number, persona_index) to force skipped

    def run_turn(
        self,
        *,
        tier,
        messages,
        guardrails,
        round_number,
        persona_index,
        max_tokens=1024,
        system=None,
    ) -> TurnResult:
        # run_turn runs inside asyncio.to_thread; list.append is atomic under the GIL.
        self.calls.append(
            SimpleNamespace(
                round=round_number,
                index=persona_index,
                tier=tier,
                text=messages[0].content,
                system=system,
            )
        )
        if (round_number, persona_index) in self.skip:
            return TurnResult(status="skipped", model="stub", reason="forced")
        return TurnResult(
            status="ok",
            model="stub",
            text=f"r{round_number}p{persona_index}",
            tokens_in=1,
            tokens_out=1,
        )


def make_guardrails(**over) -> DebateGuardrails:
    defaults = {"max_personas": 3, "max_rounds": 2, "max_tokens_per_debate": 100_000}
    defaults.update(over)
    return DebateGuardrails(**defaults)


def test_two_rounds_three_personas_all_ok(session):
    debate = repo.create_debate(session, decision="Adopt SQLite?", context="dev tooling")
    stub = StubLLM()

    result = asyncio.run(run_debate(session, debate, llm=stub, guardrails=make_guardrails()))

    # Three fixed personas assigned & persisted (DEC-001/002).
    assert len(result.personas) == 3
    assert {p.archetype for p in result.personas} == {
        Archetype.ADVOCATE,
        Archetype.SKEPTIC,
        Archetype.PRAGMATIST,
    }

    # 2 rounds × 3 personas = 6 turns, in transcript order (DEC-003).
    assert len(result.turns) == 6
    assert [t.round for t in result.turns] == [1, 1, 1, 2, 2, 2]
    assert all(t.status == TurnStatus.OK for t in result.turns)
    assert result.status == DebateStatus.COMPLETED

    # Every persona spoke in both rounds; personas run on the Sonnet tier (DEC-007).
    assert len(stub.calls) == 6
    assert {(c.round, c.index) for c in stub.calls} == {(r, i) for r in (1, 2) for i in (0, 1, 2)}
    assert all(c.tier == "personas" for c in stub.calls)


def test_round_two_sees_round_one_transcript(session):
    debate = repo.create_debate(session, decision="Adopt SQLite?")
    stub = StubLLM()

    asyncio.run(run_debate(session, debate, llm=stub, guardrails=make_guardrails()))

    round_one = [c for c in stub.calls if c.round == 1]
    round_two = [c for c in stub.calls if c.round == 2]

    # Round 1 is the opening round: no prior discussion threaded in.
    assert all("No prior discussion" in c.text for c in round_one)
    assert all("[Round 1]" not in c.text for c in round_one)

    # Round 2 receives the round-1 transcript (sequential rounds, transcript-aware).
    assert all("[Round 1]" in c.text for c in round_two)
    for c in round_two:
        assert any(f"r1p{i}" in c.text for i in (0, 1, 2))


def test_forced_skip_still_completes(session):
    debate = repo.create_debate(session, decision="Adopt SQLite?")
    stub = StubLLM(skip={(1, 1)})  # The Skeptic drops out in round 1.

    result = asyncio.run(run_debate(session, debate, llm=stub, guardrails=make_guardrails()))

    # A dropped turn is recorded as skipped and the debate still completes.
    assert result.status == DebateStatus.COMPLETED
    assert len(result.turns) == 6

    skipped = [t for t in result.turns if t.status == TurnStatus.SKIPPED]
    assert len(skipped) == 1
    assert skipped[0].content == ""  # content is NOT nullable
    assert skipped[0].round == 1

    # Round 2 still ran for all three personas — a skip doesn't remove a persona.
    round_two = [c for c in stub.calls if c.round == 2]
    assert {c.index for c in round_two} == {0, 1, 2}


def test_all_skips_in_a_round_still_completes(session):
    debate = repo.create_debate(session, decision="Adopt SQLite?")
    stub = StubLLM(skip={(1, 0), (1, 1), (1, 2)})  # whole first round drops out

    result = asyncio.run(run_debate(session, debate, llm=stub, guardrails=make_guardrails()))

    assert result.status == DebateStatus.COMPLETED
    assert len(result.turns) == 6
    round_one_turns = [t for t in result.turns if t.round == 1]
    assert all(t.status == TurnStatus.SKIPPED and t.content == "" for t in round_one_turns)

    # With no OK turns in round 1, round 2 sees an empty transcript (opening-round prompt).
    round_two = [c for c in stub.calls if c.round == 2]
    assert all("No prior discussion" in c.text for c in round_two)
