"""Runs every offline suite. From the repository root:  python -m tests.run_all

Prints "<n> assertions passed, <m> failed" and exits non-zero if anything failed, so
it can be run before every push. The assertion count is the figure quoted as testing
evidence in docs/TESTING.md — it is counted here rather than claimed.
"""

import sys
import importlib

from .harness import run_suites


# Each entry is a module under tests/ exposing a module-level SUITES list of
# (name, function) pairs. Listed explicitly rather than auto-discovered so a suite
# that fails to import is a loud error rather than a silently missing test.
SUITE_MODULES = [
    'tests.test_validation',
    'tests.test_datetime',
    'tests.test_reminders',
    'tests.test_assessments',
    'tests.test_notes',
    'tests.test_nlp',
    'tests.test_constants_integrity',
]


def collect():
    """Import every suite module and flatten their SUITES lists into one."""
    collected = []
    for module_name in SUITE_MODULES:
        module = importlib.import_module(module_name)
        short_name = module_name.split('.')[-1].replace('test_', '')
        for suite_name, suite_fn in module.SUITES:
            collected.append(('%s / %s' % (short_name, suite_name), suite_fn))
    return collected


if __name__ == '__main__':
    sys.exit(run_suites(collect()))
