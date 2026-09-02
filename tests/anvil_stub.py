"""Offline test harness: a fake `anvil` package, installed into `sys.modules`.

WHY THIS EXISTS
---------------
`server_code/*.py` can only be executed inside Anvil's own server runtime, because
every module opens with `import anvil.tables` and reaches the database through the
`app_tables` singleton. That makes the validation logic — the code SAT criterion 7.3
is marked on — impossible to exercise without deploying.

This module fakes just enough of the Anvil API for the real, unmodified server
modules to be imported and called on a normal Python interpreter, backed by
in-memory dictionaries instead of PostgreSQL. Nothing in `server_code/` is altered
or mocked out: the tests drive the same functions the live app calls.

WHAT IS FAKED (and the fidelity that matters for the tests)
-----------------------------------------------------------
* `FakeRow`      — Anvil's Row: `row['col']` reads, `row['col'] = v` writes,
                   `.get_id()`, `.update(**kw)`, `.delete()`. Reading a column the
                   table was never given raises `KeyError`, exactly as Anvil does,
                   so the `_row_value()` missing-column guard is genuinely tested.
* `FakeTable`    — `.add_row()`, `.get()`, `.get_by_id()`, `.search()`, `.delete()`.
                   `.get()` raises when MORE than one row matches, which is the real
                   Anvil behaviour the settings-row code depends on.
* `q.*`          — query operators as small predicate objects, evaluated by
                   `FakeTable.search()` the way the server expects.
* `tables.Transaction` — a context manager that SNAPSHOTS every table on entry and
                   restores it if the body raises, so "all-or-nothing" claims about
                   bulk insert and import can actually be falsified by a test.
* `anvil.users`  — `get_user()` returns whichever user the test set with
                   `set_current_user()`; `signup_with_email` / `login_with_email`
                   behave like the real ones including `UserExists` / `AuthenticationFailed`.
* `anvil.email`  — `send()` records the message instead of sending it, so the
                   reminder dispatcher can be asserted on without a mail server.
* `anvil.server` — `callable` / `background_task` are identity decorators.
* `anvil.secrets`— `get_secret()` reads a dict the test controls.

Install the stub by importing this module BEFORE any `server_code` import:

    from tests.anvil_stub import install, reset, set_current_user
    install()

See docs/TESTING.md section 1.
"""

import sys
import types
import copy


# ---------------------------------------------------------------------------
# Exceptions — the real Anvil names, so `except` clauses in server_code match.
# ---------------------------------------------------------------------------

class AnvilWrappedError(Exception):
    """Base for the fake Anvil exception family."""


class TableError(AnvilWrappedError):
    """Raised for row/table misuse (e.g. .get() matching more than one row)."""


class NoSuchColumnError(AnvilWrappedError):
    """Raised when a column is read that the table does not define."""


class UserExists(AnvilWrappedError):
    """signup_with_email() called with an email that is already registered."""


class AuthenticationFailed(AnvilWrappedError):
    """login_with_email() called with credentials that do not match."""


class SendFailure(AnvilWrappedError):
    """anvil.email.send() could not deliver. Tests raise this deliberately."""


# ---------------------------------------------------------------------------
# Query operators. Each is a tiny predicate object; FakeTable.search() calls
# .matches(value). This mirrors how Anvil composes q.any_of / q.between etc.
# ---------------------------------------------------------------------------

class _QueryOp(object):
    """Base class: a column filter that can say whether a stored value matches."""

    def matches(self, value):
        raise NotImplementedError


class _AnyOf(_QueryOp):
    """q.any_of(a, b, ...) — true when the stored value is one of the options."""

    def __init__(self, options):
        self._options = list(options)

    def matches(self, value):
        return value in self._options


class _Not(_QueryOp):
    """q.not_(x) — true when the stored value differs from x."""

    def __init__(self, excluded):
        self._excluded = excluded

    def matches(self, value):
        return value != self._excluded


class _Between(_QueryOp):
    """q.between(lo, hi) — inclusive lower bound, exclusive upper (Anvil default)."""

    def __init__(self, min_value, max_value, min_inclusive=True, max_inclusive=False):
        self._min = min_value
        self._max = max_value
        self._min_inclusive = min_inclusive
        self._max_inclusive = max_inclusive

    def matches(self, value):
        if value is None:
            return False
        if self._min is not None:
            if value < self._min or (value == self._min and not self._min_inclusive):
                return False
        if self._max is not None:
            if value > self._max or (value == self._max and not self._max_inclusive):
                return False
        return True


class _Ilike(_QueryOp):
    """q.ilike('%text%') — case-insensitive SQL LIKE over a text column."""

    def __init__(self, pattern):
        self._pattern = pattern

    def matches(self, value):
        if not isinstance(value, str):
            return False
        needle = self._pattern.replace('%', '').lower()
        return needle in value.lower()


# ---------------------------------------------------------------------------
# Rows and tables
# ---------------------------------------------------------------------------

class FakeRow(object):
    """One row of an in-memory table, presenting Anvil's Row interface.

    `_columns` is the set of column names the TABLE declares. Reading a name
    outside that set raises, which is what lets the tests prove that the
    `_row_value()` helper really does protect against a lagging Data Tables
    migration — a plain dict would silently return None and prove nothing.
    """

    def __init__(self, table, row_id, values):
        self._table = table
        self._row_id = row_id
        self._values = dict(values)

    # -- Anvil Row API ------------------------------------------------------
    def __getitem__(self, column):
        if column not in self._table.columns:
            raise NoSuchColumnError("no column %r in table %r" % (column, self._table.name))
        return self._values.get(column)

    def __setitem__(self, column, value):
        if column not in self._table.columns:
            raise NoSuchColumnError("no column %r in table %r" % (column, self._table.name))
        self._values[column] = value

    def __contains__(self, column):
        return column in self._table.columns

    def get_id(self):
        """Anvil's opaque row id. Stable for the life of the row."""
        return self._row_id

    def update(self, **fields):
        """Write several columns at once, as `row.update(**kw)` does in Anvil."""
        for column, value in fields.items():
            self[column] = value

    def delete(self):
        """Remove this row from its table."""
        self._table.rows.pop(self._row_id, None)

    def __eq__(self, other):
        # Anvil rows compare by identity of the underlying record, which is what
        # `row['user'] != user` in _own_or_raise relies on.
        return isinstance(other, FakeRow) and other._row_id == self._row_id \
            and other._table is self._table

    def __hash__(self):
        return hash((id(self._table), self._row_id))

    def __repr__(self):
        return '<FakeRow %s#%s>' % (self._table.name, self._row_id)


class FakeTable(object):
    """An in-memory stand-in for one Anvil Data Table."""

    def __init__(self, name, columns):
        self.name = name
        self.columns = set(columns)
        self.rows = {}
        self._next_id = 1

    # -- Anvil table API ----------------------------------------------------
    def add_row(self, **values):
        """Insert a row and return it. Unknown columns raise, as Anvil does."""
        unknown = set(values) - self.columns
        if unknown:
            raise NoSuchColumnError(
                "table %r has no column(s) %s" % (self.name, sorted(unknown)))
        row_id = '%s_%d' % (self.name[:3], self._next_id)
        self._next_id += 1
        row = FakeRow(self, row_id, values)
        self.rows[row_id] = row
        return row

    def get_by_id(self, row_id):
        """Fetch by opaque id, or None. Anvil returns None for a bad id."""
        return self.rows.get(row_id)

    def get(self, **filters):
        """Fetch the single matching row, None if none match.

        Raises when MORE than one row matches — the real Anvil behaviour, and the
        reason `_get_or_create_settings` can be shown to be exposed to a duplicate
        settings row.
        """
        found = self.search(**filters)
        if len(found) > 1:
            raise TableError(
                "%d rows matched %r in table %r; get() expects at most one"
                % (len(found), filters, self.name))
        return found[0] if found else None

    def search(self, *ordering, **filters):
        """Return every row matching `filters`, optionally ordered.

        Positional arguments are `tables.order_by(...)` markers. Keyword values are
        either a plain value (equality) or a query operator object.
        """
        unknown = set(filters) - self.columns
        if unknown:
            raise NoSuchColumnError(
                "table %r has no column(s) %s" % (self.name, sorted(unknown)))

        matched = []
        for row in self.rows.values():
            if all(self._matches(row, col, want) for col, want in filters.items()):
                matched.append(row)

        # Apply order_by markers right-to-left so the first one listed wins.
        for marker in reversed([o for o in ordering if isinstance(o, _OrderBy)]):
            matched.sort(
                key=lambda r, m=marker: (r[m.column] is None, r[m.column]),
                reverse=not marker.ascending)
        return matched

    @staticmethod
    def _matches(row, column, wanted):
        value = row[column]
        if isinstance(wanted, _QueryOp):
            return wanted.matches(value)
        return value == wanted

    def delete_all_rows(self):
        """Test helper — not part of the Anvil API."""
        self.rows.clear()


class _OrderBy(object):
    """Marker produced by tables.order_by(); consumed by FakeTable.search()."""

    def __init__(self, column, ascending=True):
        self.column = column
        self.ascending = ascending


class _AppTables(object):
    """The `app_tables` singleton: attribute access returns a FakeTable."""

    def __init__(self):
        self._tables = {}

    def _register(self, name, columns):
        self._tables[name] = FakeTable(name, columns)
        return self._tables[name]

    def __getattr__(self, name):
        try:
            return self.__dict__['_tables'][name]
        except KeyError:
            raise AttributeError("no data table named %r" % name)


# ---------------------------------------------------------------------------
# The schema, transcribed from anvil.yaml db_schema. Kept here rather than
# parsed so the tests fail loudly if a column is added to the app without the
# harness being updated to match.
# ---------------------------------------------------------------------------

SCHEMA = {
    'assessments': [
        'title', 'subject', 'type', 'due_date', 'start_date', 'weight', 'status',
        'description', 'reminder_days', 'linked_note_ids', 'term_info',
        'confidence', 'source_text', 'user', 'created_at', 'updated_at',
    ],
    'notes': [
        'title', 'content', 'tags', 'is_pinned', 'user', 'created_at', 'updated_at',
    ],
    'reminder_logs': [
        'assessment_id', 'user', 'sent_date', 'reminder_type',
    ],
    'user_settings': [
        'user', 'theme', 'default_reminder_days', 'notifications_enabled',
        'school_year', 'school_terms', 'timezone', 'subjects',
    ],
    'users': [
        'email', 'enabled', 'last_login', 'password_hash', 'n_password_failures',
        'remembered_logins', 'signed_up',
    ],
}


# ---------------------------------------------------------------------------
# Module-level state the tests manipulate
# ---------------------------------------------------------------------------

app_tables = _AppTables()
_current_user = None
sent_emails = []
secrets_store = {}
_email_should_fail = False


def set_current_user(user):
    """Make `anvil.users.get_user()` return `user` (a FakeRow or None)."""
    global _current_user
    _current_user = user


def get_current_user():
    return _current_user


def set_email_failure(should_fail):
    """When True, the next anvil.email.send() raises SendFailure."""
    global _email_should_fail
    _email_should_fail = should_fail


def reset():
    """Clear all tables, the signed-in user, captured emails and secrets."""
    global _current_user, _email_should_fail
    for name, columns in SCHEMA.items():
        app_tables._register(name, columns)
    _current_user = None
    _email_should_fail = False
    del sent_emails[:]
    secrets_store.clear()


# ---------------------------------------------------------------------------
# Building the fake `anvil` package
# ---------------------------------------------------------------------------

def _build_modules():
    """Construct every anvil.* module object the server code imports."""
    anvil_mod = types.ModuleType('anvil')

    # -- anvil.server: decorators are identity, so the decorated function stays
    #    directly callable from a test.
    server_mod = types.ModuleType('anvil.server')

    def _identity_decorator(fn):
        return fn

    server_mod.callable = _identity_decorator
    server_mod.background_task = _identity_decorator
    server_mod.call = lambda name, *a, **kw: (_ for _ in ()).throw(
        RuntimeError('anvil.server.call is client-side; not available offline'))

    class _NoServerFunctionError(AnvilWrappedError):
        pass

    server_mod.NoServerFunctionError = _NoServerFunctionError

    # -- anvil.tables (+ .query) -------------------------------------------
    tables_mod = types.ModuleType('anvil.tables')
    tables_mod.app_tables = app_tables
    tables_mod.Row = FakeRow
    tables_mod.TableError = TableError
    tables_mod.NoSuchColumnError = NoSuchColumnError

    def order_by(column, ascending=True):
        return _OrderBy(column, ascending)

    tables_mod.order_by = order_by

    class Transaction(object):
        """Snapshot/restore context manager, so rollback is really testable."""

        def __enter__(self):
            self._snapshot = {
                name: (copy.copy(table.rows), table._next_id)
                for name, table in app_tables._tables.items()
            }
            return self

        def __exit__(self, exc_type, exc, tb):
            if exc_type is not None:
                for name, (rows, next_id) in self._snapshot.items():
                    table = app_tables._tables[name]
                    table.rows = rows
                    table._next_id = next_id
            return False   # never swallow the exception

    tables_mod.Transaction = Transaction

    query_mod = types.ModuleType('anvil.tables.query')
    query_mod.any_of = lambda *options: _AnyOf(options)
    query_mod.not_ = lambda excluded: _Not(excluded)
    query_mod.between = _Between
    query_mod.ilike = lambda pattern: _Ilike(pattern)
    tables_mod.query = query_mod

    # -- anvil.users --------------------------------------------------------
    users_mod = types.ModuleType('anvil.users')
    users_mod.UserExists = UserExists
    users_mod.AuthenticationFailed = AuthenticationFailed
    users_mod.get_user = lambda: _current_user

    def signup_with_email(email, password, remember=False):
        """Create a users row, or raise UserExists — as the real service does."""
        if app_tables.users.get(email=email) is not None:
            raise UserExists('user already exists')
        return app_tables.users.add_row(
            email=email, enabled=True, password_hash='hashed:' + password,
            n_password_failures=0, remembered_logins=[], signed_up=None,
            last_login=None)

    def login_with_email(email, password, remember=False):
        """Sign a user in, or raise AuthenticationFailed."""
        row = app_tables.users.get(email=email)
        if row is None or row['password_hash'] != 'hashed:' + password:
            raise AuthenticationFailed('bad credentials')
        set_current_user(row)
        return row

    def force_login(user, remember=False):
        set_current_user(user)
        return user

    users_mod.signup_with_email = signup_with_email
    users_mod.login_with_email = login_with_email
    users_mod.force_login = force_login
    users_mod.logout = lambda: set_current_user(None)

    # -- anvil.email --------------------------------------------------------
    email_mod = types.ModuleType('anvil.email')
    email_mod.SendFailure = SendFailure

    def send(to=None, subject=None, text=None, html=None, **kw):
        """Capture the message in `sent_emails` instead of delivering it."""
        if _email_should_fail:
            raise SendFailure('simulated send failure')
        sent_emails.append(
            {'to': to, 'subject': subject, 'text': text, 'html': html})

    email_mod.send = send

    # -- anvil.secrets ------------------------------------------------------
    secrets_mod = types.ModuleType('anvil.secrets')

    def get_secret(name):
        if name not in secrets_store:
            raise AnvilWrappedError('no such secret: %s' % name)
        return secrets_store[name]

    secrets_mod.get_secret = get_secret

    # -- assemble -----------------------------------------------------------
    anvil_mod.server = server_mod
    anvil_mod.tables = tables_mod
    anvil_mod.users = users_mod
    anvil_mod.email = email_mod
    anvil_mod.secrets = secrets_mod
    anvil_mod.__path__ = []          # marks it as a package so submodules import

    return {
        'anvil': anvil_mod,
        'anvil.server': server_mod,
        'anvil.tables': tables_mod,
        'anvil.tables.query': query_mod,
        'anvil.users': users_mod,
        'anvil.email': email_mod,
        'anvil.secrets': secrets_mod,
    }


def install():
    """Put the fake anvil package into sys.modules and start with empty tables.

    Safe to call more than once; later calls just reset the data.
    """
    if 'anvil' not in sys.modules:
        for name, module in _build_modules().items():
            sys.modules[name] = module
    reset()
