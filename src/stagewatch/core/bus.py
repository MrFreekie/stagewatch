"""Tiny in-process pub/sub. Subscribers are plain callables; a slow or
failing subscriber must never block or break the publisher."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Callable

log = logging.getLogger(__name__)

Handler = Callable[[str, Any], None]


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        """Subscribe to a topic ("*" receives everything). Returns an unsubscribe."""
        self._subs[topic].append(handler)

        def unsubscribe() -> None:
            if handler in self._subs[topic]:
                self._subs[topic].remove(handler)

        return unsubscribe

    def publish(self, topic: str, payload: Any = None) -> None:
        for handler in list(self._subs.get(topic, ())) + list(self._subs.get("*", ())):
            try:
                handler(topic, payload)
            except Exception:  # noqa: BLE001 - isolate subscribers from each other
                log.exception("Event handler failed for topic %s", topic)
