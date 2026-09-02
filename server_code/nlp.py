import anvil.secrets
import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""Text parser for assessment specifications (FR01, FR02, FR15, FR16, FR17).

This is the app's flagship feature: it turns one typed sentence — "Methods SAC2
due Friday week 5 worth 25%" — into the six fields an assessment row needs.

Exposes parse_text(s) and parse_bulk(s) as @anvil.server.callable. Pure parser
logic only: NO table writes. parse_text returns a plain dict that the client
previews (AssessmentEditorForm mode='preview') and then passes to
assessments.create_assessment. parse_bulk is the parsing half of FR02 — one
result per non-blank line, each tagged with the line number it came from, so
the bulk dialog can name a line the student can find.

Return shape (spec section 2):
    {
      'fields': {'title', 'subject', 'type', 'due_date', 'weight', 'term_info'},
      'why':    {<field>: '<provenance string>'},   # detected fields only
      'confidence': 'HIGH' | 'MEDIUM' | 'LOW',
      'source_text': <original input>,
    }

'why' is the per-field provenance the preview shows beside each control ("Due
date: matched 'friday' → 22 May 2026"), which is how a student can tell a
correct guess from a lucky one before saving.

Confidence (_score): counts genuinely-detected fields among
{subject, type, due_date, weight}; >=4 HIGH, 2-3 MEDIUM, <2 LOW. 'type' counts
only when a keyword actually fired (not the 'other' fallback); 'due_date' from a
'Term X Week Y' phrase counts only when school_terms is configured (FR15).
No field is weighted more heavily than another — see _score for what that does
and does not mean for a sentence with no resolvable date.

THE ONE RULE THIS MODULE KEEPS: _parse_one never raises. Every field matcher
answers "not found" rather than throwing, because the parser's promise is a
best-effort record for ANY sentence — a half-understood record the student can
correct in the preview is useful, a traceback is not. Input validation (empty
box, over-long paste) happens in the two callables ABOVE _parse_one, where
there is still a person at the keyboard to be told what to fix.

See IMPLEMENTATION_SPEC.md section 2 (server_code/nlp.py).
"""

import anvil.server
import math
import re
import datetime

from ._auth import _require_user
from ._datetime import _user_today
from ._constants import (
    SUBJECT_ALIASES, TYPE_KEYWORDS, MATHS_GROUP, AMBIGUOUS_BARE_ALIASES,
    CANONICAL_SUBJECTS, MAX_BULK_LINES, MAX_PARSER_INPUT_LENGTH,
    MAX_TITLE_LENGTH, MIN_TERM_NUMBER, MAX_TERM_NUMBER,
)
from ._validation import require_list, require_text, safe_list
from .notes import _get_or_create_settings, _row_value

# dateparser is an optional third-party fallback (spec section 7) for free-form
# English dates the regex chain misses. Guarded so the module works without it.
try:
    import dateparser as _dateparser
except ImportError:
    _dateparser = None

# The four fields FR17 counts when scoring a parse. 'title' and 'term_info' are
# deliberately absent: title is always produced (it falls back to the raw
# sentence), so counting it would inflate every score by one, and term_info is
# an audit copy of words already counted through due_date.
_SCORED_FIELDS = ('subject', 'type', 'due_date', 'weight')

# --- bounds on numbers the parser lifts out of free text --------------------
# Every regex below captures a bare \d+, so the number in "in N days" or
# "Term 1 Week N" is whatever the student typed. Feeding that straight to
# datetime.timedelta raised OverflowError (NOT ValueError, so no existing except
# clause caught it) and killed the whole parse for one silly number.
#
# The window is five years either side of today, the same horizon
# _validation.require_within_horizon enforces on a due date that is being SAVED —
# a phrase resolving outside it could never be stored anyway. It is restated here
# rather than imported because this module must never raise: out here an
# out-of-range number means "that was not a date phrase", which the ordered chain
# in _extract_date already knows how to handle by falling through to the next rule.
_MAX_RELATIVE_DAYS = 366 * 5
_MAX_TERM_WEEK = _MAX_RELATIVE_DAYS // 7

# A digit run longer than this is not a typo, it is junk. Tested BEFORE int(),
# because Python 3.11+ itself refuses to convert a very long digit string and
# would raise ValueError from inside the guard meant to prevent an exception.
_MAX_NUMBER_DIGITS = 9

# The largest bulk paste worth looking at, derived from the two published limits
# so it can never contradict them: MAX_BULK_LINES sentences, each at most
# MAX_PARSER_INPUT_LENGTH long. It is a cheap early bail — every surviving line
# is still length-checked individually in parse_bulk.
_MAX_BULK_TEXT_LENGTH = MAX_BULK_LINES * MAX_PARSER_INPUT_LENGTH

# KEY = a lowercase weekday name or the abbreviations a student actually types;
# VALUE = Python's own weekday index (0=Monday), so the value can be handed
# straight to date.weekday() arithmetic in _next_weekday without a lookup table
# of its own. Several keys share a value on purpose ('tue'/'tues'/'tuesday').
#
# ORDER: full names are listed ahead of their abbreviations ('monday' before
# 'mon'), because both regexes built from these keys join them with '|' and
# Python's alternation takes the FIRST branch that matches. This ordering is
# defensive rather than load-bearing — alphabetising the lines would capture
# 'mon' instead of 'monday', but every abbreviation shares its full name's
# index, so the resolved date is identical either way. Worth keeping so the
# capture is the whole word, in case a future rule reads the token itself.
_WEEKDAYS = {
    'monday': 0, 'mon': 0, 'tuesday': 1, 'tue': 1, 'tues': 1,
    'wednesday': 2, 'wed': 2, 'thursday': 3, 'thu': 3, 'thur': 3, 'thurs': 3,
    'friday': 4, 'fri': 4, 'saturday': 5, 'sat': 5, 'sunday': 6, 'sun': 6,
}

# Free-text weekday matcher (_extract_date step 5) EXCLUDES the bare 'sat'
# abbreviation: it collides with the SAT assessment type (School Assessed Task),
# so a bare 'SAT' must never be read as Saturday. 'saturday' still matches. The
# term-week path (_try_parse_week_phrase) has its own position guard. Sorted
# longest-first so multi-char names win the alternation.
_FREE_WEEKDAYS = sorted((k for k in _WEEKDAYS if k != 'sat'), key=len, reverse=True)

# KEY = a lowercase month name or its usual three/four-letter abbreviation;
# VALUE = the 1-12 month number datetime.date wants. Read only by _extract_date
# step 6, which joins the keys into an alternation.
#
# The abbreviation is listed BEFORE the full name here ('jan' before 'january'),
# which is the opposite of _WEEKDAYS and looks like the bug described above. It
# is safe only because step 6's pattern closes with r'\b': at "january" the
# alternation tries 'jan' first, the following 'u' is not a word boundary, and
# the regex backtracks into 'january'. _WEEKDAYS cannot rely on that, because
# its pattern allows a r'\w*' tail that would happily swallow the rest.
_MONTHS = {
    'jan': 1, 'january': 1, 'feb': 2, 'february': 2, 'mar': 3, 'march': 3,
    'apr': 4, 'april': 4, 'may': 5, 'jun': 6, 'june': 6, 'jul': 7, 'july': 7,
    'aug': 8, 'august': 8, 'sep': 9, 'sept': 9, 'september': 9,
    'oct': 10, 'october': 10, 'nov': 11, 'november': 11, 'dec': 12, 'december': 12,
}

# Filler words stripped from the residual when deriving the title. Read only by
# _extract_title. These are the words that GLUE an assessment sentence together
# rather than name anything in it: "Methods SAC2 due Friday" should title as
# "Methods SAC2", not "Methods SAC2 due". Two groups are in here —
#   * ordinary English connectives ('the', 'a', 'for', 'of', 'and', 'is')
#   * the scaffolding of a date or weight phrase ('due', 'worth', 'on', 'by',
#     'at', 'in', 'next', 'this', 'week', 'term')
# The second group is the important one: _extract_title is handed the exact
# spans the matchers consumed, but a student writes plenty of words AROUND
# those spans, so "due" survives the removal of "friday" and "worth" survives
# the removal of "25%".
#
# A set, not a list, because the lookup runs once per word of every parsed line.
# Everything is lowercase; _extract_title lowercases each word before testing.
_TITLE_FILLER = {
    'due', 'worth', 'on', 'by', 'at', 'the', 'a', 'an', 'for', 'is', 'in',
    'next', 'this', 'week', 'term', 'and', 'of',
}


# --- guards on values read back out of the database -------------------------
# The parser reads two user_settings columns, `school_terms` and `subjects`.
# Both are Anvil simpleObject columns, which accept ANY JSON: the Data Tables
# console will happily leave a scalar, a dict, or a list with one hand-typed
# entry missing its dates where a clean list belongs (SAT 5 §6 names this exact
# risk for hand-editable stored data). There is nobody present to correct such a
# cell mid-parse, so both reads go through _validation.safe_list, which drops the
# unusable elements and keeps the rest.

_CANONICAL_SUBJECT_SET = frozenset(CANONICAL_SUBJECTS)


def _is_well_formed_term(value) -> bool:
    """Element predicate for safe_list: one usable `school_terms` entry.

    Accepts exactly the shape notes._validate_school_terms enforces when the
    Settings page WRITES the column — a real Victorian term number plus ISO
    start and end dates — so the read rule and the write rule cannot drift apart.
    """
    if not isinstance(value, dict):
        return False
    term_number = value.get('term')
    # bool is a subclass of int, so a stored True would otherwise read as term 1.
    if not isinstance(term_number, int) or isinstance(term_number, bool):
        return False
    if not (MIN_TERM_NUMBER <= term_number <= MAX_TERM_NUMBER):
        return False
    return (_iso_to_date(value.get('start_date')) is not None
            and _iso_to_date(value.get('end_date')) is not None)


def _is_canonical_subject(value) -> bool:
    """Element predicate for safe_list: one entry of the stored `subjects` list.

    Filtered against the picker catalog that notes.set_subjects validates against,
    so a subject the app no longer offers cannot reach the alias ranking.
    """
    return isinstance(value, str) and value in _CANONICAL_SUBJECT_SET


def _stored_terms(settings_row) -> list:
    """The configured school terms, with unusable entries dropped. Never raises.

    A damaged column degrades to an empty list, which is the documented
    "school_terms not configured" path (FR15): the 'Term X Week Y' phrase stays
    unresolved and confidence falls to LOW, rather than the parse dying.
    """
    stored = _row_value(settings_row, 'school_terms') if settings_row is not None else None
    return safe_list(stored, element_check=_is_well_formed_term)


def _stored_subjects(settings_row) -> list:
    """The student's locked-in subjects, with unusable entries dropped (spec §11)."""
    stored = _row_value(settings_row, 'subjects') if settings_row is not None else None
    return safe_list(stored, element_check=_is_canonical_subject)


def _bounded_int(digits, minimum, maximum):
    """A captured run of digits as an int inside [minimum, maximum], else None.

    Used everywhere a number lifted out of free text reaches datetime arithmetic.
    Returning None rather than raising is deliberate: the caller reads it as
    "this was not a date phrase after all" and falls through to the next rule,
    which keeps the parser's promise to return a best-effort record for anything.
    """
    if not digits or len(digits) > _MAX_NUMBER_DIGITS:
        return None
    value = int(digits)
    if value < minimum or value > maximum:
        return None
    return value


# --- field matchers --------------------------------------------------------

def _match_subject(text: str, user_subjects=None):
    """Which VCE study a sentence is about, by ranked alias match (FR16).

    `text` is one raw assessment sentence (any case; matching is done on a
    lowercased copy). `user_subjects` is the student's locked-in study list
    from user_settings.subjects — a list of CANONICAL_SUBJECTS names, already
    filtered by _stored_subjects, or None/[] when they have not been through
    onboarding yet. It only ever breaks ties; it never adds or removes a
    possible match, so the parser still works for a brand-new account.

    Returns (canonical_subject, matched_alias), both str, or (None, None) when
    no alias appears at all. The alias is returned so _parse_one can quote the
    student's own word back at them in the 'why' string.

    Reads SUBJECT_ALIASES, AMBIGUOUS_BARE_ALIASES and MATHS_GROUP from
    _constants. Touches no data table and never raises.

    WHY RANKING RATHER THAN FIRST-HIT: a real sentence mentions more than one
    alias more often than not — "specialist maths" contains "maths", "health
    survey for PE" names two studies. Returning on the first hit would make the
    answer depend on dict insertion order, which is a property of the constants
    file rather than of what the student wrote. So EVERY hit is collected with
    its position and one winner is chosen by explicit rules:

    1. A hit whose span sits inside a longer hit loses to it, so 'maths'
       never pre-empts 'maths methods' or 'specialist maths' — even when the
       shorter alias belongs to a locked subject.
    2. Unambiguous aliases beat AMBIGUOUS_BARE_ALIASES (ordinary sentence
       words): 'Health survey for PE' parses as Physical Education, while a
       lone 'health essay' still parses as HHD.
    3. A locked subject (spec §11) beats a non-locked one, so a student's own
       studies win alias collisions; the full table still matches as
       fallback when none of their subjects is mentioned.
    4. Earliest mention wins, longer alias breaking position ties.

    Bonus remap: with exactly one locked maths study, a surviving bare
    'math'/'maths'/'mathematics' hit means THAT study, not the generic
    'Mathematics' catch-all.
    """
    # 1. Collect every alias that appears as a WHOLE WORD. The \b guards are
    #    what stop 'sd' firing inside "Wednesday" and 'art' inside "started";
    #    re.escape is belt-and-braces for a future alias containing a '.' or
    #    '+'. Only the FIRST occurrence of each alias is recorded — a second
    #    mention of the same word cannot change which subject it names, and
    #    the first is the one the earliest-mention rule in step 5 wants.
    low = text.lower()
    matches = []   # (start, end, alias, canonical)
    for alias, canonical in SUBJECT_ALIASES.items():
        m = re.search(r'\b' + re.escape(alias) + r'\b', low)
        if m:
            matches.append((m.start(), m.end(), alias, canonical))
    if not matches:
        return None, None

    # 2. The bare-maths remap. 'math'/'maths'/'mathematics' resolve to the
    #    'Mathematics' catch-all, which is a group heading and NOT a study a
    #    student can be enrolled in — saving it would fail the canonical-subject
    #    check downstream. When the student has locked in exactly one maths
    #    study, "maths SAC" unambiguously means that one, so the catch-all is
    #    rewritten to it here. With two maths studies (Methods and Spesh, a
    #    common combination) there is no honest answer, so the catch-all is left
    #    alone and the student picks in the preview.
    #
    #    Done BEFORE ranking, not after, so the remapped name is what the
    #    locked-subject test in _rank compares — otherwise a hit that is really
    #    the student's own Methods would rank as an outsider.
    maths = [s for s in (user_subjects or []) if s in MATHS_GROUP]
    if len(maths) == 1:
        matches = [(start, end, alias,
                    maths[0] if canonical == 'Mathematics' else canonical)
                   for start, end, alias, canonical in matches]

    # 3. Containment filter. In "specialist maths SAC" the table matches both
    #    'specialist maths' (0-16) and 'maths' (11-16); the short hit sits
    #    wholly inside the long one and is dropped, so the more specific study
    #    wins. This is a filter rather than another sort key on purpose: a
    #    contained hit is not merely worse evidence, it is the SAME words being
    #    counted twice, and leaving it in the list would let it win rule 5 by
    #    virtue of belonging to a locked subject.
    #
    #    `o is not m` compares identity, not value, so a hit is not judged
    #    against itself; aliases are unique dict keys, so no two tuples can
    #    collide. The length test is STRICT (>), which means two hits covering
    #    exactly the same span both survive to be ranked normally.
    def _contained(m):
        return any(o is not m
                   and o[0] <= m[0] and m[1] <= o[1]
                   and (o[1] - o[0]) > (m[1] - m[0])
                   for o in matches)
    survivors = [m for m in matches if not _contained(m)]

    # 4. `locked` is the student's own studies as a set, for O(1) membership in
    #    the sort key below. Empty when onboarding has not run.
    locked = set(user_subjects or [])

    # 5. One sort key holding all four tie-breakers, in priority order. Python
    #    sorts tuples left to right and False sorts before True, so each
    #    element is phrased as "the bad case is True":
    #      [0] ambiguity  — a word that is also ordinary English ('health',
    #                       'general', 'french') loses to one that is only ever
    #                       a subject name. FIRST on purpose: an unambiguous
    #                       alias wins from anywhere in the sentence, even
    #                       against an ambiguous word naming a LOCKED subject.
    #      [1] not locked — among equally strong aliases, the student's own
    #                       studies win. Neutralised to False for everyone when
    #                       `locked` is empty, so a new account ranks on
    #                       position alone instead of demoting every hit.
    #      [2] start      — earliest mention wins; a sentence names its subject
    #                       before it describes the task.
    #      [3] -(length)  — NEGATED so that longer sorts first, breaking a
    #                       position tie toward the more specific alias.
    def _rank(m):
        start, end, alias, canonical = m
        return (alias in AMBIGUOUS_BARE_ALIASES,
                canonical not in locked if locked else False,
                start,
                -(end - start))
    survivors.sort(key=_rank)
    # survivors is non-empty: `matches` was non-empty and the longest hit in it
    # can never be contained by anything, so at least one always survives.
    _, _, alias, canonical = survivors[0]
    return canonical, alias


def _match_type(text: str):
    """What kind of assessment the sentence describes (FR01, FR03 vocabulary).

    `text` is one raw assessment sentence, any case. Returns
    (canonical_type, matched_keyword) where canonical_type is always a member
    of VALID_TYPES — so a parse can never produce a type the write path would
    reject — and matched_keyword is the literal text that fired, for the 'why'
    string.

    Falls back to ('other', None). That None is load-bearing: it is how
    _parse_one tells a type the student actually named from the catch-all, and
    only the former counts toward the confidence score (FR17). Reads
    TYPE_KEYWORDS; touches no table; never raises.
    """
    low = text.lower()
    # Two nested loops rather than one combined regex, because the ORDER of the
    # search is the precedence rule and must stay visible. TYPE_KEYWORDS is
    # walked in its own insertion order and the first keyword to fire anywhere
    # in the sentence wins, so a line naming both a SAC and a SAT is filed as a
    # SAC. A single alternation would instead pick whichever word came first in
    # the SENTENCE, which is not the rule TYPE_KEYWORDS documents.
    for canonical, keywords in TYPE_KEYWORDS.items():
        for kw in keywords:
            # Allow trailing digits so 'SAC2' matches the 'sac' keyword.
            m = re.search(r'\b' + re.escape(kw) + r'\d*\b', low)
            if m:
                return canonical, m.group(0)
    # Reached only when nothing matched: 'other' carries an empty keyword list
    # in TYPE_KEYWORDS, so the loop above cannot return it.
    return 'other', None


def _extract_weight(text: str):
    """How much the assessment is worth, as a percentage of the study score.

    `text` is one raw assessment sentence. Returns (weight, matched_text) where
    weight is a float and matched_text is the phrase that produced it ('25%',
    '12.5 percent'), or (None, None) when no percentage is written. Feeds
    assessments.weight, which _validation bounds to MIN_WEIGHT..MAX_WEIGHT.

    Deliberately does NOT range-check here: see the inline note below.
    """
    # The '%' or the word 'percent' is required — a bare number is never read
    # as a weight, because "SAC 2" and "week 5" put bare numbers in almost every
    # sentence. \s* allows "25 %" as well as "25%", and the optional decimal
    # group accepts the half-marks some studies use ("12.5%").
    m = re.search(r'(\d+(?:\.\d+)?)\s*(?:%|percent)', text, re.IGNORECASE)
    if not m:
        return None, None
    # The regex already guarantees a float-shaped capture, so this try is for
    # the impossible case only; it is here because this function sits on the
    # never-raises path and a bare float() is the one call in it that could.
    try:
        weight = float(m.group(1))
    except (ValueError, TypeError):
        return None, None
    # The capture is an unbounded \d+, so a long enough digit run overflows
    # float() to inf. An out-of-range percentage is still reported (the preview
    # shows it and create_assessment explains the 0-100 rule in one clear
    # message), but inf is not a number at all: it cannot survive the JSON trip
    # to the client, so it is treated as "no weight found".
    if not math.isfinite(weight):
        return None, None
    return weight, m.group(0)


# --- date extraction -------------------------------------------------------

def _try_parse_week_phrase(text: str, settings_row):
    """Resolve 'Term X Week Y' (with optional weekday) to a date (FR15).

    `text` is one raw assessment sentence. `settings_row` is the student's
    user_settings Row (or None), read for its `school_terms` column only — a
    list of {'term': 1-4, 'start_date': 'YYYY-MM-DD', 'end_date': 'YYYY-MM-DD'}
    dicts the student fills in on the Settings page.

    Returns (date, matched_text) or (None, None) when the phrase is absent, when
    school_terms is empty/unconfigured (or holds nothing usable — see
    _stored_terms), when the named term is missing, or when the week number is
    not a plausible one. The empty-config case is what forces LOW confidence per
    FR15; every other case above degrades to it rather than raising.

    matched_text is the whole phrase as typed ("term 2 monday week 4"), which
    _extract_date passes on as `term_info` so the audit column preserves the
    student's own wording next to the resolved date.
    """
    # The optional weekday attaches directly before 'week' (after 'term N'), so
    # an assessment type like 'SAT' before 'term' is NOT misread as 'Saturday'.
    m = re.search(
        r'term\s*(\d)\s*'
        r'(?:(' + '|'.join(_WEEKDAYS) + r')\w*\s+)?'
        r'week\s*(\d+)',
        text, re.IGNORECASE,
    )
    if not m:
        return None, None

    # Read through the stored-value guard (see _stored_terms): the column is an
    # Anvil simpleObject the Data Tables console can leave in any shape at all.
    terms = _stored_terms(settings_row)
    if not terms:
        return None, None

    term_n = int(m.group(1))          # a single digit by construction
    weekday_token = (m.group(2) or '').lower()
    # Bounded because week_n drives a timedelta below: 'Term 1 Week 99999999999'
    # is not a week number, so the phrase is treated as unresolvable.
    week_n = _bounded_int(m.group(3), 1, _MAX_TERM_WEEK)
    if week_n is None:
        return None, None

    # The term the student named may simply not be configured — they set up
    # Term 1 and 2 in February and never came back. next(..., None) is the
    # cheapest way to say "the first match, or nothing" without an index or a
    # try/except around a list comprehension.
    term = next((t for t in terms if t.get('term') == term_n), None)
    if term is None:
        return None, None

    start = _iso_to_date(term.get('start_date'))
    if start is None:
        return None, None

    # Week 1 IS the term's start date, so the offset is (week_n - 1) whole
    # weeks — week 1 adds nothing, week 5 adds 28 days. FR15 calls the answer
    # "the Monday of the requested week", which holds because a Victorian term
    # starts on a Monday and the student enters that date.
    due = start + datetime.timedelta(days=(week_n - 1) * 7)
    # An optional weekday shifts within that week using Python's own 0=Monday
    # index, which is why _WEEKDAYS stores those numbers rather than names.
    # This ADDS to the week's start, so it assumes start_date is a Monday; a
    # mid-week start_date would skew every weekday-qualified phrase, which is
    # the trade for not asking the student for anything more than two dates.
    if weekday_token:
        due = due + datetime.timedelta(days=_WEEKDAYS[weekday_token])

    # Sanity check against the term's own end date: "term 1 week 30" parses
    # cleanly and lands in the spring holidays. A date outside the term the
    # student named means the phrase was not really a week reference, so it is
    # rejected and _extract_date falls through to its later rules rather than
    # returning a confidently wrong date. Skipped when end_date is unreadable,
    # since a missing bound is no evidence against the date.
    end = _iso_to_date(term.get('end_date'))
    if end is not None and not (start <= due <= end):
        return None, None

    return due, m.group(0).strip()


def _iso_to_date(s):
    """'YYYY-MM-DD' -> datetime.date, or None for anything unreadable.

    `s` is a value read back out of the school_terms simpleObject column, so it
    is whatever JSON happens to be stored — a string if the Settings page wrote
    it, but possibly a number, a None, or a mistyped '2026-13-01' if the Data
    Tables console did. Returning None rather than raising is what lets
    _is_well_formed_term use this as a plain predicate.
    """
    # The isinstance guard comes first because fromisoformat raises TypeError
    # (not ValueError) on a non-string, and the empty-string case is caught by
    # `not s` so the common "column never filled in" path costs no exception.
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _next_weekday(today: datetime.date, target_wd: int) -> datetime.date:
    """The next date on which it is `target_wd` (0=Mon). Never returns today.

    `today` is the student's local today from _user_today; `target_wd` is a
    0-6 value out of _WEEKDAYS. Used by _extract_date step 5 for bare weekday
    phrases ('due friday').
    """
    # Modulo 7 wraps the difference into 0-6, so a target earlier in the week
    # than today rolls forward into next week without any branching.
    delta = (target_wd - today.weekday()) % 7
    # delta == 0 means the student typed today's own weekday name. That is read
    # as NEXT week, not today: someone writing "due friday" on a Friday means
    # the coming Friday. "today" has its own keyword in _extract_date step 3 for
    # the other reading.
    if delta == 0:
        delta = 7
    return today + datetime.timedelta(days=delta)


def _extract_date(text: str, today: datetime.date, settings_row):
    """When the assessment is due. Ordered regex chain; first match wins.

    `text` is one raw assessment sentence. `today` is the student's local today
    (from _user_today, so a late-night parse does not resolve 'tomorrow' off
    the server's UTC clock). `settings_row` is passed through to the FR15 term
    resolver and is otherwise unused.

    Returns a 4-tuple:
      [0] date        the resolved due date, or None if nothing matched
      [1] why         the provenance sentence for the preview, or None
      [2] term_info   the literal 'Term X Week Y' phrase if one was written,
                      or None — returned by EVERY branch, including the ones
                      that resolved the date some other way
      [3] matched     the literal text this branch consumed, or None; handed
                      to _extract_title so the date words are not left in the
                      title. None from the dateparser branch, which reads the
                      whole sentence and cannot say which words it used.

    THE ORDER OF THE CHAIN IS THE RULE, most specific first. A sentence like
    "term 2 week 5 friday" satisfies three of these branches at once, and the
    earliest one is the most precise reading of what the student meant. The
    dateparser fallback is deliberately last: it will find a date in almost
    anything, so letting it run before the explicit rules would hide them.

    Never raises. Every branch that could — bad month/day combinations, huge
    numbers reaching timedelta, dateparser's own errors — is guarded, because
    an unparseable date must degrade to LOW confidence, not to a traceback.
    """
    low = text.lower()

    # 1. Term X Week Y (uses school_terms; None when unconfigured -> FR15 LOW).
    due, matched = _try_parse_week_phrase(text, settings_row)
    if due is not None:
        why = 'matched "%s" → %s' % (matched, due.strftime('%d %b %Y'))
        return due, why, matched, matched
    # The phrase is captured even when it could NOT be resolved, because every
    # branch below returns it as `term_info`: the audit column records the words
    # the student actually typed ("term 2 week 4") whether or not school_terms
    # could turn them into a date.
    week_phrase = re.search(r'term\s*\d\s*week\s*\d+', low)
    week_phrase_text = week_phrase.group(0) if week_phrase else None

    # 2. DD/MM or DD/MM/YYYY. Day-first because the client and the whole user
    #    base are Australian (NFR08 exists for the same reason); '3/4' is the
    #    3rd of April here, never the 4th of March. Note this branch reads
    #    `text` rather than `low` — there are no letters in it to case-fold.
    m = re.search(r'\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b', text)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        # No year written is the normal case, and the current year is the
        # starting assumption; the rollover below fixes it when that is past.
        year = today.year
        if m.group(3):
            year = int(m.group(3))
            # A two-digit year is this century: '26' is 2026. Students write
            # dates from the current school year, so there is no window in
            # which '99' should mean 1999.
            if year < 100:
                year += 2000
        d = _safe_date(year, month, day)
        if d is not None:
            # No explicit year and already past -> roll to next year (matches the
            # month-name branch and the parser's future-oriented intent).
            if m.group(3) is None and d < today:
                d = _safe_date(today.year + 1, month, day) or d
            return d, 'matched "%s" → %s' % (m.group(0), d.strftime('%d %b %Y')), week_phrase_text, m.group(0)

    # 3. 'tomorrow' / 'today'.
    if re.search(r'\btomorrow\b', low):
        d = today + datetime.timedelta(days=1)
        return d, 'matched "tomorrow" → %s' % d.strftime('%d %b %Y'), week_phrase_text, 'tomorrow'
    if re.search(r'\btoday\b', low):
        return today, 'matched "today" → %s' % today.strftime('%d %b %Y'), week_phrase_text, 'today'

    # 4. 'in N days'. N is bounded to the five-year horizon: past that it is not
    #    a plan, so the phrase is left to the rules below rather than resolved.
    #    (Unbounded, int(N) reached timedelta and raised OverflowError, which no
    #    caller caught — one absurd number killed the entire parse.)
    m = re.search(r'\bin\s+(\d+)\s+days?\b', low)
    if m:
        days_ahead = _bounded_int(m.group(1), 0, _MAX_RELATIVE_DAYS)
        if days_ahead is not None:
            d = today + datetime.timedelta(days=days_ahead)
            return d, 'matched "%s" → %s' % (m.group(0), d.strftime('%d %b %Y')), week_phrase_text, m.group(0)

    # 5. Weekday names ('next friday', 'friday'). Bare 'sat' excluded (SAT type).
    m = re.search(r'\b(?:next\s+|this\s+)?(' + '|'.join(_FREE_WEEKDAYS) + r')\b', low)
    if m:
        d = _next_weekday(today, _WEEKDAYS[m.group(1)])
        return d, 'matched "%s" → %s' % (m.group(0), d.strftime('%d %b %Y')), week_phrase_text, m.group(0)

    # 6. Month-name dates, in both orders a student might write them. The
    #    day-first form is tried FIRST and the month-first form only if it
    #    fails, matching the Australian reading — though with a month NAME
    #    present the two orders are unambiguous anyway, so this is about
    #    which spelling is more common rather than about correctness.
    #    (?:st|nd|rd|th)? absorbs the ordinal suffix in "15th March".
    #    Both branches roll a past date into next year, exactly as step 2 does.
    names = '|'.join(_MONTHS)
    m = re.search(r'\b(\d{1,2})(?:st|nd|rd|th)?\s+(' + names + r')\b', low)
    if not m:
        m2 = re.search(r'\b(' + names + r')\s+(\d{1,2})(?:st|nd|rd|th)?\b', low)
        if m2:
            month, day = _MONTHS[m2.group(1)], int(m2.group(2))
            d = _safe_date(today.year, month, day)
            if d is not None:
                if d < today:
                    d = _safe_date(today.year + 1, month, day) or d
                return d, 'matched "%s" → %s' % (m2.group(0), d.strftime('%d %b %Y')), week_phrase_text, m2.group(0)
    else:
        day, month = int(m.group(1)), _MONTHS[m.group(2)]
        d = _safe_date(today.year, month, day)
        if d is not None:
            if d < today:
                d = _safe_date(today.year + 1, month, day) or d
            return d, 'matched "%s" → %s' % (m.group(0), d.strftime('%d %b %Y')), week_phrase_text, m.group(0)

    # 7. dateparser fallback (optional dependency, spec section 7): free-form
    #    English the rules above do not cover, such as "end of next month".
    #    PREFER_DATES_FROM='future' because an assessment is always ahead of
    #    the student, and RELATIVE_BASE pins the library's idea of "now" to the
    #    student's local today rather than the server's clock.
    if _dateparser is not None:
        # A BARE except, which is normally wrong, is right here: this is a
        # third-party library given arbitrary student text, its exception types
        # are not part of its documented contract, and the correct answer for
        # any failure is the same — no date, keep the rest of the parse.
        try:
            parsed = _dateparser.parse(
                text,
                settings={'PREFER_DATES_FROM': 'future', 'RELATIVE_BASE': datetime.datetime(today.year, today.month, today.day)},
            )
        except Exception:
            parsed = None
        if parsed is not None:
            d = parsed.date()
            # The 'why' is hedged ('interpreted' rather than 'matched ...') and
            # matched_text is None, because the library read the whole sentence
            # and cannot report which words it used. That None means step 7
            # strips nothing from the title — the safe direction, since guessing
            # a span could delete words the student wanted to keep.
            return d, 'interpreted the date as %s' % d.strftime('%d %b %Y'), week_phrase_text, None

    # Nothing matched. term_info is still returned: an unresolvable "term 2
    # week 4" is recorded verbatim so the student can see what they typed
    # beside the empty date field (FR15).
    return None, None, week_phrase_text, None


def _safe_date(year, month, day):
    """datetime.date(year, month, day), or None if that is not a real date.

    Every numeric date branch in _extract_date builds its parts from separate
    \\d captures, so nothing has checked that they combine: '31/02' and
    '30 february' are both well-formed phrases naming a day that does not
    exist. Constructing and catching is used instead of checking month lengths
    by hand because datetime already knows about leap years and month lengths,
    and duplicating that here is exactly the sort of second copy that goes
    stale. None means "not a date", which each caller reads as "keep looking".
    """
    try:
        return datetime.date(year, month, day)
    except (ValueError, TypeError):
        return None


# --- title -----------------------------------------------------------------

def _find_week_phrase_text(text: str):
    """The literal 'week N' / 'term N week M' phrase in text, or None.

    A SEPARATE, looser pattern from the one _try_parse_week_phrase resolves
    with, and only _extract_title uses it. Its job is removal, not resolution,
    so it accepts a bare 'week 5' with no term in front of it — those words
    belong to the due date and must not survive into the title, whether or not
    school_terms could turn them into an actual date.
    """
    m = re.search(r'(?:term\s*\d\s*)?week\s*\d+', text, re.IGNORECASE)
    return m.group(0) if m else None


def _extract_title(raw: str, matched_spans: list) -> str:
    """Residual title after removing date/weight/week spans, filler and orphans.

    `raw` is the original sentence. `matched_spans` is a list of literal
    substrings the other matchers consumed (any of them may be None, which is
    skipped). Returns a string of at most MAX_TITLE_LENGTH chars. It produces a
    title for any input with a word in it, which is why 'title' is not one of
    the fields the confidence score counts.

    Subject and type words are intentionally kept — for a fully-structured input
    like 'Methods SAC2 ...' they form the natural title 'Methods SAC2'.

    SUBTRACTIVE rather than constructive: it is easier to say which words are
    definitely NOT part of a title than to say which are. Whatever the student
    wrote that no rule claimed is assumed to be what they were calling the task.
    """
    # 1. Delete the spans the date and weight matchers already consumed.
    #    re.escape because a span is literal student text that may contain
    #    regex characters (the '/' and '.' in "12/03" are harmless, but a '%'
    #    or '+' in a future span would not be). Replaced with a SPACE, not '',
    #    so cutting from the middle of "SAC2due" style input cannot fuse two
    #    words together. IGNORECASE because some spans were matched against a
    #    lowercased copy and so may not match `raw`'s own casing.
    residual = raw
    for span in matched_spans:
        if span:
            residual = re.sub(re.escape(span), ' ', residual, flags=re.IGNORECASE)
    # 2. Walk what is left word by word, keeping the ORIGINAL casing of each
    #    kept word while testing a lowercased, de-punctuated copy. That is why
    #    `w` is appended and `cw` is only ever compared: the student's "Methods
    #    SAC2" must come back capitalised as they wrote it.
    kept = []
    for w in re.split(r'\s+', residual):
        if not w:
            continue
        cw = w.lower().strip('.,;:!?%')
        if not cw or cw in _TITLE_FILLER:
            continue
        # A word that is now nothing but digits is an orphan left behind by a
        # partial removal — "week 5" loses "week" to _TITLE_FILLER and would
        # otherwise leave a bare "5" sitting in the title.
        if re.fullmatch(r'\d+', cw):   # drop orphaned numbers (e.g. leftover 'week 5')
            continue
        kept.append(w)
    title = ' '.join(kept).strip(' .,;:-')
    # 3. Everything can be stripped away — "due friday" is entirely filler and
    #    date. An empty title would be useless in the list view, so the raw
    #    sentence stands in: the student sees what they typed and can rename it
    #    in the preview, which beats a blank row.
    if not title:
        # Fall back to the raw input trimmed of trailing punctuation.
        title = raw.strip(' .,;:-')
    # Truncated to the same limit assessments.create_assessment enforces on the
    # title column, so a parsed record can never be rejected for a length the
    # parser itself produced.
    return title[:MAX_TITLE_LENGTH]


# --- scoring ---------------------------------------------------------------

def _score(detected: set) -> str:
    """Confidence in a parse, from how many fields it genuinely found (FR17).

    `detected` is the set of field names _parse_one actually recognised — a
    subset of {'subject', 'type', 'due_date', 'weight'} in practice, though it
    is intersected with _SCORED_FIELDS rather than trusted, so adding a field to
    the result dict later cannot quietly inflate every score.

    Returns one of VALID_CONFIDENCE: 'HIGH' (4 fields), 'MEDIUM' (2-3), 'LOW'
    (0-1), which are FR17's bands exactly. The client shows this as a coloured
    pill above the preview, and the bulk dialog leaves LOW rows unticked by
    default, so a poor parse costs the student a click rather than a wrong row.

    WHAT COUNTS AS "GENUINELY FOUND" is decided by the caller, not here, and it
    is the interesting half of the rule:
      * 'type' is added only when a TYPE_KEYWORDS keyword actually fired. Every
        parse gets a type — 'other' is the fallback — so counting the field's
        presence would give a free point to every sentence.
      * 'due_date' is added only when a date was resolved. A 'Term X Week Y'
        phrase with no school_terms configured therefore scores nothing, which
        is the LOW-confidence-on-missing-config behaviour FR15 asks for.

    NO FIELD IS WEIGHTED, and nothing here is a hard gate: a sentence with a
    subject and a weight but no date scores MEDIUM. What is true is that HIGH
    needs all four, so a parse with no resolvable due date can never reach it.
    Nothing downstream refuses to save on confidence either — the score is
    advice to the student, and the preview is where they act on it.
    """
    # Counting rather than checking named fields keeps the rule symmetric: no
    # single field can make or break a score on its own.
    hits = len(detected & set(_SCORED_FIELDS))
    # >= rather than == so the bands still hold if _SCORED_FIELDS ever grows;
    # with four scored fields, 'hits >= 4' can only mean all of them.
    if hits >= 4:
        return 'HIGH'
    if hits >= 2:
        return 'MEDIUM'
    return 'LOW'


# --- orchestration ---------------------------------------------------------

def _parse_one(line: str, today: datetime.date, settings_row) -> dict:
    """Run every matcher over one sentence and assemble the result dict.

    The single place the parse is put together, shared by both callables so
    parse_text and parse_bulk cannot drift apart in what they produce.

    `line` is one sentence (None is tolerated and read as blank). `today` is
    the student's local today. `settings_row` is their user_settings Row, read
    for `subjects` and `school_terms` only.

    Returns the dict documented at the top of this module: 'fields', 'why',
    'confidence', 'source_text'. Writes nothing. NEVER RAISES — every matcher
    it calls answers "not found" instead of throwing, which is what lets the
    bulk path keep going past a line it could not understand.
    """
    line = (line or '').strip()

    # 1. The matchers are independent — each one searches the whole sentence
    #    for its own thing — so the order of these four lines does not matter.
    #    They read the sentence rather than each other's leftovers, so a word
    #    can legitimately serve two of them at once (the '2' in "SAC2").
    #
    #    user_subjects is the student's locked-in studies from settings; it
    #    only breaks ties inside _match_subject and is not needed elsewhere.
    #    *_src holds the literal text that produced each value, kept for the
    #    'why' strings and for title stripping.
    user_subjects = _stored_subjects(settings_row)
    subject, subject_src = _match_subject(line, user_subjects)
    type_value, type_src = _match_type(line)
    weight, weight_src = _extract_weight(line)
    due_date, date_why, term_info, date_src = _extract_date(line, today, settings_row)

    # 2. The title is whatever is left over, so it must be built AFTER the
    #    matchers have reported which words they consumed. The week phrase is
    #    re-found separately because date_src holds only what the winning
    #    branch matched: when step 2's '12/03' resolved the date, an
    #    accompanying "week 5" was never claimed by anyone and would otherwise
    #    survive into the title.
    #    Strip date/weight/week phrases from the title; keep subject/type words.
    title = _extract_title(line, [weight_src, date_src, _find_week_phrase_text(line)])

    # 3. 'why' and 'detected' are filled in the same pass because they answer
    #    the same question: a field is explained to the student exactly when it
    #    counts toward the score, so the pill and the provenance lines can never
    #    disagree about what was found.
    #
    #    Each test names the value that proves a REAL detection, which is not
    #    always the field itself:
    #      * subject — None when no alias appeared.
    #      * type    — tested on type_src, not type_value: type_value is 'other'
    #                  for an unrecognised sentence, and 'other' is a fallback
    #                  rather than a finding. This is the only asymmetric one.
    #      * due_date/weight — None when unresolved.
    why = {}
    detected = set()
    if subject is not None:
        why['subject'] = 'matched "%s" → %s' % (subject_src, subject)
        detected.add('subject')
    if type_src is not None:
        why['type'] = 'matched "%s" → %s' % (type_src, type_value)
        detected.add('type')
    if due_date is not None:
        # Reuses the sentence _extract_date already built, because only that
        # function knows which of its seven branches fired.
        why['due_date'] = date_why
        detected.add('due_date')
    if weight is not None:
        # '%g' rather than '%s' so a whole-number weight reads "25%" and not
        # "25.0%", while 12.5 still shows its half.
        why['weight'] = 'matched "%s" → %s%%' % (weight_src, ('%g' % weight))
        detected.add('weight')

    # 4. 'fields' mirrors the assessment columns the client will submit, so the
    #    preview form can bind to it directly. source_text is the stripped
    #    sentence, stored on the row as the parser's audit trail (spec §3.2).
    return {
        'fields': {
            'title': title,
            'subject': subject,
            'type': type_value,
            'due_date': due_date,
            'weight': weight,
            'term_info': term_info,
        },
        'why': why,
        'confidence': _score(detected),
        'source_text': line,
    }


@anvil.server.callable
def parse_text(s: str) -> dict:
    """Parse one assessment sentence into a preview dict (FR01, FR17).

    The Parse button on the dashboard calls this. It never writes to the DB —
    the student sees the result in AssessmentEditorForm mode='preview' and
    presses Save (which calls assessments.create_assessment) or Cancel.

    `s` is the raw sentence, at most MAX_PARSER_INPUT_LENGTH (500) characters.
    Returns the result dict documented at the top of this module.

    Raises ValueError when the box is empty or the sentence is over
    MAX_PARSER_INPUT_LENGTH: a blank parse used to return a LOW-confidence record
    with an empty title, which tells the student nothing about what went wrong.
    Also raises (from _require_user) when nobody is logged in — NFR03, and the
    reason the settings read below is safe to scope to one user.
    """
    user = _require_user()
    # Validated before any work is done, so a bad input costs one message rather
    # than a settings read and a full regex chain over an unbounded string.
    raw_text = require_text(s, 'Assessment text', MAX_PARSER_INPUT_LENGTH)
    # One settings read serves the whole parse: _stored_subjects and
    # _try_parse_week_phrase both take the Row rather than fetching their own,
    # so a parse costs a single table hit (NFR01).
    settings_row = _get_or_create_settings(user)
    # Resolved from the student's stored timezone, not the server's clock, so
    # 'tomorrow' typed at 11pm means the day they mean.
    today = _user_today(settings_row)
    return _parse_one(raw_text, today, settings_row)


@anvil.server.callable
def parse_bulk(s: str) -> list:
    """Parse one assessment per non-blank line; each result carries 'line_index'.

    The parsing half of FR02. `s` is the whole bulk-add box: at most
    MAX_BULK_LINES (100) lines, each at most MAX_PARSER_INPUT_LENGTH (500)
    characters. Returns a list of the usual result dicts, one per non-blank
    line, each with an extra 'line_index' key — the 0-based position of that
    line in the ORIGINAL paste, which the bulk dialog turns into the "Line 7"
    label beside a row. Blank lines produce no entry, so list position and
    line_index deliberately do not agree.

    Writes nothing, exactly like parse_text: the client tick-boxes the rows it
    wants and assessments.create_bulk_assessments does the writing.

    Raises ValueError when the paste is empty, holds more than MAX_BULK_LINES
    lines, or contains a single line longer than MAX_PARSER_INPUT_LENGTH — the
    same per-sentence limit parse_text applies, so one paste cannot slip past a
    bound the single-line path enforces.
    """
    user = _require_user()
    # Checked against the whole paste, then split from the ORIGINAL string:
    # require_text returns a stripped copy, and splitting that would shift every
    # 'line_index' whenever the paste happens to start with a blank line.
    require_text(s, 'Bulk assessment text', _MAX_BULK_TEXT_LENGTH)
    lines = require_list((s or '').splitlines(), 'Bulk assessment text')
    # Counted BEFORE any parsing, so a 5,000-line paste costs one message
    # instead of five thousand regex chains (NFR01). The message quotes both
    # numbers because "too many lines" alone does not tell the student how much
    # to cut.
    if len(lines) > MAX_BULK_LINES:
        raise ValueError(
            'That is %d lines — paste at most %d at a time.'
            % (len(lines), MAX_BULK_LINES))

    # Read once for the whole paste rather than per line: 100 lines would
    # otherwise mean 100 identical settings lookups (NFR01).
    settings_row = _get_or_create_settings(user)
    today = _user_today(settings_row)
    results = []
    for i, line in enumerate(lines):
        # Blank lines are skipped, not rejected: a pasted list normally has them.
        if not line.strip():
            continue
        # The label names the line by its position on screen (1-based), so the
        # student can find the offending line in the box they just pasted into.
        clean_line = require_text(line, 'Line %d' % (i + 1), MAX_PARSER_INPUT_LENGTH)
        result = _parse_one(clean_line, today, settings_row)
        # `i` counts every line of the paste, blanks included, which is what
        # makes it usable as a pointer back into the box the student is looking
        # at. len(results) would count only the lines that survived and would
        # name the wrong one as soon as a blank line appears.
        result['line_index'] = i
        results.append(result)
    return results
