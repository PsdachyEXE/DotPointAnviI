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
import re
import datetime

from ._auth import _require_user
from ._datetime import _user_today
from ._constants import SUBJECT_ALIASES, TYPE_KEYWORDS
from .notes import _get_or_create_settings

# dateparser is an optional third-party fallback (spec section 7) for free-form
# English dates the regex chain misses. Guarded so the module works without it.
try:
    import dateparser as _dateparser
except ImportError:
    _dateparser = None

_SCORED_FIELDS = ('subject', 'type', 'due_date', 'weight')

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


# --- tokenisation ----------------------------------------------------------

def _tokenise(s: str) -> list:
    """Split on whitespace, preserving "quoted substrings" as single tokens."""
    tokens = []
    for chunk in re.findall(r'"[^"]*"|\S+', s):
        if chunk.startswith('"') and chunk.endswith('"') and len(chunk) >= 2:
            tokens.append(chunk[1:-1])
        else:
            tokens.append(chunk)
    return tokens


# --- field matchers --------------------------------------------------------

def _match_subject(text: str, tokens: list):
    """Return (canonical_subject, matched_alias) or (None, None).

    Multi-word aliases ('math methods', 'phys ed') are tried first, longest
    first, so 'methods' does not pre-empt 'math methods'. Then single tokens.
    """
    low = text.lower()
    multi = sorted(
        (a for a in SUBJECT_ALIASES if ' ' in a), key=len, reverse=True
    )
    for alias in multi:
        if re.search(r'\b' + re.escape(alias) + r'\b', low):
            return SUBJECT_ALIASES[alias], alias
    for tok in tokens:
        key = tok.lower().strip('.,;:!?')
        if key in SUBJECT_ALIASES:
            return SUBJECT_ALIASES[key], key
    return None, None


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
        return float(m.group(1)), m.group(0)
    except (ValueError, TypeError):
        return None, None


# --- date extraction -------------------------------------------------------

def _try_parse_week_phrase(text: str, settings_row):
    """Resolve 'Term X Week Y' (with optional weekday) to a date (FR15).

    Returns (date, matched_text) or (None, None) when the phrase is absent, when
    school_terms is empty/unconfigured, or when the named term is missing. The
    empty-config case is what forces LOW confidence per FR15.
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

    terms = settings_row['school_terms'] if settings_row is not None else None
    if not terms:
        return None, None

    term_n = int(m.group(1))
    weekday_token = (m.group(2) or '').lower()
    week_n = int(m.group(3))

    term = next((t for t in terms if isinstance(t, dict) and t.get('term') == term_n), None)
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
    # Detect the phrase even when unresolved, so we don't mis-read it as a title.
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

    # 4. 'in N days'.
    m = re.search(r'\bin\s+(\d+)\s+days?\b', low)
    if m:
        d = today + datetime.timedelta(days=int(m.group(1)))
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

def _week_phrase_span(text: str):
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
    return title[:200]


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
    tokens = _tokenise(line)

    subject, subject_src = _match_subject(line, tokens)
    type_value, type_src = _match_type(line)
    weight, weight_src = _extract_weight(line)
    due_date, date_why, term_info, date_src = _extract_date(line, today, settings_row)

    # Strip date/weight/week phrases from the title; keep subject/type words.
    title = _extract_title(line, [weight_src, date_src, _week_phrase_span(line)])

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
    """Parse one assessment sentence into a preview dict (never writes to the DB)."""
    user = _require_user()
    settings_row = _get_or_create_settings(user)
    today = _user_today(settings_row)
    return _parse_one(s or '', today, settings_row)


@anvil.server.callable
def parse_bulk(s: str) -> list:
    """Parse one assessment per non-blank line; each result carries 'line_index'."""
    user = _require_user()
    settings_row = _get_or_create_settings(user)
    today = _user_today(settings_row)
    results = []
    for i, line in enumerate((s or '').splitlines()):
        if line.strip():
            result = _parse_one(line, today, settings_row)
            result['line_index'] = i
            results.append(result)
    return results
