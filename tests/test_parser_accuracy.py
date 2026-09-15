"""Measures the parser against evaluation criteria EC-EF-01 and EC-EF-02.

    EC-EF-01  the parser correctly identifies the SUBJECT from the input via
              SUBJECT_ALIASES on at least 80% of inputs in the test set.
    EC-EF-02  the parser correctly extracts the DUE DATE (DD/MM, weekday,
              Term X Week Y, 'tomorrow') on at least 80% of inputs in the set.

docs/TESTING.md has quoted "30/30 = 100%" for both since the first build, against
a test set that lived in a session scratchpad and was lost. A figure an assessor
cannot reproduce is not evidence, so this file rebuilds the set IN THE REPOSITORY
and recomputes both percentages every run. The sentences are new; the 2026-07
figures are not reproducible and should not be quoted as though this file
produced them.

HOW THE EXPECTED DATES ARE DERIVED, and why they are not read from the parser.
Writing `expected = nlp.parse_text(...)` would make every assertion true by
construction. Every expectation below is instead computed from the student's
MEANING by the three helpers at the top of this file — the next occurrence of a
weekday, a day-and-month rolled forward if it has passed, a term week counted off
the stored term start. If a helper and the parser disagree, that is a finding
about the parser, not a test to be adjusted.

The conventions those helpers encode, confirmed against the shipped parser:
  * a weekday name resolves to its next occurrence STRICTLY AFTER today, so a
    sentence typed on a Sunday saying "due Sunday" means the Sunday a week away;
  * "next <weekday>" resolves the same way — it does not add a further week, so
    "next Monday" typed on a Sunday is tomorrow;
  * a bare DD/MM is day-first (Australian) and rolls into next year once the date
    has passed;
  * Term X Week Y is the term's start date plus (X - 1) whole weeks, which is the
    Monday of that week whenever the stored term starts on a Monday.
"""

from .harness import load_server_code, make_user, make_settings
from . import anvil_stub

load_server_code()

import datetime

from server_code import nlp


# The VIC 2026 term dates the Settings page offers as a preset. Every term here
# starts on a Monday, which is what makes "the Monday of week N" exact.
TERMS_2026 = [
    {'term': 1, 'start_date': '2026-01-28', 'end_date': '2026-04-02'},
    {'term': 2, 'start_date': '2026-04-20', 'end_date': '2026-06-26'},
    {'term': 3, 'start_date': '2026-07-13', 'end_date': '2026-09-18'},
    {'term': 4, 'start_date': '2026-10-05', 'end_date': '2026-12-18'},
]

# The studies the notional student has locked in. Subject ranking prefers a locked
# study over an unlocked one, so the set matters to the measurement and is stated
# rather than left implicit.
SUBJECTS = [
    'Mathematical Methods', 'Specialist Mathematics', 'English', 'Literature',
    'Physics', 'Chemistry', 'Biology', 'Psychology', 'Software Development',
]

MON, TUE, WED, THU, FRI, SAT, SUN = range(7)


# --- independent expectations ----------------------------------------------

def _next_weekday(today, target):
    """The next `target` weekday strictly after `today` (0 = Monday)."""
    ahead = (target - today.weekday()) % 7
    return today + datetime.timedelta(days=ahead or 7)


def _next_week_weekday(today, target):
    """The `target` weekday of NEXT week (0 = Monday).

    Deliberately NOT written as _next_weekday(today, target) + 7 days, and
    deliberately not calling anything in server_code. Both shortcuts would make
    the expectation true by construction, which is the exact failure the SAT 8
    case study was about: an assertion that cannot fail is worse than no
    assertion. This counts from the Monday of this week, the way a person does
    when they say "the Thursday of next week".
    """
    monday_this_week = today - datetime.timedelta(days=today.weekday())
    return monday_this_week + datetime.timedelta(days=7 + target)


def _rolled(today, day, month):
    """`day`/`month` this year, or next year if that date has already passed."""
    candidate = datetime.date(today.year, month, day)
    if candidate < today:
        candidate = datetime.date(today.year + 1, month, day)
    return candidate


def _term_week(term_no, week_no, weekday=None):
    """The start of week `week_no` of term `term_no`, optionally moved to a weekday."""
    term = next(t for t in TERMS_2026 if t['term'] == term_no)
    start = datetime.date.fromisoformat(term['start_date'])
    week_start = start + datetime.timedelta(days=(week_no - 1) * 7)
    if weekday is None:
        return week_start
    return week_start + datetime.timedelta(
        days=(weekday - week_start.weekday()) % 7)


def _cases(today):
    """The 30 sentences, each with the subject and the date it is meant to mean.

    Phrasing follows the shorthand recorded in the Data Collection interview:
    subject abbreviation, assessment type, then a date in whichever of the four
    supported forms the student happened to use.
    """
    return [
        # --- weekday form ---
        ('Methods SAC2 due Friday worth 25%',
         'Mathematical Methods', _next_weekday(today, FRI)),
        ('eng essay due monday',
         'English', _next_weekday(today, MON)),
        # "next thursday" means next week's, not the soonest one. The
        # expectation used to read _next_weekday(today, THU), which matched the
        # parser only because the parser was discarding the word "next" - the
        # test and the bug agreed with each other. Beta tester U01 found it.
        ('english oral presentation next thursday',
         'English', _next_week_weekday(today, THU)),
        ('bio prac next tuesday',
         'Biology', _next_weekday(today, TUE)),
        ('spesh homework due wednesday',
         'Specialist Mathematics', _next_weekday(today, WED)),
        ('swd folio due thursday',
         'Software Development', _next_weekday(today, THU)),
        # 'saturday' spelled in full must still parse, even though the bare 'sat'
        # abbreviation is excluded from the weekday matcher to protect the SAT type.
        ('software development sac due saturday',
         'Software Development', _next_weekday(today, SAT)),
        ('psych test due friday',
         'Psychology', _next_weekday(today, FRI)),

        # --- DD/MM form (day-first, rolls forward) ---
        ('methods sac 3 on 12/11',
         'Mathematical Methods', _rolled(today, 12, 11)),
        ('phys sac due 1/10 worth 30%',
         'Physics', _rolled(today, 1, 10)),
        ('lit essay due 20/10',
         'Literature', _rolled(today, 20, 10)),
        ('english essay due 5/11',
         'English', _rolled(today, 5, 11)),
        # A date that has already gone by must roll into next year rather than
        # being stored in the past.
        ('bio sac due 1/3',
         'Biology', _rolled(today, 1, 3)),

        # --- month-name form ---
        # 'SAT' here is the assessment type, not Saturday. It collided once.
        ('softdev SAT due 12 November',
         'Software Development', _rolled(today, 12, 11)),
        ('physics exam 12 November',
         'Physics', _rolled(today, 12, 11)),
        ('psych sac 12 October worth 20%',
         'Psychology', _rolled(today, 12, 10)),
        ('chem sac due 3 October',
         'Chemistry', _rolled(today, 3, 10)),
        ('physics prac due 15 December',
         'Physics', _rolled(today, 15, 12)),

        # --- Term X Week Y form ---
        ('bio sac term 4 week 2',
         'Biology', _term_week(4, 2)),
        ('swd sac term 3 week 5',
         'Software Development', _term_week(3, 5)),
        ('specialist maths sac term 4 week 3',
         'Specialist Mathematics', _term_week(4, 3)),
        ('chemistry sac term 4 week 1',
         'Chemistry', _term_week(4, 1)),
        ('methods exam term 4 week 8',
         'Mathematical Methods', _term_week(4, 8)),
        ('lit oral due term 4 week 5',
         'Literature', _term_week(4, 5)),
        ('psych report term 4 friday week 2',
         'Psychology', _term_week(4, 2, FRI)),

        # --- relative form ---
        ('chem prac tomorrow',
         'Chemistry', today + datetime.timedelta(days=1)),
        ('literature sac tomorrow worth 15%',
         'Literature', today + datetime.timedelta(days=1)),
        ('chem test in 10 days',
         'Chemistry', today + datetime.timedelta(days=10)),
        ('bio exam in 30 days',
         'Biology', today + datetime.timedelta(days=30)),
        ('psych research report in 21 days',
         'Psychology', today + datetime.timedelta(days=21)),
    ]


def _signed_in():
    user = make_user()
    make_settings(user, subjects=SUBJECTS, school_terms=TERMS_2026)
    return user


# --- the measurement --------------------------------------------------------

def suite_ec_ef_01_subject(results):
    """EC-EF-01: subject identified correctly on at least 80% of the set."""
    anvil_stub.reset()
    _signed_in()
    today = datetime.date.today()
    cases = _cases(today)

    hits = []
    misses = []
    for text, expected_subject, _ in cases:
        got = ((nlp.parse_text(text) or {}).get('fields') or {}).get('subject')
        (hits if got == expected_subject else misses).append(
            (text, expected_subject, got))

    # Each sentence is asserted individually, so a regression names the sentence
    # that broke rather than only moving a percentage.
    for text, expected, got in misses:
        results.equal(got, expected, 'EC-EF-01 subject for %r' % text)
    for text, expected, _ in hits:
        results.ok(True, 'EC-EF-01 subject for %r is %s' % (text, expected))

    percentage = 100.0 * len(hits) / len(cases)
    results.ok(percentage >= 80.0,
               'EC-EF-01 met: %d/%d subjects = %.1f%% (target 80%%)'
               % (len(hits), len(cases), percentage))


def suite_ec_ef_02_due_date(results):
    """EC-EF-02: due date extracted correctly on at least 80% of the set."""
    anvil_stub.reset()
    _signed_in()
    today = datetime.date.today()
    cases = _cases(today)

    hits = []
    misses = []
    for text, _, expected_date in cases:
        got = ((nlp.parse_text(text) or {}).get('fields') or {}).get('due_date')
        if isinstance(got, str):
            got = datetime.date.fromisoformat(got)
        (hits if got == expected_date else misses).append(
            (text, expected_date, got))

    for text, expected, got in misses:
        results.equal(got, expected, 'EC-EF-02 due date for %r' % text)
    for text, expected, _ in hits:
        results.ok(True, 'EC-EF-02 due date for %r is %s' % (text, expected))

    percentage = 100.0 * len(hits) / len(cases)
    results.ok(percentage >= 80.0,
               'EC-EF-02 met: %d/%d due dates = %.1f%% (target 80%%)'
               % (len(hits), len(cases), percentage))


def suite_set_shape(results):
    """The set itself must stay big enough and varied enough to mean anything.

    A 30-input set that quietly shrank to six, or that lost the Term X Week Y
    sentences, would keep reporting a healthy percentage of a much easier task.
    """
    today = datetime.date.today()
    cases = _cases(today)
    results.equal(len(cases), 30, 'the set holds 30 inputs')
    results.equal(len(set(text for text, _, _ in cases)), 30,
                  'and no sentence is repeated')

    # Every date form EC-EF-02 names is represented.
    texts = [text.lower() for text, _, _ in cases]
    results.ok(sum(1 for t in texts if 'term' in t and 'week' in t) >= 5,
               'Term X Week Y is represented')
    results.ok(sum(1 for t in texts if '/' in t) >= 5,
               'the DD/MM form is represented')
    results.ok(sum(1 for t in texts if 'tomorrow' in t or ' in ' in t) >= 5,
               "'tomorrow' and 'in N days' are represented")
    results.ok(sum(1 for t in texts if any(d in t for d in (
                   'monday', 'tuesday', 'wednesday', 'thursday', 'friday',
                   'saturday', 'sunday'))) >= 5,
               'weekday names are represented')
    results.ok(len(set(subject for _, subject, _ in cases)) >= 8,
               'and at least eight different studies appear')


SUITES = [
    ('EC-EF-01 subject identification', suite_ec_ef_01_subject),
    ('EC-EF-02 due date extraction', suite_ec_ef_02_due_date),
    ('test set shape', suite_set_shape),
]


def report():
    """Print the input-by-input measurement, for the SAT testing evidence.

        python -m tests.test_parser_accuracy

    run_all reports only failures, which is right for a regression suite and
    useless as evidence: "0 failed" does not show an assessor the 30 sentences or
    what each produced. This prints the whole table and both percentages.
    """
    anvil_stub.reset()
    _signed_in()
    today = datetime.date.today()
    cases = _cases(today)

    print('EC-EF-01 / EC-EF-02 parser accuracy measurement')
    print("today = %s (%s); expectations are computed from the student's meaning,"
          % (today, today.strftime('%A')))
    print('not read back from the parser. Target for both criteria: 80%.')
    print()
    header = '%-3s %-42s %-24s %-12s %-4s' % (
        '#', 'input', 'subject', 'due date', 'ok')
    print(header)
    print('-' * len(header))

    subject_hits = date_hits = 0
    for i, (text, expected_subject, expected_date) in enumerate(cases, start=1):
        fields = (nlp.parse_text(text) or {}).get('fields') or {}
        got_subject = fields.get('subject')
        got_date = fields.get('due_date')
        if isinstance(got_date, str):
            got_date = datetime.date.fromisoformat(got_date)
        subject_ok = got_subject == expected_subject
        date_ok = got_date == expected_date
        subject_hits += subject_ok
        date_hits += date_ok
        print('%-3d %-42s %-24s %-12s %s%s' % (
            i, text[:42], (got_subject or '-')[:24],
            got_date or '-', 'S' if subject_ok else 's',
            'D' if date_ok else 'd'))

    total = len(cases)
    print()
    print('EC-EF-01 subject identification : %d/%d = %.1f%%  (target 80%%)  %s'
          % (subject_hits, total, 100.0 * subject_hits / total,
             'MET' if subject_hits / total >= 0.8 else 'NOT MET'))
    print('EC-EF-02 due date extraction    : %d/%d = %.1f%%  (target 80%%)  %s'
          % (date_hits, total, 100.0 * date_hits / total,
             'MET' if date_hits / total >= 0.8 else 'NOT MET'))


if __name__ == '__main__':
    report()
