import anvil.secrets
import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""Text parser for assessment specifications (FR01, FR15, FR16, FR17).

Exposes parse_text(s) and parse_bulk(s) as @anvil.server.callable. Pure parser
logic only: NO table writes. parse_text returns a plain dict that the client
previews (AssessmentEditorForm mode='preview') and then passes to
assessments.create_assessment.

Return shape (spec section 2):
    {
      'fields': {'title', 'subject', 'type', 'due_date', 'weight', 'term_info'},
      'why':    {<field>: '<provenance string>'},   # detected fields only
      'confidence': 'HIGH' | 'MEDIUM' | 'LOW',
      'source_text': <original input>,
    }

Confidence (_score): counts genuinely-detected fields among
{subject, type, due_date, weight}; >=4 HIGH, 2-3 MEDIUM, <2 LOW. 'type' counts
only when a keyword actually fired (not the 'other' fallback); 'due_date' from a
'Term X Week Y' phrase counts only when school_terms is configured (FR15).

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

_MONTHS = {
    'jan': 1, 'january': 1, 'feb': 2, 'february': 2, 'mar': 3, 'march': 3,
    'apr': 4, 'april': 4, 'may': 5, 'jun': 6, 'june': 6, 'jul': 7, 'july': 7,
    'aug': 8, 'august': 8, 'sep': 9, 'sept': 9, 'september': 9,
    'oct': 10, 'october': 10, 'nov': 11, 'november': 11, 'dec': 12, 'december': 12,
}

# Filler words stripped from the residual when deriving the title.
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
    """Return (canonical_subject, matched_alias) or (None, None).

    Collects EVERY alias hit with its span, then picks one winner:

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
    low = text.lower()
    matches = []   # (start, end, alias, canonical)
    for alias, canonical in SUBJECT_ALIASES.items():
        m = re.search(r'\b' + re.escape(alias) + r'\b', low)
        if m:
            matches.append((m.start(), m.end(), alias, canonical))
    if not matches:
        return None, None

    maths = [s for s in (user_subjects or []) if s in MATHS_GROUP]
    if len(maths) == 1:
        matches = [(start, end, alias,
                    maths[0] if canonical == 'Mathematics' else canonical)
                   for start, end, alias, canonical in matches]

    def _contained(m):
        return any(o is not m
                   and o[0] <= m[0] and m[1] <= o[1]
                   and (o[1] - o[0]) > (m[1] - m[0])
                   for o in matches)
    survivors = [m for m in matches if not _contained(m)]

    locked = set(user_subjects or [])

    def _rank(m):
        start, end, alias, canonical = m
        return (alias in AMBIGUOUS_BARE_ALIASES,
                canonical not in locked if locked else False,
                start,
                -(end - start))
    survivors.sort(key=_rank)
    _, _, alias, canonical = survivors[0]
    return canonical, alias


def _match_type(text: str):
    """Return (canonical_type, matched_keyword). Falls back to ('other', None)."""
    low = text.lower()
    for canonical, keywords in TYPE_KEYWORDS.items():
        for kw in keywords:
            # Allow trailing digits so 'SAC2' matches the 'sac' keyword.
            m = re.search(r'\b' + re.escape(kw) + r'\d*\b', low)
            if m:
                return canonical, m.group(0)
    return 'other', None


def _extract_weight(text: str):
    """Return (weight_float, matched_text) or (None, None). Matches '25%', '25 percent'."""
    m = re.search(r'(\d+(?:\.\d+)?)\s*(?:%|percent)', text, re.IGNORECASE)
    if not m:
        return None, None
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

    Returns (date, matched_text) or (None, None) when the phrase is absent, when
    school_terms is empty/unconfigured (or holds nothing usable — see
    _stored_terms), when the named term is missing, or when the week number is
    not a plausible one. The empty-config case is what forces LOW confidence per
    FR15; every other case above degrades to it rather than raising.
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

    term = next((t for t in terms if t.get('term') == term_n), None)
    if term is None:
        return None, None

    start = _iso_to_date(term.get('start_date'))
    if start is None:
        return None, None

    due = start + datetime.timedelta(days=(week_n - 1) * 7)
    if weekday_token:
        due = due + datetime.timedelta(days=_WEEKDAYS[weekday_token])

    end = _iso_to_date(term.get('end_date'))
    if end is not None and not (start <= due <= end):
        return None, None

    return due, m.group(0).strip()


def _iso_to_date(s):
    """'YYYY-MM-DD' -> date, or None."""
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _next_weekday(today: datetime.date, target_wd: int) -> datetime.date:
    """The next date on which it is `target_wd` (0=Mon). Never returns today."""
    delta = (target_wd - today.weekday()) % 7
    if delta == 0:
        delta = 7
    return today + datetime.timedelta(days=delta)


def _extract_date(text: str, today: datetime.date, settings_row):
    """Ordered regex chain; first match wins.

    Returns (date_or_None, why_or_None, term_info_or_None, matched_text_or_None).
    term_info carries the original 'Term X Week Y' phrase for the audit column.
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

    # 2. DD/MM or DD/MM/YYYY (Australian day-first).
    m = re.search(r'\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b', text)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = today.year
        if m.group(3):
            year = int(m.group(3))
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

    # 6. Month-name dates ('15 March', 'March 15').
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

    # 7. dateparser fallback (optional dependency).
    if _dateparser is not None:
        try:
            parsed = _dateparser.parse(
                text,
                settings={'PREFER_DATES_FROM': 'future', 'RELATIVE_BASE': datetime.datetime(today.year, today.month, today.day)},
            )
        except Exception:
            parsed = None
        if parsed is not None:
            d = parsed.date()
            return d, 'interpreted the date as %s' % d.strftime('%d %b %Y'), week_phrase_text, None

    return None, None, week_phrase_text, None


def _safe_date(year, month, day):
    try:
        return datetime.date(year, month, day)
    except (ValueError, TypeError):
        return None


# --- title -----------------------------------------------------------------

def _find_week_phrase_text(text: str):
    """The literal 'week N' / 'term N week M' phrase in text, or None."""
    m = re.search(r'(?:term\s*\d\s*)?week\s*\d+', text, re.IGNORECASE)
    return m.group(0) if m else None


def _extract_title(raw: str, matched_spans: list) -> str:
    """Residual title after removing date/weight/week spans, filler and orphans.

    Subject and type words are intentionally kept — for a fully-structured input
    like 'Methods SAC2 ...' they form the natural title 'Methods SAC2'.
    """
    residual = raw
    for span in matched_spans:
        if span:
            residual = re.sub(re.escape(span), ' ', residual, flags=re.IGNORECASE)
    kept = []
    for w in re.split(r'\s+', residual):
        if not w:
            continue
        cw = w.lower().strip('.,;:!?%')
        if not cw or cw in _TITLE_FILLER:
            continue
        if re.fullmatch(r'\d+', cw):   # drop orphaned numbers (e.g. leftover 'week 5')
            continue
        kept.append(w)
    title = ' '.join(kept).strip(' .,;:-')
    if not title:
        # Fall back to the raw input trimmed of trailing punctuation.
        title = raw.strip(' .,;:-')
    # Truncated to the same limit assessments.create_assessment enforces on the
    # title column, so a parsed record can never be rejected for a length the
    # parser itself produced.
    return title[:MAX_TITLE_LENGTH]


# --- scoring ---------------------------------------------------------------

def _score(detected: set) -> str:
    """HIGH/MEDIUM/LOW from the count of genuinely-detected scored fields."""
    hits = len(detected & set(_SCORED_FIELDS))
    if hits >= 4:
        return 'HIGH'
    if hits >= 2:
        return 'MEDIUM'
    return 'LOW'


# --- orchestration ---------------------------------------------------------

def _parse_one(line: str, today: datetime.date, settings_row) -> dict:
    """Parse a single line into the parse_text result dict. Never raises."""
    line = (line or '').strip()

    user_subjects = _stored_subjects(settings_row)
    subject, subject_src = _match_subject(line, user_subjects)
    type_value, type_src = _match_type(line)
    weight, weight_src = _extract_weight(line)
    due_date, date_why, term_info, date_src = _extract_date(line, today, settings_row)

    # Strip date/weight/week phrases from the title; keep subject/type words.
    title = _extract_title(line, [weight_src, date_src, _find_week_phrase_text(line)])

    why = {}
    detected = set()
    if subject is not None:
        why['subject'] = 'matched "%s" → %s' % (subject_src, subject)
        detected.add('subject')
    if type_src is not None:
        why['type'] = 'matched "%s" → %s' % (type_src, type_value)
        detected.add('type')
    if due_date is not None:
        why['due_date'] = date_why
        detected.add('due_date')
    if weight is not None:
        why['weight'] = 'matched "%s" → %s%%' % (weight_src, ('%g' % weight))
        detected.add('weight')

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
    """Parse one assessment sentence into a preview dict (never writes to the DB).

    Raises ValueError when the box is empty or the sentence is over
    MAX_PARSER_INPUT_LENGTH: a blank parse used to return a LOW-confidence record
    with an empty title, which tells the student nothing about what went wrong.
    """
    user = _require_user()
    # Validated before any work is done, so a bad input costs one message rather
    # than a settings read and a full regex chain over an unbounded string.
    raw_text = require_text(s, 'Assessment text', MAX_PARSER_INPUT_LENGTH)
    settings_row = _get_or_create_settings(user)
    today = _user_today(settings_row)
    return _parse_one(raw_text, today, settings_row)


@anvil.server.callable
def parse_bulk(s: str) -> list:
    """Parse one assessment per non-blank line; each result carries 'line_index'.

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
    if len(lines) > MAX_BULK_LINES:
        raise ValueError(
            'That is %d lines — paste at most %d at a time.'
            % (len(lines), MAX_BULK_LINES))

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
        result['line_index'] = i
        results.append(result)
    return results
