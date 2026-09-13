"""Driver used for the SAT 8 breakpoint session (docs/evidence/sat8).

Loads the UNMODIFIED shipped server_code through the repository's own offline
harness (tests/anvil_stub.py fakes the anvil package; nothing in server_code is
mocked), then parses the two sentences the investigation compares. Run under the
debugger:

    python docs/evidence/sat8/debug_driver.py

The breakpoint session recorded in TC-DBG-02_breakpoints.txt was run against the
real suite instead (python -m pdb -m tests.run_all), because a breakpoint set on
server_code/nlp.py only resolves once the repository root is on sys.path, which
-m gives you for free. This driver reproduces the same two parses on their own.
"""

import os
import sys

# The repository root, so `tests` and `server_code` import the same way they do
# under `python -m tests.run_all` from the root.
sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from tests.harness import load_server_code, make_user, make_settings
from tests import anvil_stub

load_server_code()

from server_code import nlp

TERMS_2026 = [
    {'term': 1, 'start_date': '2026-01-28', 'end_date': '2026-04-02'},
    {'term': 2, 'start_date': '2026-04-20', 'end_date': '2026-06-26'},
    {'term': 3, 'start_date': '2026-07-13', 'end_date': '2026-09-18'},
    {'term': 4, 'start_date': '2026-10-05', 'end_date': '2026-12-18'},
]


def signed_in():
    anvil_stub.reset()
    user = make_user()
    make_settings(user, subjects=['Mathematical Methods', 'English', 'Physics'],
                  school_terms=TERMS_2026)
    return user


if __name__ == '__main__':
    # A: the fixture the test suite has always used.
    signed_in()
    a = nlp.parse_text('Methods SAC2 due Friday week 5 worth 25%')

    # B: the same sentence with the token the resolver actually requires.
    signed_in()
    b = nlp.parse_text('Methods SAC2 due term 3 week 5 worth 25%')

    for label, r in (('A', a), ('B', b)):
        print('%s due_date=%s term_info=%r' % (
            label, r['fields']['due_date'], r['fields']['term_info']))
