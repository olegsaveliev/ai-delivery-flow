"""ORM models. Importing this package populates ``Base.metadata`` fully."""

from app.models.debate import Debate
from app.models.enums import Archetype, DebateStatus, TurnStatus
from app.models.persona import Persona
from app.models.turn import Turn
from app.models.verdict import Verdict

__all__ = [
    "Archetype",
    "Debate",
    "DebateStatus",
    "Persona",
    "Turn",
    "TurnStatus",
    "Verdict",
]
