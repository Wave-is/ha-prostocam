"""A small parser of `text/event-stream` (WHATWG HTML, server-sent events)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SseEvent:
    """One dispatched event: name, joined data lines and the id it carried."""

    event: str
    data: str
    id: str | None


class SseParser:
    """Feed it lines (with or without the line break); it returns whole events."""

    def __init__(self) -> None:
        """Start with an empty buffer."""
        self.last_event_id: str | None = None
        self.retry_ms: int | None = None
        self._event = ""
        self._data: list[str] = []
        self._id: str | None = None
        self._has_fields = False

    def feed(self, line: str) -> SseEvent | None:
        """Take one line; a blank line dispatches the buffered event."""
        line = line.rstrip("\r\n")
        if not line:
            return self._dispatch()
        if line.startswith(":"):
            return None  # a comment keeps proxies awake
        field, colon, value = line.partition(":")
        if colon and value.startswith(" "):
            value = value[1:]
        if field == "event":
            self._event = value
            self._has_fields = True
        elif field == "data":
            self._data.append(value)
            self._has_fields = True
        elif field == "id":
            if "\0" not in value:
                self._id = value
                self.last_event_id = value or None
                self._has_fields = True
        elif field == "retry":
            if value.isdigit():
                self.retry_ms = int(value)
        return None

    def _dispatch(self) -> SseEvent | None:
        if not self._has_fields or (not self._data and not self._event):
            self._reset()
            return None
        event = SseEvent(self._event or "message", "\n".join(self._data), self._id)
        self._reset()
        return event

    def _reset(self) -> None:
        self._event = ""
        self._data = []
        self._id = None
        self._has_fields = False
