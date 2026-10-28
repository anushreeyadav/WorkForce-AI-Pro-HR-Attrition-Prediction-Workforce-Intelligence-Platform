"""Opaque, signed cursors for incremental activity updates.

Format: base64url(JSON payload) + "." + base64url(HMAC-SHA256(payload)[:16]). The key is derived from
JWT_SECRET_KEY with a fixed purpose label, so rotating that secret invalidates outstanding cursors (clients
reload the feed). Payload: version, position key (recorded_at in UTC microseconds, source rank, record id,
action rank), the user it was issued to and a fingerprint of the scope and filters. The position contains only
identifiers the same user could already see; the signature stops clients from forging or editing it, and the
user/filter binding stops a cursor from being replayed under a different scope.
"""

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

from app.core.config import Settings

VERSION = 1
MAX_LENGTH = 512
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
Key = tuple[datetime, int, int, int]


class CursorError(ValueError):
    """Malformed, tampered or mismatched cursor (422)."""


class CursorExpired(CursorError):
    """Valid but older than ACTIVITY_UPDATES_CURSOR_MAX_AGE_HOURS (410)."""


def _signing_key(settings: Settings) -> bytes:
    return hashlib.sha256(b"workforce-activity-cursor:v1:" + settings.jwt_secret_key.get_secret_value().encode()).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def fingerprint(department_id: int | None, categories: set[str], kinds: set[str] | None, priorities: set[str] | None) -> str:
    canonical = json.dumps({"d": department_id, "c": sorted(categories), "k": sorted(kinds or []),
                            "p": sorted(priorities or [])}, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def encode(key: Key, user_id: int, filters: str, settings: Settings) -> str:
    recorded_at, source_rank, record_id, action_rank = key
    micros = (recorded_at - _EPOCH) // timedelta(microseconds=1)
    payload = json.dumps({"v": VERSION, "t": micros, "s": source_rank, "i": record_id, "a": action_rank,
                          "u": user_id, "f": filters}, separators=(",", ":"), sort_keys=True).encode()
    signature = hmac.new(_signing_key(settings), payload, hashlib.sha256).digest()[:16]
    return f"{_b64(payload)}.{_b64(signature)}"


def decode(cursor: str, user_id: int, filters: str, now: datetime, settings: Settings) -> Key:
    if not cursor or len(cursor) > MAX_LENGTH or cursor.count(".") != 1:
        raise CursorError("Invalid cursor")
    body, signature = cursor.split(".")
    try:
        payload, given = _unb64(body), _unb64(signature)
    except (ValueError, TypeError):
        raise CursorError("Invalid cursor") from None
    expected = hmac.new(_signing_key(settings), payload, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(given, expected):
        raise CursorError("Invalid cursor")
    try:
        data = json.loads(payload)
        fields = [data[k] for k in ("v", "t", "s", "i", "a", "u", "f")]
    except (ValueError, KeyError, TypeError):
        raise CursorError("Invalid cursor") from None
    version, micros, source_rank, record_id, action_rank, owner, issued_filters = fields
    if version != VERSION or not all(type(v) is int for v in (micros, source_rank, record_id, action_rank, owner)):
        raise CursorError("Invalid cursor")
    if owner != user_id or issued_filters != filters:
        raise CursorError("This cursor was issued for a different user, scope or filter set; reload the feed")
    try:
        recorded_at = _EPOCH + timedelta(microseconds=micros)
    except OverflowError:
        raise CursorError("Invalid cursor") from None
    if recorded_at > now + timedelta(minutes=5):
        raise CursorError("Invalid cursor")
    if recorded_at < now - timedelta(hours=settings.activity_updates_cursor_max_age_hours):
        raise CursorExpired("Cursor expired; reload the feed to get a new cursor")
    return recorded_at, source_rank, record_id, action_rank
