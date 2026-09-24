/**
 * Debate stream event contract (EPIC-B B-T1 / KAN-17, DEC-003/004).
 * Mirrors backend/app/schemas/events.py — keep the two in lockstep.
 * Server → client only (DEC-004 watch-only): there are no inbound event types.
 * Field names are snake_case to match the JSON on the wire.
 */

export type Archetype = "advocate" | "skeptic" | "pragmatist";

export type TurnStatus = "ok" | "skipped";

export type DebateStatus = "pending" | "running" | "completed" | "failed";

/** Mirrors PersonaOut. UI color is derived from `archetype` (DEC-006), not sent. */
export interface PersonaOut {
  id: string;
  archetype: Archetype;
  name: string;
  stance: string;
}

export interface Case {
  option: string;
  argument: string;
}

/** After the 3 personas are persisted. */
export interface PersonasAssignedEvent {
  type: "personas_assigned";
  debate_id: string;
  personas: PersonaOut[];
}

/** A persona turn begins. */
export interface TurnStartedEvent {
  type: "turn_started";
  round: number;
  persona_id: string;
}

/** A streamed text chunk of an in-progress turn. */
export interface TurnDeltaEvent {
  type: "turn_delta";
  round: number;
  persona_id: string;
  text: string;
}

/**
 * Turn persisted — full, authoritative content.
 * `content` REPLACES any text accumulated from `turn_delta` events for this
 * (round, persona_id): discard the accumulated text and show `content`. When
 * `status === "skipped"`, `content` is "" and any partial delta text must be discarded
 * (a turn can stream some deltas and still end skipped).
 */
export interface TurnCompletedEvent {
  type: "turn_completed";
  round: number;
  persona_id: string;
  turn_id: string;
  status: TurnStatus;
  content: string;
}

/** All 3 turns of the round have landed (twice per debate, DEC-003). */
export interface RoundCompletedEvent {
  type: "round_completed";
  round: number;
}

/** Judge verdict persisted (single, non-streamed event). */
export interface VerdictEvent {
  type: "verdict";
  recommendation: string;
  cases: Case[];
  tradeoffs: string[];
}

/** Terminal: success. Always "completed"; every failure ends with an `error` event. */
export interface DoneEvent {
  type: "done";
  debate_id: string;
  status: "completed";
}

/**
 * Terminal: failure. `code` is an open string; expected codes ("judge_failed", "timeout",
 * "internal") are provisional pending Proposed DEC-012.
 */
export interface ErrorEvent {
  type: "error";
  code: string;
  message: string;
}

export type DebateEvent =
  | PersonasAssignedEvent
  | TurnStartedEvent
  | TurnDeltaEvent
  | TurnCompletedEvent
  | RoundCompletedEvent
  | VerdictEvent
  | DoneEvent
  | ErrorEvent;

export type DebateEventType = DebateEvent["type"];

export type DebateEventOf<T extends DebateEventType> = Extract<DebateEvent, { type: T }>;

export type TerminalDebateEvent = DoneEvent | ErrorEvent;

/** A parsed SSE frame: `seq` is the frame's `id:` (MessageEvent.lastEventId), not part of data. */
export interface SequencedDebateEvent {
  seq: number;
  event: DebateEvent;
}
