from datetime import datetime
from typing import ClassVar
from uuid import uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import TurnStatus


class Turn(Base):
    """A single persona utterance in one round of a debate."""

    __tablename__ = "turns"
    # See Persona: don't warn when the DB FK cascade beats the ORM's DELETE.
    __mapper_args__: ClassVar[dict] = {"confirm_deleted_rows": False}

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid4().hex)
    debate_id: Mapped[str] = mapped_column(
        String, ForeignKey("debates.id", ondelete="CASCADE"), nullable=False
    )
    persona_id: Mapped[str] = mapped_column(
        String, ForeignKey("personas.id", ondelete="CASCADE"), nullable=False
    )
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[TurnStatus] = mapped_column(
        Enum(TurnStatus, native_enum=False, length=16),
        default=TurnStatus.OK,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    debate: Mapped["Debate"] = relationship(back_populates="turns")  # noqa: F821
