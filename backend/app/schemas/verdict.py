"""Judge verdict contract (TICKET-5 / KAN-8, DEC-007).

The schema-validated shape the judge produces and the API/UI consume. Mirrors
``models.verdict.Verdict`` (the ORM row): ``cases`` is a list of
``{"option", "argument"}`` and ``tradeoffs`` a list of strings. In-memory only;
persistence maps this onto the ORM via ``repositories.debates.set_verdict``.
"""

from typing import Annotated

from pydantic import BaseModel, Field

# A non-empty trade-off string; guards element emptiness, not just list length.
Tradeoff = Annotated[str, Field(min_length=1)]


class Case(BaseModel):
    """The strongest case for one option (DEC-007)."""

    option: str = Field(..., min_length=1, description="The option this case argues for")
    argument: str = Field(..., min_length=1, description="Strongest argument for the option")


class Verdict(BaseModel):
    """Judge's synthesized outcome — schema-validated (DEC-007).

    Field shapes match ``models.verdict.Verdict``: one ``Case`` per option debated
    and a list of key trade-offs. Extra keys are ignored (not forbidden) so a
    harmless extra field from the model doesn't force a needless repair retry.
    """

    recommendation: str = Field(..., min_length=1, description="Balanced recommendation")
    cases: list[Case] = Field(..., min_length=1, description="Strongest case per option")
    tradeoffs: list[Tradeoff] = Field(..., min_length=1, description="Key trade-offs")
