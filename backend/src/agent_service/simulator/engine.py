"""Deterministic ARS state and real-time virtual playback."""

import asyncio
import time
from dataclasses import dataclass, field

SCENARIO_TEXTS = {
    "welcome": (
        "안녕하세요. 가상 병원 안내입니다. 예약 안내는 일 번, 기타 안내는 사 번을 눌러 주세요."
    ),
    "other": "기타 안내입니다. 병원 위치는 일 번, 진료 시간은 이 번을 눌러 주세요.",
    "hours": "가상 병원의 진료 시간입니다. 평일 오전 아홉 시부터 오후 여섯 시까지 진료합니다. "
    "점심시간은 낮 열두 시부터 오후 한 시까지입니다. 토요일과 일요일, 공휴일은 쉽니다. "
    "이상으로 안내를 마칩니다.",
    "invalid": "잘못 누르셨습니다. 안내를 다시 들으신 뒤 번호를 눌러 주세요.",
}
NUMBER = "01000000001"


@dataclass
class Line:
    id: str
    destination: str
    created: float = field(default_factory=time.monotonic)
    status: str = "active"
    stage: str = "welcome"
    revision: int = 0
    digits: list[str] = field(default_factory=list)
    invalid_digits: int = 0
    repeats: int = 0
    hours_delivered: bool = False
    end_reason: str = ""
    connected: bool = False
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    journal: list[dict] = field(default_factory=list)
    player: object | None = None
    replay_invalid: bool = False

    def record(self, kind, **data):
        self.journal.append(
            {"at_ms": round((time.monotonic() - self.created) * 1000), "event": kind, **data}
        )
        self.journal[:] = self.journal[-300:]

    def digit(self, digit):
        if self.status != "active":
            return
        self.digits.append(digit)
        self.digits[:] = self.digits[-100:]
        target = {("welcome", "4"): "other", ("other", "2"): "hours"}.get((self.stage, digit))
        self.record("dtmf", digit=digit, stage=self.stage, accepted=target is not None)
        if target:
            self.stage = target
            self.replay_invalid = False
            self.repeats = 0
        else:
            self.invalid_digits += 1
            self.replay_invalid = True
        self.revision += 1

    def end(self, reason):
        if self.status == "active":
            self.status = "completed"
            self.end_reason = reason
            self.record("ended", reason=reason)
            self.closed.set()

    def snapshot(self):
        return {
            "id": self.id,
            "destination": self.destination,
            "status": self.status,
            "scenario": "clinic-hours",
            "stage": self.stage,
            "digits": self.digits,
            "invalid_digits": self.invalid_digits,
            "hours_delivered": self.hours_delivered,
            "end_reason": self.end_reason,
            "events": self.journal,
            "played_bytes": getattr(self.player, "played_bytes", 0),
            "clears": getattr(self.player, "clears", 0),
        }


class Playback:
    """ACK only audio which has elapsed on a virtual speaker; clear cancels pending audio."""

    def __init__(self, send):
        self.send = send
        self.queue = asyncio.Queue(maxsize=1000)
        self.interrupted = asyncio.Event()
        self.epoch = 0
        self.played_bytes = 0
        self.clears = 0

    def media(self, raw):
        self.queue.put_nowait(("media", len(raw)))

    def mark(self, name):
        self.queue.put_nowait(("mark", name))

    def clear(self):
        self.epoch += 1
        self.clears += 1
        self.interrupted.set()
        while not self.queue.empty():
            self.queue.get_nowait()

    async def run(self):
        while True:
            kind, value = await self.queue.get()
            epoch = self.epoch
            if kind == "mark":
                await self.send({"event": "mark", "mark": {"name": value}})
                continue
            self.interrupted.clear()
            try:
                await asyncio.wait_for(self.interrupted.wait(), value / 8000)
            except TimeoutError:
                if epoch == self.epoch:
                    self.played_bytes += value
