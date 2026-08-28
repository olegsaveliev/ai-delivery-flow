from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import DebateStatus


class Debate(Base):
    """A single decision debate: 3 personas × N rounds + one judge verdict."""

    __tablename__ = "debates"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid4().hex)
    decision: Mapped[str] = mapped_column(String, nullable=False)
    context: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[DebateStatus] = mapped_column(
        Enum(DebateStatus, native_enum=False, length=16),
        default=DebateStatus.PENDING,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    # passive_deletes=True lets the DB-level FK ``ON DELETE CASCADE`` remove
    # children so the ORM doesn't also emit redundant DELETEs (belt-and-
    # suspenders cascade without the double-delete warning).
    personas: Mapped[list["Persona"]] = relationship(
        back_populates="debate",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    turns: Mapped[list["Turn"]] = relationship(
        back_populates="debate",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    verdict: Mapped["Verdict | None"] = relationship(
        back_populates="debate",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )


# Imported for the type-only forward references above and to register the
# related tables on Base.metadata when this module is imported directly.
from app.models.persona import Persona
from app.models.turn import Turn
from app.models.verdict import Verdict
