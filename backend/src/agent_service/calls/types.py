from dataclasses import dataclass

TERMINAL = frozenset({"completed", "failed", "busy", "no_answer", "canceled"})


class SessionConflict(Exception):
    """A request conflicts with the persisted call lifecycle."""


class DialRejected(Exception):
    """The carrier explicitly rejected creation of the call."""


class DialUncertain(Exception):
    """The carrier might have created a call; never retry automatically."""


class ProviderFailure(Exception):
    """A sanitized error at an external provider boundary."""


@dataclass(frozen=True)
class CallSnapshot:
    external_id: str
    status: str
    from_number: str
    to_number: str
