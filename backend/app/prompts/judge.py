"""Judge prompt: system prompt + message builders (TICKET-5 / KAN-8, DEC-007).

The judge runs last on the full debate transcript (Opus tier, DEC-007) and must
return a JSON verdict matching ``schemas.verdict.Verdict``. We prompt-constrain the
JSON and validate it ourselves (rather than using native structured outputs) so the
DEC-007 *one repair retry* path is real and testable: a malformed response is caught
and repaired once. These builders assemble the initial transcript message and the
single repair message.
"""

from app.schemas.chat import ChatMessage

JUDGE_SYSTEM_PROMPT = (
    "You are an impartial judge synthesizing a multi-persona decision debate. You have "
    "read the full transcript in which fixed personas argued different positions. Weigh "
    "all sides fairly and produce a balanced verdict: the strongest case for each option, "
    "the key trade-offs, and a clear recommendation. Ground everything in the transcript — "
    "never invent facts.\n\n"
    "Respond with ONLY a single JSON object and nothing else — no prose, no explanation, "
    "no markdown code fences. The object must have exactly these fields:\n"
    '  "recommendation": string — the balanced recommendation.\n'
    '  "cases": array of objects, one per distinct option debated, each '
    '{"option": string, "argument": string} giving that option\'s strongest case.\n'
    '  "tradeoffs": array of strings — the key trade-offs to weigh.\n'
    "Every field is required; arrays must be non-empty."
)


def build_judge_messages(
    *,
    decision: str,
    context: str | None,
    transcript_lines: list[str],
) -> list[ChatMessage]:
    """Assemble the single user message that drives the judge synthesis.

    The system prompt (passed separately to the LLM) defines the role and the JSON
    contract; this builds the user turn: the decision (+ optional context), the
    rendered transcript, and a closing instruction to return the JSON verdict.
    """
    lines: list[str] = [f'Decision under debate: "{decision}"']
    if context and context.strip():
        lines.append(f"Context: {context.strip()}")

    lines.append("")
    if transcript_lines:
        lines.append("Debate transcript:")
        lines.extend(transcript_lines)
    else:
        lines.append("The debate produced no usable turns.")

    lines.append("")
    lines.append("Return the verdict as a single JSON object now.")
    return [ChatMessage(role="user", content="\n".join(lines))]


def build_repair_message(*, bad_output: str, error: str) -> ChatMessage:
    """Build the single repair turn quoting the invalid output and the error."""
    content = (
        "Your previous response was not a valid verdict.\n\n"
        f"Error: {error}\n\n"
        "Previous response:\n"
        f"{bad_output}\n\n"
        "Fix it and return ONLY the corrected JSON object — no prose, no code fences."
    )
    return ChatMessage(role="user", content=content)
