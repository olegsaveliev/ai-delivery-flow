"""Debate HTTP endpoints (TICKET-6 / KAN-9).

Wires the headless engine behind REST: ``POST`` creates a debate and runs it to
completion (orchestrator → judge → persist), then ``GET``/``GET list``/``DELETE``
map onto the repository CRUD. Watch-only (DEC-004): no mid-debate mutation endpoint.
No auth / no ownership scoping (DEC-005); persistence is SQLite (DEC-008).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.repositories import debates as repo
from app.schemas.debate import DebateCreateRequest, DebateOut, DebateSummary
from app.services.judge import JudgeError, judge, persist_verdict
from app.services.llm import LLMService, get_llm_service
from app.services.orchestrator import run_debate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/debates", tags=["debates"])


@router.post("", response_model=DebateOut, status_code=201)
async def create_debate(
    request: DebateCreateRequest,
    session: Session = Depends(get_db),
    llm: LLMService = Depends(get_llm_service),
) -> DebateOut:
    """Create a debate, run it to completion, synthesize + persist a verdict, return it.

    A judge failure after its one repair retry keeps the completed transcript and
    returns ``verdict: null`` (status stays COMPLETED) rather than discarding the work.
    """
    debate = repo.create_debate(session, decision=request.decision, context=request.context)
    session.commit()

    completed = await run_debate(session, debate, llm=llm)
    debate_id = completed.id
    try:
        verdict = judge(completed, llm=llm)
        persist_verdict(session, completed, verdict)
        session.commit()
    except JudgeError:
        logger.warning(
            "judge failed for debate=%s; returning transcript with null verdict", debate_id
        )

    # ``set_verdict`` works by debate_id and doesn't touch the in-session ``Debate.verdict``
    # relationship (loaded as None during run_debate's reload). Expire so the reload re-reads
    # it (mirrors the expire in tests/test_judge.py's persist round-trip).
    session.expire_all()
    reloaded = repo.get_debate(session, debate_id)
    return DebateOut.model_validate(reloaded)


@router.get("", response_model=list[DebateSummary])
def list_debates(session: Session = Depends(get_db)) -> list[DebateSummary]:
    """History list, newest first (DEC-005: no ownership filter)."""
    return repo.list_debates(session)


@router.get("/{debate_id}", response_model=DebateOut)
def get_debate(debate_id: str, session: Session = Depends(get_db)) -> DebateOut:
    """Full transcript + verdict for one debate; 404 if it doesn't exist."""
    debate = repo.get_debate(session, debate_id)
    if debate is None:
        raise HTTPException(status_code=404, detail="debate not found")
    return DebateOut.model_validate(debate)


@router.delete("/{debate_id}", status_code=204)
def delete_debate(debate_id: str, session: Session = Depends(get_db)) -> None:
    """Delete a debate and cascade to its children (privacy); 404 if missing."""
    if not repo.delete_debate(session, debate_id):
        raise HTTPException(status_code=404, detail="debate not found")
