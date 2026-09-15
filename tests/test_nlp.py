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

    # The app's own placeholder sentence. Note what resolves the date here: the
    # weekday 'Friday', NOT the trailing 'week 5'. _try_parse_week_phrase requires
    # a literal 'term N' token and returns before reading school_terms without one,
    # so this sentence never touches the FR15 path. That is asserted below rather
    # than assumed, because for a long time this fixture carried an assertion
    # claiming it proved the term-and-week resolver — see the suite that follows.
    parsed = nlp.parse_text('Methods SAC2 due Friday week 5 worth 25%')
    results.equal(_fields(parsed).get('subject'), 'Mathematical Methods',
                  'the subject shorthand still resolves')
    results.equal(_fields(parsed).get('type'), 'sac', 'the type still resolves')
    results.equal(_fields(parsed).get('weight'), 25.0, 'the weight still resolves')
    results.ok(_fields(parsed).get('due_date') is not None,
               'the weekday still resolves to a date')
    results.equal(_fields(parsed).get('term_info'), None,
                  'and it is the weekday that resolved it, not "week 5"')
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


# --- the weight the student actually typed ----------------------------------

def suite_weight_reading(results):
    """A percentage must be read as the whole number, not as a fragment of one.

    The regex starts matching at the first digit, so anything that makes those
    digits part of a larger number is invisible to it. Two sentences were read as
    a DIFFERENT figure from the one written, silently and at HIGH confidence.
    """
    _signed_in()

    # The two confirmed misreads. Both used to return 5.0.
    results.equal(_fields(nlp.parse_text('Physics sac worth -5%')).get('weight'), None,
                  'a negative percentage is not read as its positive twin')
    results.equal(_fields(nlp.parse_text('Physics sac worth 1e5%')).get('weight'), None,
                  'the exponent of a number in scientific notation is not the weight')
    # Same family: the capture would have been the 5 of ".5", i.e. ten times over.
    results.equal(_fields(nlp.parse_text('Physics sac worth .5%')).get('weight'), None,
                  'a leading decimal point is not dropped')

    # Rejecting a candidate must mean "keep looking", not "there is no weight here".
    results.equal(
        _fields(nlp.parse_text('Physics sac worth -5% or 25%')).get('weight'), 25.0,
        'a rejected candidate does not hide a real percentage later in the sentence')

    # Everything that was already read correctly must still be.
    for text, expected in (('Physics sac worth 25%', 25.0),
                           ('Physics sac worth 12.5%', 12.5),
                           ('Physics sac worth 25 percent', 25.0),
                           ('Physics sac worth 25 %', 25.0),
                           ('Physics sac worth 0%', 0.0),
                           ('Physics sac worth 100%', 100.0),
                           # The 'e' test is narrow on purpose: an ordinary word
                           # ending in 'e' must not disqualify the number after it.
                           ('Physics sac grade5%', 5.0)):
        results.equal(_fields(nlp.parse_text(text)).get('weight'), expected,
                      'the parser still reads %r as %s' % (text, expected))

    # Out of range is still REPORTED rather than swallowed: the preview shows it and
    # create_assessment explains the 0-100 rule in one sentence. Reading it and
    # refusing to save it are different jobs.
    results.equal(_fields(nlp.parse_text('Physics sac worth 200%')).get('weight'), 200.0,
                  'an out-of-range percentage is still read, and refused on save')


# --- what the confidence pill actually says ---------------------------------

def suite_confidence_bands(results):
    """FR17's three bands, and the provenance line beneath each detected field.

    Only HIGH was ever asserted. The whole point of the score is that a student can
    tell a confident parse from a lucky one, so MEDIUM and LOW need pinning too.
    """
    _signed_in()

    # 4 of {subject, type, due_date, weight} -> HIGH.
    results.equal(nlp.parse_text('Methods SAC2 due friday worth 25%').get('confidence'),
                  'HIGH', 'all four scored fields gives HIGH')
    # 2-3 -> MEDIUM. Subject and type only; no date and no weight.
    results.equal(nlp.parse_text('Methods SAC2').get('confidence'),
                  'MEDIUM', 'subject and type alone gives MEDIUM')
    # Fewer than 2 -> LOW. 'type' only counts when a keyword actually fired, and
    # every parse gets a title, so a bare word scores nothing.
    results.equal(nlp.parse_text('something').get('confidence'),
                  'LOW', 'a sentence the parser cannot read gives LOW')

    # The provenance strings. These are what the preview prints under each field,
    # and FR17/EC-UX-04 rest on them being present and specific.
    parsed = nlp.parse_text('Methods SAC2 due friday worth 25%')
    why = parsed.get('why') or {}
    for field in ('subject', 'type', 'due_date', 'weight'):
        results.ok(bool(why.get(field)),
                   'the preview can explain how it read %r' % field)
    results.ok('25%' in (why.get('weight') or ''),
               'and the weight explanation quotes the phrase it matched')


# --- Term X Week Y, the requirement itself (FR15) ---------------------------

# Every sentence in this file that is meant to exercise the term-and-week resolver
# must contain a literal "term N" token. _try_parse_week_phrase's regex requires
# one and returns (None, None) before it reads school_terms without it, so a
# sentence like "due Friday week 5" resolves its date from the WEEKDAY and never
# reaches FR15 at all. Named here once, because a fixture that silently misses the
# code it claims to test is the fault this suite exists to prevent.
_TERM_SENTENCE = 'Methods SAC2 due term 3 week 5 worth 25%'


def suite_term_week_dates(results):
    """"Term X Week Y" resolves against the student's stored term dates (FR15).

    The dates below are fixed by TERMS_2026, not by the day the suite runs:
    _try_parse_week_phrase computes start_date + (week - 1) * 7 from the stored
    term, so these assertions are stable whenever they are run.
    """
    _signed_in()

    # Term 3 starts Monday 13 Jul 2026, so week 5 is Monday 10 Aug 2026.
    parsed = nlp.parse_text(_TERM_SENTENCE)
    results.equal(_fields(parsed).get('due_date'), datetime.date(2026, 8, 10),
                  'term 3 week 5 resolves to the Monday of that week')
    results.equal(_fields(parsed).get('term_info'), 'term 3 week 5',
                  'and the student\'s own wording is kept in term_info')
    results.equal(parsed.get('confidence'), 'HIGH',
                  'a resolved term phrase counts as a found due date')
    # The provenance line the preview shows under the due date. FR17/EC-UX-04 rest
    # on the student being able to see WHY a date was chosen.
    results.ok('term 3 week 5' in ((parsed.get('why') or {}).get('due_date') or ''),
               'and the preview can say which phrase produced it')

    # A different term, to prove the term number is read rather than assumed.
    parsed = nlp.parse_text('Methods SAC2 due term 4 week 2 worth 25%')
    results.equal(_fields(parsed).get('due_date'), datetime.date(2026, 10, 12),
                  'term 4 week 2 resolves against term 4, not term 3')

    # The optional weekday inside the phrase (an extension beyond FR15's wording).
    parsed = nlp.parse_text('Methods SAC2 due term 3 friday week 5 worth 25%')
    results.equal(_fields(parsed).get('due_date'), datetime.date(2026, 8, 14),
                  'a weekday inside the phrase moves the date within that week')

    # FR15's fall-through: the term is not configured, so no date resolves and the
    # confidence drops. The wording is still preserved so the preview can explain
    # itself, and the student fills the date in by hand.
    anvil_stub.reset()
    _signed_in(school_terms=[])
    parsed = nlp.parse_text(_TERM_SENTENCE)
    results.equal(_fields(parsed).get('due_date'), None,
                  'an unconfigured term set resolves no date')
    results.equal(_fields(parsed).get('term_info'), 'term 3 week 5',
                  'but the phrase the student typed is still recorded')
    results.equal(parsed.get('confidence'), 'MEDIUM',
                  'and losing the date costs exactly one confidence field')

    # The named term is missing from an otherwise valid config.
    anvil_stub.reset()
    _signed_in(school_terms=[TERMS_2026[0]])
    parsed = nlp.parse_text(_TERM_SENTENCE)
    results.equal(_fields(parsed).get('due_date'), None,
                  'a term the student has not configured resolves no date')

    # A week number outside the plausible range is treated as "not a date" rather
    # than fabricating one years away.
    anvil_stub.reset()
    _signed_in()
    parsed = nlp.parse_text('Methods SAC2 due term 3 week 99 worth 25%')
    results.equal(_fields(parsed).get('due_date'), None,
                  'week 99 falls outside the term and resolves no date')


# --- guarding the stored term dates ----------------------------------------

def suite_corrupt_school_terms(results):
    """A corrupt school_terms column must degrade, not break the parser.

    SAT 5 section 6 names this exact risk: "Anvil simpleObject list_of_dicts
    (school_terms) corrupted by hand-edit in the Data Tables console".

    Every sentence here uses _TERM_SENTENCE. It has to: with a sentence carrying no
    "term N" token the resolver returns at its first guard and the corrupt column is
    never read, so the assertions pass without touching the code they name.
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
            lambda: nlp.parse_text(_TERM_SENTENCE),
            'a school_terms column holding %r does not break the parse' % (corrupt,))

        parsed = nlp.parse_text(_TERM_SENTENCE)
        # Losing the date is the documented consequence of a corrupt column...
        results.equal(_fields(parsed).get('due_date'), None,
                      'the date degrades to None with %r' % (corrupt,))
        # ...and losing the rest of the sentence would not be.
        results.equal(_fields(parsed).get('subject'), 'Mathematical Methods',
                      'and the subject is still read with %r' % (corrupt,))

    # A term set that is well-formed but BACKWARDS resolves no week phrase, because
    # _try_parse_week_phrase tests start <= due <= end. This is why the settings
    # validator now refuses to store one.
    anvil_stub.reset()
    _signed_in(school_terms=[{'term': 1, 'start_date': '2026-04-02',
                              'end_date': '2026-01-28'}])
    results.does_not_raise(
        lambda: nlp.parse_text('Methods SAC2 due term 1 week 5'),
        'a backwards term does not break the parse either')
    parsed = nlp.parse_text('Methods SAC2 due term 1 week 5')
    results.equal(_fields(parsed).get('due_date'), None,
                  'and a backwards term resolves no date')


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


def suite_type_vocabulary(results):
    """The words a humanities student writes must be read as a type (NFR04).

    NFR04 asks the parser for "a usable record (subject + due_date + type
    detected)". Measured over the 30-sentence accuracy set on 15 Sep 2026,
    subject and due date each landed 30/30 but a type keyword fired on only
    22/30, so the requirement's own three-field test held on 22 of 30. Every one
    of the eight misses used a word describing the FORM of the work - essay,
    oral, presentation, folio, report - none of which was in TYPE_KEYWORDS.

    Two things are asserted here, and the second matters more than the first.
    One: each of those words now fires. Two: adding them did not cost the
    precedence rule, so a sentence that also names a real VCE category still
    gets the category. Without the fix the first group fails; without the
    ordering the second does.
    """
    _signed_in()

    # 1. The form words fire, and 'why' carries the token - which is what the
    #    confidence score reads. Testing type_value alone would pass even when
    #    nothing matched, because 'other' is the fallback for every sentence.
    for sentence, token in (
            ('eng essay due monday', 'essay'),
            ('english oral presentation next thursday', 'oral'),
            ('swd folio due thursday', 'folio'),
            ('swd portfolio due thursday', 'portfolio'),
            ('psych research report in 21 days', 'report'),
    ):
        parsed = nlp.parse_text(sentence)
        results.equal(_fields(parsed).get('type'), 'project',
                      '%r is typed as a project' % sentence)
        results.ok(token in (parsed.get('why') or {}).get('type', ''),
                   '%r records %r as the token that fired' % (sentence, token))

    # 2. Precedence survives. 'project' is the fourth key, so a sentence naming a
    #    category is still filed under the category - the form word never wins.
    #    This is the assertion that would fail if the six words were prepended,
    #    or if TYPE_KEYWORDS were ever re-sorted into alphabetical order.
    results.equal(_fields(nlp.parse_text('english essay sac due monday')).get('type'),
                  'sac', 'a named SAC beats the form of the work')
    results.equal(_fields(nlp.parse_text('literature oral exam on friday')).get('type'),
                  'exam', 'a named exam beats the form of the work')

    # 3. A word boundary is still required, so a form word buried inside a longer
    #    word does not fire. 'moral' is the trap 'oral' introduces.
    parsed = nlp.parse_text('philosophy moral dilemmas due friday')
    results.equal((parsed.get('why') or {}).get('type'), None,
                  "'moral' does not fire the 'oral' keyword")


SUITES = [
    ('accuracy unchanged', suite_still_parses),
    ('unbounded day counts', suite_unbounded_day_counts),
    ('input bounds', suite_input_bounds),
    ('weight reading', suite_weight_reading),
    ('confidence bands (FR17)', suite_confidence_bands),
    ('term week dates', suite_term_week_dates),
    ('corrupt school terms', suite_corrupt_school_terms),
    ('corrupt subjects', suite_corrupt_subjects),
    ('type vocabulary (NFR04)', suite_type_vocabulary),
]
