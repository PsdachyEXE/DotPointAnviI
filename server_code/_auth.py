import anvil.secrets
import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
import anvil.users
import anvil.server
"""Auth helpers shared across all server modules.

Defines: _require_user(), _own_or_raise(row, user).

Implementation pending - see IMPLEMENTATION_SPEC.md section 2 (server_code/_auth.py)
and section 5 (Authentication).
"""

# DEV_EMAIL is for development/testing only.
# Production user emails must use user['email'] from the Users table, never this secret.

pass


class AccountLockedError(anvil.users.AuthenticationFailed):
    # Raised when a user account is locked due to too many failed login attempts.
    pass


@anvil.server.callable
def login_with_lockout(email, password):
    # Log in a user, enforcing the lockout policy in IMPLEMENTATION_SPEC.md section 5.
    #
    # Behaviour:
    #   - If a user row exists for email and n_password_failures >= 10, raise
    #     AccountLockedError. Lockout is permanent until the password is reset via
    #     the email link from anvil.users.send_password_reset_email.
    #   - Otherwise, delegate to anvil.users.login_with_email, which lets Anvil's
    #     built-in counter increment n_password_failures on failure.
    #   - On successful login, reset n_password_failures to 0 explicitly. This also
    #     recovers accounts whose password has just been reset, since Anvil does
    #     not expose a documented post-reset hook (see spec gap).
    #   - Failures against non-existent emails do not increment any counter and
    #     do not contribute to lockout.
    user_row = app_tables.users.get(email=email)
    if user_row is not None and (user_row['n_password_failures'] or 0) >= 10:
        raise AccountLockedError(
            "This account is locked due to too many failed login attempts. "
            "Please reset your password via the email link to regain access."
        )
    user = anvil.users.login_with_email(email, password)
    if user is not None:
        try:
            user['n_password_failures'] = 0
        except Exception:
            pass
    return user


@anvil.server.callable
def request_password_reset(email):
    # Send a password-reset email to email.
    #
    # Wraps anvil.users.send_password_reset_email. Anvil does not expose a
    # documented post-reset hook, so we cannot reset n_password_failures the
    # moment the password is changed. Instead, login_with_lockout resets the
    # counter on the next successful login. This gap is documented in
    # IMPLEMENTATION_SPEC.md section 5 (Authentication).
    return anvil.users.send_password_reset_email(email)
