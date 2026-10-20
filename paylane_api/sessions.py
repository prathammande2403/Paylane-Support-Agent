"""
paylane_api/sessions.py
Hardened in-memory session management with TTL expiration, max session cap,
and per-IP sliding window rate limiting.
"""

from __future__ import annotations
import time
import uuid
from typing import Dict, List, Optional, Tuple
from paylane_api.service import SessionState

SESSION_TTL_SECONDS = 1800  # 30 minutes
SESSION_CAP = 200
RATE_LIMIT_REQUESTS = 20  # requests per minute
RATE_LIMIT_WINDOW_SECONDS = 60


class SessionStore:
    def __init__(self):
        # session_id -> (SessionState, last_accessed_timestamp)
        self._sessions: Dict[str, Tuple[SessionState, float]] = {}
        # ip_address -> list of request timestamps
        self._ip_rate_limits: Dict[str, List[float]] = {}

    def _cleanup_expired(self):
        now = time.time()
        expired_keys = [
            sid for sid, (_, last_active) in self._sessions.items()
            if now - last_active > SESSION_TTL_SECONDS
        ]
        for sid in expired_keys:
            self._sessions.pop(sid, None)

        # Evict LRU if still over cap
        if len(self._sessions) > SESSION_CAP:
            sorted_by_lru = sorted(self._sessions.items(), key=lambda item: item[1][1])
            excess = len(self._sessions) - SESSION_CAP
            for sid, _ in sorted_by_lru[:excess]:
                self._sessions.pop(sid, None)

    def get_or_create(self, session_id: Optional[str] = None) -> SessionState:
        self._cleanup_expired()
        now = time.time()
        if session_id and session_id in self._sessions:
            session, _ = self._sessions[session_id]
            self._sessions[session_id] = (session, now)
            return session

        new_id = session_id or f"sess_{uuid.uuid4().hex[:12]}"
        new_session = SessionState(session_id=new_id)
        self._sessions[new_id] = (new_session, now)
        return new_session

    def get(self, session_id: str) -> Optional[SessionState]:
        self._cleanup_expired()
        if session_id in self._sessions:
            session, _ = self._sessions[session_id]
            self._sessions[session_id] = (session, time.time())
            return session
        return None

    def reset(self, session_id: Optional[str] = None) -> SessionState:
        self._cleanup_expired()
        new_id = session_id or f"sess_{uuid.uuid4().hex[:12]}"
        new_session = SessionState(session_id=new_id)
        self._sessions[new_id] = (new_session, time.time())
        return new_session

    def check_rate_limit(self, client_ip: str) -> bool:
        """Returns True if request is allowed, False if rate limited."""
        now = time.time()
        window_start = now - RATE_LIMIT_WINDOW_SECONDS
        timestamps = self._ip_rate_limits.setdefault(client_ip, [])
        # Keep only timestamps within window
        valid_timestamps = [t for t in timestamps if t > window_start]
        self._ip_rate_limits[client_ip] = valid_timestamps

        if len(valid_timestamps) >= RATE_LIMIT_REQUESTS:
            return False

        valid_timestamps.append(now)
        return True

    def clear(self):
        """Resets all sessions and rate limits (useful for testing)."""
        self._sessions.clear()
        self._ip_rate_limits.clear()


# Global singleton session store
session_store = SessionStore()
