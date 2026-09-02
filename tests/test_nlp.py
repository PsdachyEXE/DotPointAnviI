"""Proves the parser survives hostile input without losing its accuracy.

The parser's measured accuracy is SAT evidence (30/30 subjects and 30/30 due dates
against the EC-EF-01/02 test set), so the first suite here re-asserts that the
guards added for criterion 7.3 did not change what it understands. The rest are the
inputs that used to break it.
"""

from .harness import load_server_code, make_user, make_settings
from . import anvil_stub

load_server_code()

import datetime

from server_code import nlp
from server_code._constants import MAX_PARSER_INPUT_LENGTH, MAX_BULK_LINES


TERMS_2026 = [
    {'term': 1, 'start_date': '2026-01-28', 'end_date': '2026-04-02'},
    {'term': 2, 'start_date': '2026-04-20', 'end_date': '2026-06-26'},
    {'term': 3, 'start_date': '2026-07-13', 'end_date': '2026-09-18'},
    {'term': 4, 'start_date': '2026-10-05', 'end_date': '2026-12-18'},
]


def _fields(parsed):
    """The parsed values out of a parse_text result.

    parse_text returns {'fields', 'why', 'confidence', 'source_text'}: the values
    live under 'fields', and 'why' carries the token that produced each one so the
    preview can show the student how it was read.
    """
    return (parsed or {}).get('fields') or {}


def _signed_in(**settings_overrides):
    user = make_user()
    fields = {
        'subjects': ['Mathematical Methods', 'English', 'Physics', 'Chemistry',
                     'Biology', 'Software Development'],
        'school_terms': TERMS_2026,
    }
    fields.update(settings_overrides)
    make_settings(user, **fields)
    return user


# --- the accuracy that must not regress ------------------------------------

def suite_still_parses(results):
    """The guards must not have changed what the parser understands."""
    _signed_in()

    parsed = nlp.parse_text('Methods SAC2 due Friday week 5 worth 25%')
    results.equal(_fields(parsed).get('subject'), 'Mathematical Methods',
                  'the subject shorthand still resolves')
    results.equal(_fields(parsed).get('type'), 'sac', 'the type still resolves')
    results.equal(_fields(parsed).get('weight'), 25.0, 'the weight still resolves')
    results.ok(_fields(parsed).get('due_date') is not None,
               'a term-and-week phrase still resolves to a date')
    results.equal(parsed.get('confidence'), 'HIGH',
                  'all four fields found still scores HIGH')

    # A representative slice of the shorthand the client actually types.
    for text, expected_subject in (
            ('bio prac next tuesday', 'Biology'),
            ('chem test in 10 days', 'Chemistry'),
            ('softdev SAT due 12 November', 'Software Development'),
            ('physics sac 3 worth 15%', 'Physics'),
    ):
        parsed = nlp.parse_text(text)
        results.equal(_fields(parsed).get('subject'), expected_subject,
                      'the parser still reads %r as %s' % (text, expected_subject))

    # The parser must never write to the database — the preview is the only commit
    # path, and that guarantee is what FR17 and EC-UX-04 rest on.
    before = len(anvil_stub.app_tables.assessments.rows)
    nlp.parse_text('Methods SAC2 due Friday week 5 worth 25%')
    results.equal(len(anvil_stub.app_tables.assessments.rows), before,
                  'parsing writes nothing to the database')


# --- the confirmed crash ---------------------------------------------------

def suite_unbounded_day_counts(results):
    """"in N days" with an enormous N used to raise OverflowError and kill the parse.

    timedelta refuses a day count beyond about 999,999,999, and nothing bounded the
    captured digits, so one typed sentence took the whole request down.
    """
    _signed_in()

    for hostile in ('Methods SAC in 99999999999 days',
                    'Methods SAC in 999999999999999999999 days',
                    'Physics test in 100000000 days',
                    'Chem prac in 0000000000009 days'):
        results.does_not_raise(lambda t=hostile: nlp.parse_text(t),
                               'parsing %r does not raise' % hostile)

    # Past the plausible horizon the phrase must be treated as "not a date", so the
    # preview shows an empty due date the student can fill in — not a fabricated one
    # decades away, and not an error page.
    parsed = nlp.parse_text('Methods SAC in 99999999999 days')
    due_date = _fields(parsed).get('due_date')
    if due_date is not None:
        parsed_date = datetime.date.fromisoformat(due_date) \
            if isinstance(due_date, str) else due_date
        years_away = (parsed_date - datetime.date.today()).days / 365.0
        results.ok(years_away < 6,
                   'an absurd day count does not become a real due date')
    else:
        results.ok(True, 'an absurd day count yields no due date, as intended')

    # A SENSIBLE count must still work — the bound must not break the feature.
    parsed = nlp.parse_text('Chem test in 10 days')
    results.ok(_fields(parsed).get('due_date') is not None,
               '"in 10 days" still resolves to a date')


# --- input bounds ----------------------------------------------------------

def suite_input_bounds(results):
    """The parser box and the bulk box are both bounded."""
    _signed_in()

    results.raises(ValueError, lambda: nlp.parse_text(''),
                   'an empty parse is refused rather than returning a useless record')
    results.raises(ValueError, lambda: nlp.parse_text('    '),
                   'a whitespace-only parse is refused')
    results.raises(ValueError,
                   lambda: nlp.parse_text('x' * (MAX_PARSER_INPUT_LENGTH + 1)),
                   'an absurdly long sentence is refused')
    results.raises(ValueError, lambda: nlp.parse_text(None),
                   'a missing sentence is refused')
    results.raises(ValueError, lambda: nlp.parse_text(12345),
                   'a non-text sentence is refused')

    # parse_bulk takes the WHOLE pasted block as one string and splits it itself,
    # so that 'line_index' can be the real line number in what the student pasted.
    results.does_not_raise(
        lambda: nlp.parse_bulk('Methods SAC2 worth 25%\nPhysics test friday'),
        'a normal bulk paste is accepted')
    results.raises(ValueError,
                   lambda: nlp.parse_bulk('\n'.join(['line'] * (MAX_BULK_LINES + 1))),
                   'a bulk paste over the line cap is refused')
    results.raises(ValueError, lambda: nlp.parse_bulk(''),
                   'an empty bulk paste is refused')
    results.raises(ValueError, lambda: nlp.parse_bulk(None),
                   'a missing bulk paste is refused')

    # THE WRONG-LINE-NUMBER FIX: every result must carry the line it came from, so a
    # rejection can point the student at the right line of their own paste. Blank
    # lines are skipped but must NOT shift the numbering of the lines after them.
    parsed_lines = nlp.parse_bulk('Methods SAC2 worth 25%\n\nPhysics test friday')
    results.equal(len(parsed_lines), 2, 'blank lines are skipped')
    results.ok(all('line_index' in item for item in parsed_lines),
               'every bulk result carries its source line number')
    results.equal([item['line_index'] for item in parsed_lines], [0, 2],
                  'and the numbering reflects the ORIGINAL paste, gaps included')


# --- guarding the stored term dates ----------------------------------------

def suite_corrupt_school_terms(results):
    """A corrupt school_terms column must degrade, not break the parser.

    SAT 5 section 6 names this exact risk: "Anvil simpleObject list_of_dicts
    (school_terms) corrupted by hand-edit in the Data Tables console".
    """
    for corrupt in ('not a list',
                    {'term': 1},
                    [{'term': 'one', 'start_date': 'x', 'end_date': 'y'}],
                    [None, 'nonsense'],
                    [{'term': 1}],
                    [{'term': 1, 'start_date': '2026-13-45', 'end_date': '2026-04-02'}],
                    None):
        anvil_stub.reset()
        _signed_in(school_terms=corrupt)
        results.does_not_raise(
            lambda: nlp.parse_text('Methods SAC2 due Friday week 5 worth 25%'),
            'a school_terms column holding %r does not break the parse' % (corrupt,))

        # The rest of the sentence must still be read. Losing the date is the
        # documented consequence; losing the subject as well would not be.
        parsed = nlp.parse_text('Methods SAC2 due Friday week 5 worth 25%')
        results.equal(_fields(parsed).get('subject'), 'Mathematical Methods',
                      'and the subject is still read with %r' % (corrupt,))

    # A term set that is well-formed but BACKWARDS resolves no week phrase, because
    # _try_parse_week_phrase tests start <= due <= end. This is why the settings
    # validator now refuses to store one.
    anvil_stub.reset()
    _signed_in(school_terms=[{'term': 1, 'start_date': '2026-04-02',
                              'end_date': '2026-01-28'}])
    results.does_not_raise(
        lambda: nlp.parse_text('Methods SAC2 due Friday week 5'),
        'a backwards term does not break the parse either')


# --- a corrupt subjects column ---------------------------------------------

def suite_corrupt_subjects(results):
    """The maths-alias remap reads the student's subjects, so that read is guarded."""
    for corrupt in ('Mathematical Methods', {'a': 1}, [None, 42], None,
                    ['Underwater Basket Weaving']):
        anvil_stub.reset()
        _signed_in(subjects=corrupt)
        results.does_not_raise(lambda: nlp.parse_text('maths sac friday'),
                               'a subjects column holding %r does not break the parse'
                               % (corrupt,))

    # With exactly one maths study locked in, bare "maths" must resolve to THAT study
    # rather than the generic catch-all. This is the behaviour the guard must preserve.
    anvil_stub.reset()
    _signed_in(subjects=['Mathematical Methods', 'English'])
    parsed = nlp.parse_text('maths sac friday')
    results.equal(_fields(parsed).get('subject'), 'Mathematical Methods',
                  'bare "maths" resolves to the student\'s own maths study')


SUITES = [
    ('accuracy unchanged', suite_still_parses),
    ('unbounded day counts', suite_unbounded_day_counts),
    ('input bounds', suite_input_bounds),
    ('corrupt school terms', suite_corrupt_school_terms),
    ('corrupt subjects', suite_corrupt_subjects),
]
