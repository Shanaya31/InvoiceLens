"""Session-partitioned in-memory record store with TTL and capacity limits.

IMPORTANT SECURITY NOTICE:
Session IDs are ephemeral isolation tokens, NOT cryptographic authentication.
They separate concurrent visitors in an MVP / demo environment to prevent cross-visitor
visibility, but they do NOT authenticate identity or provide access control.
Real financial documents and sensitive invoices must never be uploaded to a public demo.
"""
from __future__ import annotations

import builtins
import re
import threading
import time

from fastapi import Depends, HTTPException, Request

from app.config import Settings, get_settings
from app.schemas.document import DocumentRecord

SESSION_HEADER = "X-Session-ID"
DEFAULT_SESSION_ID = "default"
SESSION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{8,128}$")


class SessionLimitError(Exception):
    """Raised when a session reaches its maximum stored record capacity."""


class SessionStoreView:
    """Scoped view of the DocumentStore for a single session ID."""

    def __init__(self, root_store: DocumentStore, session_id: str) -> None:
        self._root = root_store
        self._session_id = session_id

    @property
    def session_id(self) -> str:
        return self._session_id

    def add(self, record: DocumentRecord) -> None:
        self._root.add_to_session(self._session_id, record)

    put = add

    def get(self, document_id: str) -> DocumentRecord | None:
        return self._root.get_from_session(self._session_id, document_id)

    def list(self) -> builtins.list[DocumentRecord]:
        return self._root.list_session(self._session_id)

    def delete(self, document_id: str) -> bool:
        return self._root.delete_from_session(self._session_id, document_id)

    def update(self, record: DocumentRecord) -> bool:
        return self._root.update_in_session(self._session_id, record)

    def clear(self) -> None:
        self._root.clear_session(self._session_id)


class DocumentStore:
    """Thread-safe, session-partitioned in-memory store with TTL and LRU bounds."""

    def __init__(
        self,
        ttl_seconds: float = 3600.0,
        max_sessions: int = 100,
        max_records_per_session: int = 50,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_sessions = max_sessions
        self._max_records_per_session = max_records_per_session
        self._sessions: dict[str, dict[str, DocumentRecord]] = {}
        self._last_accessed: dict[str, float] = {}
        self._lock = threading.Lock()

    def configure(
        self,
        ttl_seconds: float | None = None,
        max_sessions: int | None = None,
        max_records_per_session: int | None = None,
    ) -> None:
        with self._lock:
            if ttl_seconds is not None:
                self._ttl_seconds = ttl_seconds
            if max_sessions is not None:
                self._max_sessions = max_sessions
            if max_records_per_session is not None:
                self._max_records_per_session = max_records_per_session

    def _evict_lru_locked(self, target_count: int) -> None:
        """Evict least-recently-accessed sessions until session count <= target_count."""
        # Remove orphaned session data and stale access metadata.
        orphaned = [sid for sid in self._sessions if sid not in self._last_accessed]
        for sid in orphaned:
            self._sessions.pop(sid, None)
            self._last_accessed.pop(sid, None)

        if len(self._sessions) <= target_count:
            return

        sorted_by_lru = sorted(self._last_accessed.items(), key=lambda item: item[1])
        excess_count = len(self._sessions) - target_count
        for sid, _ in sorted_by_lru[:excess_count]:
            self._sessions.pop(sid, None)
            self._last_accessed.pop(sid, None)

    def _cleanup_locked(self, now: float) -> None:
        """Evict expired sessions and enforce max_sessions capacity (must be called with _lock)."""
        # 1. Evict expired sessions
        expired = [
            sid
            for sid, last in self._last_accessed.items()
            if now - last > self._ttl_seconds
        ]
        for sid in expired:
            self._sessions.pop(sid, None)
            self._last_accessed.pop(sid, None)

        # 2. If still over capacity, evict least-recently-accessed sessions
        self._evict_lru_locked(self._max_sessions)

    def _touch_locked(self, session_id: str, now: float) -> None:
        self._last_accessed[session_id] = now

    def add_to_session(self, session_id: str, record: DocumentRecord) -> None:
        now = time.monotonic()
        with self._lock:
            self._cleanup_locked(now)

            # Check document limit BEFORE mutating self._sessions to prevent orphaned sessions
            existing_records = self._sessions.get(session_id)
            is_new_doc = existing_records is None or record.document_id not in existing_records
            current_count = len(existing_records) if existing_records is not None else 0
            if is_new_doc and current_count >= self._max_records_per_session:
                raise SessionLimitError(
                    f"Session document limit reached (maximum {self._max_records_per_session} documents)."
                )

            # If this is a new session, ensure capacity does not exceed max_sessions
            if session_id not in self._sessions and len(self._sessions) >= self._max_sessions:
                self._evict_lru_locked(self._max_sessions - 1)

            session_records = self._sessions.setdefault(session_id, {})
            session_records[record.document_id] = record
            self._touch_locked(session_id, now)

    def update_in_session(self, session_id: str, record: DocumentRecord) -> bool:
        """Atomically update an existing document in session.

        Returns True if the document existed and was updated.
        Returns False if the session does not exist, was evicted, or does not contain this document.
        Never resurrects deleted documents or evicted sessions.
        """
        now = time.monotonic()
        with self._lock:
            self._cleanup_locked(now)
            session_records = self._sessions.get(session_id)
            if session_records is None or record.document_id not in session_records:
                return False
            session_records[record.document_id] = record
            self._touch_locked(session_id, now)
            return True

    def get_from_session(self, session_id: str, document_id: str) -> DocumentRecord | None:
        now = time.monotonic()
        with self._lock:
            self._cleanup_locked(now)
            records = self._sessions.get(session_id)
            if records is None:
                return None
            record = records.get(document_id)
            if record is not None:
                self._touch_locked(session_id, now)
            return record

    def list_session(self, session_id: str) -> builtins.list[DocumentRecord]:
        now = time.monotonic()
        with self._lock:
            self._cleanup_locked(now)
            records = self._sessions.get(session_id)
            if records is None:
                return []
            self._touch_locked(session_id, now)
            return list(records.values())

    def delete_from_session(self, session_id: str, document_id: str) -> bool:
        now = time.monotonic()
        with self._lock:
            self._cleanup_locked(now)
            records = self._sessions.get(session_id)
            if records is None:
                return False
            existed = records.pop(document_id, None) is not None
            if existed:
                self._touch_locked(session_id, now)
            return existed

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)
            self._last_accessed.pop(session_id, None)

    def clear_all(self) -> None:
        """Clear all sessions (primarily for test cleanup)."""
        with self._lock:
            self._sessions.clear()
            self._last_accessed.clear()

    def get_session(self, session_id: str) -> SessionStoreView:
        return SessionStoreView(self, session_id)

    # Backward-compatible convenience methods for tests/callers targeting default session
    def add(self, record: DocumentRecord) -> None:
        self.add_to_session(DEFAULT_SESSION_ID, record)

    put = add

    def get(self, document_id: str) -> DocumentRecord | None:
        return self.get_from_session(DEFAULT_SESSION_ID, document_id)

    def list(self) -> builtins.list[DocumentRecord]:
        return self.list_session(DEFAULT_SESSION_ID)

    def delete(self, document_id: str) -> bool:
        return self.delete_from_session(DEFAULT_SESSION_ID, document_id)

    def update(self, record: DocumentRecord) -> bool:
        return self.update_in_session(DEFAULT_SESSION_ID, record)

    def clear(self) -> None:
        self.clear_session(DEFAULT_SESSION_ID)


_store = DocumentStore()


def get_root_store() -> DocumentStore:
    """Access the process-wide root store instance."""
    return _store


def validate_session_id(session_id: str) -> str:
    """Ensure session_id conforms to safe token rules without path traversal or odd chars."""
    cleaned = session_id.strip()
    if not SESSION_ID_PATTERN.match(cleaned):
        raise HTTPException(status_code=400, detail="Invalid session ID.")
    return cleaned


def get_session_id(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> str:
    """Extract and validate the session identifier strictly from the X-Session-ID header.
    
    In hosted / production mode, missing session IDs are rejected with 400.
    In local development / test mode, missing session IDs fall back to 'default'.
    """
    raw_id = request.headers.get(SESSION_HEADER)
    if not raw_id or not raw_id.strip():
        if settings.session_id_required:
            raise HTTPException(status_code=400, detail="X-Session-ID header is required.")
        return DEFAULT_SESSION_ID

    return validate_session_id(raw_id)


def get_store(
    session_id: str = Depends(get_session_id),
    settings: Settings = Depends(get_settings),
) -> SessionStoreView:
    """FastAPI dependency returning a session-scoped store view."""
    # Ensure root store limits match current runtime settings
    _store.configure(
        ttl_seconds=settings.session_ttl_seconds,
        max_sessions=settings.max_sessions,
        max_records_per_session=settings.max_records_per_session,
    )
    if not isinstance(session_id, str):
        session_id = DEFAULT_SESSION_ID
    return _store.get_session(session_id)
