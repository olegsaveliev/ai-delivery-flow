from typing import ClassVar
from uuid import uuid4

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Verdict(Base):
    """The judge's synthesized outcome — one per debate (``unique`` FK)."""

    __tablename__ = "verdicts"
    # See Persona: don't warn when the DB FK cascade beats the ORM's DELETE.
    __mapper_args__: ClassVar[dict] = {"confirm_deleted_rows": False}

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid4().hex)
    debate_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("debates.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    recommendation: Mapped[str] = mapped_column(String, nullable=False)
    # cases: [{"option": str, "argument": str}] — shape validated in TICKET-5.
    cases: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    tradeoffs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    debate: Mapped["Debate"] = relationship(back_populates="verdict")  # noqa: F821
