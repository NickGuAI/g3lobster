"""Shared utility classes for g3lobster."""

from __future__ import annotations

from collections import OrderedDict
from typing import Iterator


class BoundedSet:
    """A set with a maximum size that evicts the oldest entries (FIFO) when full.

    Drop-in replacement for ``set`` supporting ``in``, ``add``, and ``len``.
    Designed to cap memory-resident dedup caches in long-running bridges.
    """

    def __init__(self, max_size: int = 10_000) -> None:
        if max_size < 1:
            raise ValueError("max_size must be >= 1")
        self._max_size = max_size
        self._store: OrderedDict[str, None] = OrderedDict()

    def __contains__(self, item: object) -> bool:
        return item in self._store

    def add(self, item: str) -> None:
        if item in self._store:
            return
        self._store[item] = None
        if len(self._store) > self._max_size:
            self._store.popitem(last=False)

    def __len__(self) -> int:
        return len(self._store)

    def __iter__(self) -> Iterator[str]:
        return iter(self._store)
