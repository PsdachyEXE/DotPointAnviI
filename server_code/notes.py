import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""Note CRUD, search, settings get/update server module.

Settings surface (§10 step 1): get_settings, update_settings
(+ _get_or_create_settings, _settings_row_to_dict).

Note CRUD + search (§10 step 6): create_note, update_note, delete_note,
toggle_pin, search_notes (+ _note_row_to_dict, _validate_note_fields).
delete_note also unlinks the note from any of the user's assessments.

See IMPLEMENTATION_SPEC.md section 2 (server_code/notes.py) and section 1
(user_settings table + uniqueness mandate).
"""

import anvil.server
import anvil.users
import datetime
from zoneinfo import ZoneInfo

from ._auth import _require_user, _own_or_raise
from ._constants import EDITABLE_FIELDS_NOTE

# Defaults for a freshly created user_settings row (spec §1).
_SETTINGS_DEFAULTS = {
    'theme': 'dark',
    'default_reminder_days': [7, 2],
    'notifications_enabled': True,
    'school_year': None,
    'school_terms': [],
    'timezone': 'Australia/Melbourne',  # Pending Decision 2 (A)
}

# Whitelist of client-updatable settings keys (spec §2 update_settings).
_SETTINGS_FIELDS = (
    'theme', 'default_reminder_days', 'notifications_enabled',
    'school_year', 'school_terms', 'timezone',
)


# --- settings helpers ------------------------------------------------------

def _get_or_create_settings(user) -> "tables.Row":
    """Return the user's settings row, creating it with defaults if absent.

    This is the SOLE inserter into `user_settings` (spec §1 uniqueness mandate):
    no other code path may call `app_tables.user_settings.add_row(...)`. Anvil
    server modules have no read-then-write transactional isolation, so two
    concurrent first-logins for the same user could theoretically both insert;
    the risk is negligible for this single-user app and is accepted in §1.
    """
    row = app_tables.user_settings.get(user=user)
    if row is not None:
        return row
    return app_tables.user_settings.add_row(user=user, **_SETTINGS_DEFAULTS)


def _settings_row_to_dict(row) -> dict:
    """Plain-dict view of a settings row (no live Row object leaves the server)."""
    return {
        'theme': row['theme'] or 'dark',
        'default_reminder_days': row['default_reminder_days'] or [],
        'notifications_enabled': bool(row['notifications_enabled']),
        'school_year': row['school_year'],
        'school_terms': row['school_terms'] or [],
        'timezone': row['timezone'] or 'Australia/Melbourne',
    }


# --- settings callables ----------------------------------------------------

@anvil.server.callable
def get_settings() -> dict:
    """Return the current user's settings, lazily creating the row on first call."""
    user = _require_user()
    row = _get_or_create_settings(user)
    return _settings_row_to_dict(row)


@anvil.server.callable
def update_settings(fields: dict) -> dict:
    """Whitelist-filter, validate, and persist a settings patch."""
    user = _require_user()
    row = _get_or_create_settings(user)
    clean = {k: v for k, v in (fields or {}).items() if k in _SETTINGS_FIELDS}
    _validate_settings(clean)
    if clean:
        row.update(**clean)
    return _settings_row_to_dict(row)


# --- validation ------------------------------------------------------------

def _validate_settings(fields: dict) -> None:
    if 'theme' in fields and fields['theme'] not in ('light', 'dark'):
        raise ValueError("theme must be 'light' or 'dark'")

    if 'default_reminder_days' in fields:
        v = fields['default_reminder_days']
        if not isinstance(v, list) or not all(
            isinstance(d, int) and not isinstance(d, bool) and d > 0 for d in v
        ):
            raise ValueError("default_reminder_days must be a list of positive ints")

    if 'notifications_enabled' in fields and not isinstance(fields['notifications_enabled'], bool):
        raise ValueError("notifications_enabled must be a bool")

    if 'school_year' in fields and fields['school_year'] is not None:
        y = fields['school_year']
        if not isinstance(y, int) or isinstance(y, bool):
            raise ValueError("school_year must be an integer or None")

    if 'school_terms' in fields:
        _validate_school_terms(fields['school_terms'])

    if 'timezone' in fields:
        tz = fields['timezone']
        if not isinstance(tz, str):
            raise ValueError("timezone must be a string")
        try:
            ZoneInfo(tz)
        except Exception:
            raise ValueError("invalid timezone: %r" % tz)


def _validate_school_terms(terms) -> None:
    if not isinstance(terms, list):
        raise ValueError("school_terms must be a list")
    for t in terms:
        if not isinstance(t, dict):
            raise ValueError("each school term must be a dict")
        if not isinstance(t.get('term'), int) or isinstance(t.get('term'), bool):
            raise ValueError("school term 'term' must be an int")
        for key in ('start_date', 'end_date'):
            val = t.get(key)
            if not isinstance(val, str) or not _is_iso_date(val):
                raise ValueError("school term '%s' must be a 'YYYY-MM-DD' string" % key)


def _is_iso_date(s: str) -> bool:
    try:
        datetime.date.fromisoformat(s)
        return True
    except (ValueError, TypeError):
        return False


# --- note CRUD + search (spec section 2, step 6) ---------------------------

def _note_row_to_dict(row) -> dict:
    """Plain-dict view of a note row; timestamps as ISO strings, incl. 'id'."""
    def iso(d):
        return d.isoformat() if d is not None else None
    return {
        'id': row.get_id(),
        'title': row['title'],
        'content': row['content'] or '',
        'tags': row['tags'] or [],
        'is_pinned': bool(row['is_pinned']),
        'created_at': iso(row['created_at']),
        'updated_at': iso(row['updated_at']),
    }


def _validate_note_fields(fields: dict) -> dict:
    """Validate a note create/update patch; return a cleaned copy or raise."""
    out = dict(fields)

    if 'title' in out:
        title = out['title']
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title required")
        if len(title) > 200:
            raise ValueError("title too long (max 200)")
        out['title'] = title.strip()

    if 'content' in out:
        content = out['content']
        if content is None:
            out['content'] = ''
        elif not isinstance(content, str):
            raise ValueError("content must be text")

    if 'tags' in out:
        tags = out['tags'] or []
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise ValueError("tags must be a list of strings")
        # De-duplicate, preserving order, dropping blanks.
        seen, deduped = set(), []
        for t in tags:
            key = t.strip()
            if key and key.lower() not in seen:
                seen.add(key.lower())
                deduped.append(key)
        out['tags'] = deduped

    if 'is_pinned' in out and not isinstance(out['is_pinned'], bool):
        raise ValueError("is_pinned must be a bool")

    return out


@anvil.server.callable
def create_note(record: dict) -> str:
    """Create a note owned by the current user; return its row id (FR10)."""
    user = _require_user()
    record = record or {}
    clean = _validate_note_fields({
        'title': record.get('title'),
        'content': record.get('content') or '',
        'tags': record.get('tags') or [],
        'is_pinned': bool(record.get('is_pinned', False)),
    })
    now = datetime.datetime.now(datetime.timezone.utc)
    row = app_tables.notes.add_row(
        title=clean['title'], content=clean['content'], tags=clean['tags'],
        is_pinned=clean['is_pinned'], user=user, created_at=now, updated_at=now)
    return row.get_id()


@anvil.server.callable
def update_note(row_id: str, fields: dict) -> dict:
    """Whitelist-filter, validate and apply an edit to an owned note (FR10)."""
    user = _require_user()
    row = app_tables.notes.get_by_id(row_id)
    if row is None:
        raise ValueError("not found")
    _own_or_raise(row, user)
    clean = _validate_note_fields(
        {k: v for k, v in (fields or {}).items() if k in EDITABLE_FIELDS_NOTE})
    if clean:
        clean['updated_at'] = datetime.datetime.now(datetime.timezone.utc)
        row.update(**clean)
    return _note_row_to_dict(row)


@anvil.server.callable
def delete_note(row_id: str) -> bool:
    """Delete an owned note, first unlinking it from any of the user's assessments."""
    user = _require_user()
    row = app_tables.notes.get_by_id(row_id)
    if row is None:
        return False
    _own_or_raise(row, user)
    with tables.Transaction():
        for a in app_tables.assessments.search(user=user):
            linked = a['linked_note_ids'] or []
            if row_id in linked:
                a.update(linked_note_ids=[n for n in linked if n != row_id])
        row.delete()
    return True


@anvil.server.callable
def toggle_pin(row_id: str) -> bool:
    """Flip a note's pinned state; return the new value (FR10)."""
    user = _require_user()
    row = app_tables.notes.get_by_id(row_id)
    if row is None:
        raise ValueError("not found")
    _own_or_raise(row, user)
    new_value = not row['is_pinned']
    row.update(is_pinned=new_value,
               updated_at=datetime.datetime.now(datetime.timezone.utc))
    return new_value


@anvil.server.callable
def search_notes(query: str = None, tag: str = None, pinned_only: bool = False) -> list:
    """Return the user's notes (pinned-first, then recent) filtered by query/tag (FR11)."""
    user = _require_user()
    rows = list(app_tables.notes.search(user=user))

    def _sort_key(r):
        updated = r['updated_at']
        ts = updated.timestamp() if updated is not None else 0
        return (0 if r['is_pinned'] else 1, -ts)
    rows.sort(key=_sort_key)

    if query:
        needle = query.strip().lower()
        rows = [r for r in rows
                if needle in ((r['title'] or '') + ' ' + (r['content'] or '')).lower()]
    if tag:
        want = tag.strip().lower()
        rows = [r for r in rows
                if any(want == (t or '').lower() for t in (r['tags'] or []))]
    if pinned_only:
        rows = [r for r in rows if r['is_pinned']]

    return [_note_row_to_dict(r) for r in rows]


# --- custom auth (workaround) ----------------------------------------------
# Anvil's client-initiated signup_with_form / login_with_form raise
# "PermissionDenied: Cannot access this table from server code" on the users
# table (a Users-service<->table binding issue). Running the same operations
# from a trusted server-module callable uses this module's full users-table
# access, which sidesteps that path. Returns True on success; raises ValueError
# with a user-facing message otherwise.

@anvil.server.callable
def create_account(email: str, password: str) -> bool:
    email = (email or '').strip().lower()
    if not email or not password:
        raise ValueError("Email and password are required.")
    try:
        new_user = anvil.users.signup_with_email(email, password, remember=True)
    except anvil.users.UserExists:
        raise ValueError("An account with that email already exists — try signing in.")
    anvil.users.force_login(new_user)
    _get_or_create_settings(new_user)
    return True


@anvil.server.callable
def sign_in_with_email(email: str, password: str) -> bool:
    email = (email or '').strip().lower()
    if not email or not password:
        raise ValueError("Email and password are required.")
    try:
        user = anvil.users.login_with_email(email, password, remember=True)
    except anvil.users.AuthenticationFailed:
        raise ValueError("Incorrect email or password.")
    _get_or_create_settings(user)
    return True
