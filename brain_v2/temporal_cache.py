"""In-session temporal cache per Windsurf synthesis §4.5.

Tracks every memory ID retrieved during the current session. When
ranking retrieval results, apply a recency boost to any memory already
in the cache. Cache is discarded at session end. No schema change.

Also implements the link-traversal boost (§4.9 spatial locality): when
`recall()` fetches a memory, its linked memories get temporary score
elevation for the remainder of the session.

Keyed by session_id. In-process dict — single server process.
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class SessionCache:
    session_id: str
    retrieved: dict[tuple[str, int], float] = field(default_factory=dict)  # (kind, id) -> boost score
    first_seen: dict[tuple[str, int], float] = field(default_factory=dict)
    link_boosts: dict[tuple[str, int], float] = field(default_factory=dict)

    def mark_retrieved(self, kind: str, memory_id: int, boost: float = 1.0) -> None:
        key = (kind, memory_id)
        self.retrieved[key] = boost
        self.first_seen.setdefault(key, time.time())

    def boost_for(self, kind: str, memory_id: int) -> float:
        """Returns the multiplicative boost to apply to this memory's
        retrieval score this session. 0.0 if not cached."""
        key = (kind, memory_id)
        temporal = self.retrieved.get(key, 0.0)
        spatial = self.link_boosts.get(key, 0.0)
        return temporal + spatial

    def apply_link_boost(self, linked: list[tuple[str, int]], weight: float = 0.5) -> None:
        for key in linked:
            self.link_boosts[key] = max(self.link_boosts.get(key, 0.0), weight)


_caches: dict[str, SessionCache] = defaultdict(lambda: SessionCache(session_id=""))


def get(session_id: str) -> SessionCache:
    if session_id not in _caches:
        _caches[session_id] = SessionCache(session_id=session_id)
    return _caches[session_id]


def reset(session_id: str) -> None:
    _caches.pop(session_id, None)
