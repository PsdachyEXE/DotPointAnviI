"""Proves the FR15 tripwire actually bites, by reintroducing the defect it caught.

    python docs/evidence/sat8/prove_tripwire.py

A test that has never failed is not yet evidence of anything. This reintroduces
the exact fault described in the SAT 8 debugging case study — the fixture
sentence that contains no "term N" token, so _try_parse_week_phrase returns at
its first guard and the date comes from the weekday instead — runs the suite,
and then puts the file back.

It edits tests/test_nlp.py in place and restores it in a `finally`, so an
interrupted run still leaves the working tree as it found it. Nothing else is
touched, and `git status` is checked at the end to prove it.
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
TARGET = os.path.join(REPO, 'tests', 'test_nlp.py')
BACKUP = TARGET + '.tripwire-backup'

# The fixture the suite uses now, and the one it used to use. The whole defect is
# the difference between them: the second contains no "term N" token.
GOOD = "_TERM_SENTENCE = 'Methods SAC2 due term 3 week 5 worth 25%'"
BAD = "_TERM_SENTENCE = 'Methods SAC2 due Friday week 5 worth 25%'"


def run_suite(label):
    print('\n' + '=' * 72)
    print(label)
    print('=' * 72)
    result = subprocess.run(
        [sys.executable, '-m', 'tests.run_all'],
        cwd=REPO, capture_output=True, text=True,
        env=dict(os.environ, PYTHONIOENCODING='utf-8'))
    print((result.stdout or '') + (result.stderr or ''), end='')
    return result.returncode


def main():
    source = open(TARGET, encoding='utf-8').read()
    if GOOD not in source:
        print('Could not find the fixture line in tests/test_nlp.py — has it moved?')
        return 2

    print(__doc__.strip().split('\n\n', 1)[1])

    run_suite('1. The suite as it stands.')

    shutil.copy2(TARGET, BACKUP)
    try:
        print('\n\nReintroducing the defect: the fixture loses its "term N" token,')
        print('so every sentence in the FR15 suites resolves its date from the weekday.')
        print('  - %s' % GOOD)
        print('  + %s' % BAD)
        open(TARGET, 'w', encoding='utf-8').write(source.replace(GOOD, BAD))
        code = run_suite('2. The same suite, with the defect back.')
        if code == 0:
            print('\n*** THE TRIPWIRE DID NOT BITE. That is itself a finding. ***')
        else:
            print('\nThe tripwire bit: the defect cannot return silently.')
    finally:
        shutil.move(BACKUP, TARGET)

    run_suite('3. Restored.')

    status = subprocess.run(['git', 'status', '--short'], cwd=REPO,
                            capture_output=True, text=True)
    print('\n`git status --short` after restoring:')
    print(status.stdout.rstrip() or '  (clean — the working tree is as it was)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
