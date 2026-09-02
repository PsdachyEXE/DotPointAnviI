import anvil.secrets
import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""Auth helpers shared across all server modules.

Defines: _require_user(), _own_or_raise(row, user).

THE SECURITY MODEL. NFR03 is "every Data Table query scoped to current_user —
no row may be returned to a user other than the row's owner", and FR20 puts the
sign-in itself on the Anvil Users service. This 32-line file is what enforces
NFR03 across every other server module, in two rules:

  1. RESOLVE THE USER FIRST. A callable's first statement is
     `user = _require_user()`, before it looks at an argument, a table or a
     helper. It has to be first because the row it returns is the value every
     query is then scoped on, so there is nothing to scope with until it has
     run — and because a check made after the work has already been done is not
     a check. 22 of the app's 24 @anvil.server.callable functions do exactly
     this.

     Honestly: it is not literally EVERY callable. notes.create_account and
     notes.sign_in_with_email are deliberately pre-authentication — they are
     how a person becomes a logged-in user in the first place, so demanding one
     would make signing up impossible. Those two are the only exemptions, and
     they take no row ids and read no user-owned table.

  2. SCOPE, THEN RE-CHECK. Every search carries `user=user`, and every path
     that fetches a row BY ID additionally calls _own_or_raise() on the row it
     got back, before reading from it or writing to it. _own_or_raise's own
     docstring explains why the scoped search is not enough on its own.

Both rules live on the server because Anvil publishes every
@anvil.server.callable over the network by name: the browser is not a
boundary, and neither is the UI that normally supplies the arguments. Anything
the client sends can be anything at all.

See IMPLEMENTATION_SPEC.md section 2 (server_code/_auth.py) and section 5
(Authentication).
"""

import anvil.users


def _require_user() -> "tables.Row":
    """The signed-in user's row, or AuthenticationFailed if nobody is signed in.

    The first line of every callable that touches user data (NFR03). It answers
    two questions at once, which is why it is one call and not two: "is anyone
    logged in?" and "which row do I scope the queries on?".

    Takes no parameters — the identity comes from the Anvil session cookie the
    request arrived with, not from anything the client can name. That is the
    point: a caller cannot ask to be somebody else.

    Returns:
        The `users` table Row for the current session. It is used as a VALUE,
        not as an id: `app_tables.assessments.search(user=user)` matches on the
        row link, and _own_or_raise below compares against the same object.

    Raises:
        anvil.users.AuthenticationFailed — nobody is signed in, or the session
        has expired. Never caught server-side; the client lets it reach
        Main's router, which shows the login screen.
    """
    # allow_remembered=True honours the "remember me" cookie from an earlier
    # visit. Without it a student who ticked that box would still be bounced to
    # the login screen on every page load, because Anvil only counts a
    # remembered session as logged in when it is asked to.
    user = anvil.users.get_user(allow_remembered=True)
    # get_user() RETURNS None for "not logged in" rather than raising, and
    # converting that into an exception is the entire reason this wrapper
    # exists. A None that was quietly passed on would not stop anything: it
    # would flow into search(user=None) and into the ownership test below as
    # though it were an identity, and a check that compares against nobody is
    # not a check. Failing loudly here means a callable cannot forget.
    if user is None:
        raise anvil.users.AuthenticationFailed("Login required")
    return user


def _own_or_raise(row: "tables.Row", user: "tables.Row") -> None:
    """Refuse the operation unless `row` belongs to `user` (NFR03).

    Called on every by-id path, straight after the row is fetched and before
    anything is read off it or written to it: assessments.get_assessment,
    update_assessment and delete_assessment; notes.update_note, delete_note and
    toggle_pin; and the linked-note check inside
    assessments._validate_assessment_payload,
    which has to prove the student owns the notes they are linking as well as
    the assessment they are linking them to (FR12).

    WHY THIS EXISTS WHEN THE QUERIES ARE ALREADY SCOPED. A list query is scoped
    — search(user=user) can only return the caller's own rows. But a by-id path
    never goes through a query: get_by_id() reaches straight into the table and
    will happily hand back somebody else's record. The id is not a secret
    either; it comes from the client, so it can be a stale one from a browser
    tab left open under another account, one kept out of an export file, or one
    typed by someone calling the server function directly. Scoping the search
    protects the list. This protects the row.

    Args:
        row: an `assessments` or `notes` Row — both carry a `user` column
            linking to the owning `users` row. It must already have been
            fetched; this function looks nothing up.
        user: the Row returned by _require_user() for THIS request. Never an id
            or an email string; the comparison below is Row against Row.

    Returns:
        None. It is used for its exception, so callers invoke it as a bare
        statement rather than assigning the result.

    Raises:
        PermissionError — `row` belongs to somebody else, or to nobody. The
        wording is identical in both cases and says nothing about what the row
        is, so probing with guessed ids cannot be used to learn which ones
        exist (the same reasoning behind FR20's generic "login failed").
    """
    # `!=` rather than `is not`: two reads of the same database row are not
    # guaranteed to be the same Python object, but Anvil Row objects compare
    # equal when they point at the same row, so equality is the correct test.
    #
    # A row whose `user` cell is empty fails this too — a pre-migration row, or
    # one inserted through the Data Tables console. That is the right answer:
    # an unowned row is still not this student's to edit or delete.
    if row['user'] != user:
        raise PermissionError("Not your record")
