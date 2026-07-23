import anvil.secrets
import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""Auth helpers shared across all server modules.

Defines: _require_user(), _own_or_raise(row, user).

See IMPLEMENTATION_SPEC.md section 2 (server_code/_auth.py) and section 5
(Authentication). Row-level permission model: every @anvil.server.callable
calls _require_user() first; every update/delete calls _own_or_raise() after
fetching a row by id.
"""

import anvil.users


def _require_user() -> "tables.Row":
    """Return the logged-in user row, or raise AuthenticationFailed.

    Honours "remember me" sessions (allow_remembered=True).
    """
    user = anvil.users.get_user(allow_remembered=True)
    if user is None:
        raise anvil.users.AuthenticationFailed("Login required")
    return user


def _own_or_raise(row: "tables.Row", user: "tables.Row") -> None:
    """Raise PermissionError if `row` is not owned by `user`."""
    if row['user'] != user:
        raise PermissionError("Not your record")
