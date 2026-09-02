"""Loads `server_code` as an importable package on a plain Python interpreter.

`server_code/` deliberately has no `__init__.py` — adding one would make the Anvil
IDE treat it as a server module called `__init__`. But the modules inside it use
relative imports (`from ._auth import _require_user`), which only resolve if Python
believes they live in a package.

`load_server_code()` bridges that gap: it synthesises a package object in
`sys.modules` whose `__path__` points at the real directory, so the unmodified
source files import exactly as they do inside Anvil. Nothing is copied, patched or
regenerated — the tests run the shipped code.

Also provides the tiny assertion helpers the suites share, so each suite file stays
about the behaviour it is proving rather than about plumbing.
"""

import os
import sys
import types
import traceback

from . import anvil_stub


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_server_code():
    """Install the anvil stub, then make `server_code.*` importable. Idempotent."""
    anvil_stub.install()
    if 'server_code' not in sys.modules:
        package = types.ModuleType('server_code')
        package.__path__ = [os.path.join(REPO_ROOT, 'server_code')]
        sys.modules['server_code'] = package
    return sys.modules['server_code']


# ---------------------------------------------------------------------------
# A deliberately small assertion vocabulary. Every suite reports through these,
# so the run summary can count assertions rather than test functions — which is
# the number docs/TESTING.md quotes as evidence for SAT criterion 7.3.
# ---------------------------------------------------------------------------

class Results(object):
    """Tally of one test run: assertions passed, and every failure in full."""

    def __init__(self):
        self.passed = 0
        self.failures = []
        self.current_suite = '(none)'

    def ok(self, condition, description):
        """Assert `condition`. Records a pass or a failure; never raises."""
        if condition:
            self.passed += 1
        else:
            self.failures.append('%s: %s' % (self.current_suite, description))

    def equal(self, actual, expected, description):
        """Assert equality, reporting both values when they differ."""
        self.ok(actual == expected,
                '%s (expected %r, got %r)' % (description, expected, actual))

    def raises(self, exception_type, fn, description, message_contains=None):
        """Assert that calling `fn` raises `exception_type`.

        `message_contains`, when given, additionally asserts on the message text —
        used by the suites that prove an error is worded for a student rather than
        for a developer.
        """
        try:
            fn()
        except exception_type as exc:
            if message_contains is not None and message_contains.lower() not in str(exc).lower():
                self.failures.append(
                    '%s: %s (raised %s but message %r lacks %r)'
                    % (self.current_suite, description, exception_type.__name__,
                       str(exc), message_contains))
            else:
                self.passed += 1
        except Exception as exc:
            self.failures.append(
                '%s: %s (raised %s: %s, expected %s)'
                % (self.current_suite, description, type(exc).__name__, exc,
                   exception_type.__name__))
        else:
            self.failures.append(
                '%s: %s (nothing raised, expected %s)'
                % (self.current_suite, description, exception_type.__name__))

    def does_not_raise(self, fn, description):
        """Assert that calling `fn` completes. Used for the accept-this-input cases."""
        try:
            fn()
        except Exception as exc:
            self.failures.append(
                '%s: %s (raised %s: %s)'
                % (self.current_suite, description, type(exc).__name__, exc))
        else:
            self.passed += 1


def run_suites(suites):
    """Run every (name, function) pair, print a summary, return the exit code."""
    results = Results()
    for name, suite_fn in suites:
        results.current_suite = name
        anvil_stub.reset()
        try:
            suite_fn(results)
        except Exception:
            results.failures.append(
                '%s: SUITE CRASHED\n%s' % (name, traceback.format_exc()))

    print('%d assertions passed, %d failed' % (results.passed, len(results.failures)))
    for failure in results.failures:
        print('  FAIL  %s' % failure)
    return 1 if results.failures else 0


# ---------------------------------------------------------------------------
# Fixtures shared by the suites
# ---------------------------------------------------------------------------

def make_user(email='student@example.com'):
    """Insert a users row and sign them in. Returns the row."""
    user = anvil_stub.app_tables.users.add_row(
        email=email, enabled=True, password_hash='hashed:pw',
        n_password_failures=0, remembered_logins=[], signed_up=None,
        last_login=None)
    anvil_stub.set_current_user(user)
    return user


def make_settings(user, **overrides):
    """Insert a user_settings row with sane defaults, overridable per test."""
    fields = {
        'user': user,
        'theme': 'light',
        'default_reminder_days': [7, 2],
        'notifications_enabled': True,
        'school_year': 2026,
        'school_terms': [],
        'timezone': 'Australia/Melbourne',
        'subjects': ['Mathematical Methods', 'English', 'Physics'],
    }
    fields.update(overrides)
    return anvil_stub.app_tables.user_settings.add_row(**fields)
