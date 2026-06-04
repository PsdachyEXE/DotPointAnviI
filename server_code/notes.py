import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""Note CRUD, search, settings get/update server module.

Slice 1 (§10 step 1) implements the user_settings surface only:
  get_settings, update_settings (+ _get_or_create_settings, _settings_row_to_dict).

Note CRUD (create_note, update_note, delete_note, toggle_pin, search_notes) and
the EDITABLE_FIELDS_NOTE import land in the Notes slice (§10 step 6).

See IMPLEMENTATION_SPEC.md section 2 (server_code/notes.py) and section 1
(user_settings table + uniqueness mandate).
"""

import anvil.server
import datetime
from zoneinfo import ZoneInfo

from ._auth import _require_user

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


# --- TEMPORARY DEBUG: probe server-side access to the users table.
# Remove once the signup PermissionDenied is diagnosed.
@anvil.server.callable
def _debug_users_access() -> str:
    try:
        n = len(list(app_tables.users.search()))
        return "OK: server searched users, count=%d" % n
    except Exception as e:
        return "%s: %s" % (type(e).__name__, e)
