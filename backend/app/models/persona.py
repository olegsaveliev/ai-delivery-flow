from typing import ClassVar
from uuid import uuid4

from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import Archetype


class Persona(Base):
    """One of the three archetypes arguing a per-decision stance."""

    __tablename__ = "personas"
    # The DB-level FK ON DELETE CASCADE may remove this row before the ORM's own
    # cascade DELETE runs; don't warn when that pre-emptive delete matches 0 rows.
    __mapper_args__: ClassVar[dict] = {"confirm_deleted_rows": False}

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid4().hex)
    debate_id: Mapped[str] = mapped_column(
        String, ForeignKey("debates.id", ondelete="CASCADE"), nullable=False
    )
    archetype: Mapped[Archetype] = mapped_column(
        Enum(Archetype, native_enum=False, length=16), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    stance: Mapped[str] = mapped_column(String, nullable=False)

    debate: Mapped["Debate"] = relationship(back_populates="personas")  # noqa: F821
