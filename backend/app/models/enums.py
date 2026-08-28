from enum import Enum


class Archetype(str, Enum):
    """The three fixed debate personas (DEC-001)."""

    ADVOCATE = "advocate"
    SKEPTIC = "skeptic"
    PRAGMATIST = "pragmatist"


class TurnStatus(str, Enum):
    """Outcome of a single persona turn."""

    OK = "ok"
    SKIPPED = "skipped"


class DebateStatus(str, Enum):
    """Lifecycle state of a debate."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
