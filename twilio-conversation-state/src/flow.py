"""
Call state engine — the part of the bot that is NOT the LLM.

The engine owns what the call has collected, what it still needs, and what
happens next. The LLM never decides the order of questions or whether a value
is "good enough"; it only interprets what the caller said by calling one of
three tools:

- record_answer(field, value)  -> caller gave (or corrected) a value
- confirm_readback()           -> caller said "yes" to a readback
- reject_readback(field?)      -> caller said "no" without a new value

Each turn the engine produces a Directive: either an exact line to `say`
(readbacks, the summary, the goodbye — things we never want paraphrased) or
an `instruction` for the LLM ("ask for the last name"). Everything here is
pure Python with no I/O, so it is easy to unit test and easy to replay.

Steps, in order:

    first_name -> last_name -> email -> phone -> review -> complete

Readbacks are formatted for TTS by src/speech.py: the engine knows each value's
type, so an email is spelled like an email and a phone number like a phone number.

A field step is finished when the field is "collected" (no confirmation
needed) or "confirmed" (spelled back and the caller said yes).
"""

import re
from dataclasses import asdict, dataclass, field
from typing import Callable, Literal

from src.speech import (
    format_email_for_speech,
    format_phone_for_speech,
    last_four_for_speech,
    normalize_phone,
    say_email,
    spell_chars,
)

FieldStatus = Literal["empty", "collected", "confirmed"]

EMAIL_REGEX = re.compile(
    r"^[a-z0-9][a-z0-9._%+\-]*@(?:[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?\.)+[a-z]{2,}$"
)
NAME_REGEX = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ '\-.]{0,49}$")

# ---------------------------------------------------------------------------
# Validation (phone validation lives in src/speech.py next to its formatter)
# ---------------------------------------------------------------------------

def normalize_name(value: str) -> str:
    value = " ".join(value.strip().split())
    if not NAME_REGEX.match(value):
        raise ValueError(f"'{value}' does not look like a name")
    # Fix ALL CAPS / all lower STT output, but leave "McDonald" alone.
    if value.isupper() or value.islower():
        value = "-".join(part.capitalize() for part in value.split("-"))
        value = " ".join(part[:1].upper() + part[1:] for part in value.split(" "))
    return value


def normalize_email(value: str) -> str:
    value = value.strip().lower().replace(" ", "")
    if not EMAIL_REGEX.match(value):
        raise ValueError(f"'{value}' is not a valid email address")
    return value


# ---------------------------------------------------------------------------
# Field definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    question: str
    normalize: Callable[[str], str]
    # When set, the value is read back and must be confirmed before moving on.
    readback: Callable[[str], str] | None = None
    # Hint the LLM gets after a failed attempt.
    retry_hint: str = ""


# The caller agreed to use the number they're calling from.
CALLER_ID = "caller_id"

FIELDS: list[FieldSpec] = [
    FieldSpec(
        key="first_name",
        label="first name",
        question="Can I start with your first name?",
        normalize=normalize_name,
    ),
    FieldSpec(
        key="last_name",
        label="last name",
        question="Thanks! And what's your last name?",
        normalize=normalize_name,
        readback=lambda v: f"I have your last name as {spell_chars(v)}. Is that right?",
        retry_hint="Ask them to spell their last name letter by letter.",
    ),
    FieldSpec(
        key="email",
        label="email address",
        question="Great. What's the best email address to reach you?",
        normalize=normalize_email,
        readback=lambda v: f"Let me make sure I have that right. {format_email_for_speech(v)}. Is that correct?",
        retry_hint="Ask them to spell the part before the 'at' sign slowly.",
    ),
    FieldSpec(
        key="phone",
        label="phone number",
        question="And what's the best phone number to reach you?",
        normalize=normalize_phone,
        readback=lambda v: f"I have your phone number as {format_phone_for_speech(v)}. Is that right?",
        retry_hint="Ask them to say the number slowly, including the area code.",
    ),
]
FIELD_BY_KEY = {f.key: f for f in FIELDS}

STEPS = [f.key for f in FIELDS] + ["review", "complete"]
STEP_LABELS = {**{f.key: f.label.capitalize() for f in FIELDS}, "review": "Review", "complete": "Complete"}


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@dataclass
class FieldState:
    value: str | None = None
    status: FieldStatus = "empty"
    attempts: int = 0  # how many values the caller has given for this field
    rejections: int = 0  # how many readbacks the caller said were wrong


@dataclass
class PendingReadback:
    target: str  # a field key, or "summary"
    turn: int  # turn on which the readback was spoken


@dataclass
class ToolOutcome:
    ok: bool
    message: str  # returned to the LLM as the tool result

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class Directive:
    step: str
    # Exact line the engine speaks itself (no LLM).
    say: str | None = None
    # Guidance for the LLM when it writes the reply.
    instruction: str = ""
    # True once the engine has nothing left to collect: speak `say`, then hang up.
    end_call: bool = False


@dataclass
class CallFlow:
    fields: dict[str, FieldState] = field(default_factory=lambda: {f.key: FieldState() for f in FIELDS})
    summary_confirmed: bool = False
    pending: PendingReadback | None = None
    turn: int = 0
    visited: list[str] = field(default_factory=lambda: [STEPS[0]])
    # The caller ID, if it's a valid number. Offered as the phone number so most
    # callers can just say "yes".
    caller_number: str | None = None

    @classmethod
    def for_caller(cls, from_number: str | None) -> "CallFlow":
        flow = cls()
        try:
            flow.caller_number = normalize_phone(from_number or "")
        except ValueError:
            flow.caller_number = None  # withheld, SIP, or a test call
        return flow

    # -- derived -----------------------------------------------------------

    def _field_done(self, spec: FieldSpec) -> bool:
        state = self.fields[spec.key]
        if spec.readback:
            return state.status == "confirmed"
        return state.status in ("collected", "confirmed")

    @property
    def step(self) -> str:
        for spec in FIELDS:
            if not self._field_done(spec):
                return spec.key
        return "complete" if self.summary_confirmed else "review"

    @property
    def completed(self) -> bool:
        return self.step == "complete"

    def summary_line(self) -> str:
        # Each value was already spelled back and confirmed, so the summary uses
        # natural forms: "grace at relayhawk dot com", not the letter-by-letter spelling.
        values = {key: state.value for key, state in self.fields.items()}
        return (
            f"Okay {values['first_name']}, here's what I have. {values['first_name']} {values['last_name']}. "
            f"Email, {say_email(values['email'] or '')}. "
            f"Phone, {format_phone_for_speech(values['phone'] or '')}. Did I get everything right?"
        )

    def directive(self) -> Directive:
        """What should happen on the agent's next utterance."""
        step = self.step

        if step == "complete":
            first = self.fields["first_name"].value or "there"
            return Directive(
                step=step,
                say=f"Perfect, you're all set, {first}. Thanks for calling, and have a great day!",
                end_call=True,
            )

        if step == "review":
            if self.pending and self.pending.target == "summary":
                return Directive(step=step, instruction=(
                    "You just read the caller a summary of their details. If they confirm it is all "
                    "correct, call confirm_readback. If they give a corrected value, call record_answer "
                    "with it. If they say something is wrong without the new value, call reject_readback "
                    "with the field they mean."
                ))
            return Directive(step=step, say=self.summary_line())

        spec = FIELD_BY_KEY[step]
        state = self.fields[step]

        if state.status == "collected" and spec.readback:
            if self.pending and self.pending.target == step:
                return Directive(step=step, instruction=(
                    f"You just read back the caller's {spec.label} as '{state.value}'. If they confirm "
                    "(yes, correct, that's right), call confirm_readback. If they give a correction or "
                    "spell it differently, call record_answer with the corrected value. If they only say "
                    "it is wrong, call reject_readback."
                ))
            assert state.value is not None
            return Directive(step=step, say=spec.readback(state.value))

        hint = spec.retry_hint if (state.attempts or state.rejections) and spec.retry_hint else ""
        if step == "phone" and self.caller_number and not state.rejections:
            return Directive(step=step, instruction=(
                "Ask whether the number they're calling from is the best one to reach them. Say something "
                f"close to: \"Is the number you're calling from, ending in {last_four_for_speech(self.caller_number)}, "
                "the best one to reach you?\" If they say yes, call record_answer with field 'phone' and value "
                f"'{CALLER_ID}'. If they give a different number, record that number instead. {hint}"
            ).strip())
        return Directive(step=step, instruction=(
            f"Ask the caller for their {spec.label}. Say something close to: \"{spec.question}\" "
            f"{hint} One or two short sentences. You may briefly acknowledge what they just said first."
        ).strip())

    # -- turn bookkeeping --------------------------------------------------

    def begin_turn(self) -> None:
        self.turn += 1

    def mark_said(self, directive: Directive) -> None:
        """The agent spoke a directive's `say` line — remember what we're waiting on."""
        if directive.say is None or directive.end_call:
            return
        target = "summary" if directive.step == "review" else directive.step
        self.pending = PendingReadback(target=target, turn=self.turn)

    def _track_step(self) -> None:
        if self.visited[-1] != self.step:
            self.visited.append(self.step)

    # -- tools -------------------------------------------------------------

    def record_answer(self, field_key: str, value: str) -> ToolOutcome:
        spec = FIELD_BY_KEY.get(field_key)
        if spec is None:
            return ToolOutcome(False, f"Unknown field '{field_key}'. Valid fields: {', '.join(FIELD_BY_KEY)}.")
        state = self.fields[field_key]
        state.attempts += 1
        from_caller_id = field_key == "phone" and value.strip().lower() == CALLER_ID
        if from_caller_id:
            if not self.caller_number:
                return ToolOutcome(False, "There is no caller ID on this call. Ask for their phone number.")
            value = self.caller_number
        try:
            normalized = spec.normalize(value)
        except ValueError as exc:
            return ToolOutcome(False, f"{exc}. Ask the caller to repeat their {spec.label}.")

        changed = normalized != state.value
        state.value = normalized
        if from_caller_id:
            # They just agreed to "the number ending in 0123", so no second readback.
            state.status = "confirmed"
        elif state.status != "confirmed" or changed:
            state.status = "collected"
        # Any change invalidates an earlier summary confirmation and the pending readback.
        if changed:
            self.summary_confirmed = False
        if self.pending and (self.pending.target in (field_key, "summary") or changed):
            self.pending = None
        self._track_step()
        return ToolOutcome(True, f"Recorded {spec.label}: {normalized}.")

    def confirm_readback(self) -> ToolOutcome:
        if self.pending is None:
            return ToolOutcome(False, "Nothing has been read back yet, so there is nothing to confirm.")
        if self.pending.turn >= self.turn:
            # The LLM tried to confirm in the same turn the readback was spoken.
            return ToolOutcome(False, "The caller has not answered the readback yet. Wait for them.")
        target = self.pending.target
        self.pending = None
        if target == "summary":
            self.summary_confirmed = True
            self._track_step()
            return ToolOutcome(True, "The caller confirmed all of their details.")
        self.fields[target].status = "confirmed"
        self._track_step()
        return ToolOutcome(True, f"The caller confirmed their {FIELD_BY_KEY[target].label}.")

    def reject_readback(self, field_key: str | None = None) -> ToolOutcome:
        if self.pending is None:
            return ToolOutcome(False, "Nothing has been read back yet, so there is nothing to reject.")
        target = self.pending.target
        if target == "summary":
            if not field_key or field_key not in FIELD_BY_KEY:
                return ToolOutcome(False, "Ask the caller which detail is wrong, then call reject_readback with that field.")
            target = field_key
        self.pending = None
        state = self.fields[target]
        state.value = None
        state.status = "empty"
        state.rejections += 1
        self.summary_confirmed = False
        self._track_step()
        return ToolOutcome(True, f"Cleared the {FIELD_BY_KEY[target].label}. Ask for it again.")

    # -- serialization -----------------------------------------------------

    def snapshot(self) -> dict[str, object]:
        directive = self.directive()
        return {
            "step": self.step,
            "turn": self.turn,
            "steps": [{"key": s, "label": STEP_LABELS[s]} for s in STEPS],
            "visited": list(self.visited),
            "fields": [
                {
                    "key": spec.key,
                    "label": spec.label,
                    "needs_confirmation": spec.readback is not None,
                    **asdict(self.fields[spec.key]),
                }
                for spec in FIELDS
            ],
            "summary_confirmed": self.summary_confirmed,
            "pending_readback": self.pending.target if self.pending else None,
            "completed": self.completed,
            "caller_number": self.caller_number,
            "directive": asdict(directive),
        }

    def collected_values(self) -> dict[str, str | None]:
        return {key: state.value for key, state in self.fields.items()}
