import anvil.secrets
import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""Note CRUD, search, settings get/update server module.

Settings surface (§10 step 1): get_settings, update_settings
(+ _get_or_create_settings, _settings_row_to_dict).

Subject onboarding surface (§11): get_subject_catalog, set_subjects.
user_settings.subjects holds the student's locked-in VCE studies; it is
deliberately NOT in the update_settings whitelist — set_subjects is the only
writer, so the VCE program rules (>=1 maths, English group always present)
cannot be bypassed.

Note CRUD + search (§10 step 6): create_note, update_note, delete_note,
toggle_pin, search_notes (+ _note_row_to_dict, _validate_note_fields).
delete_note also unlinks the note from any of the user's assessments.

Custom authentication (§5 workaround): create_account, sign_in_with_email.
Anvil's client-initiated signup/login forms cannot reach the users table from
server code, so both operations run here instead — see the comment above
create_account. They live in this module because they are the only other
writers of a user_settings row (via _get_or_create_settings).

See IMPLEMENTATION_SPEC.md section 2 (server_code/notes.py) and section 1
(user_settings table + uniqueness mandate).
"""

import anvil.server
import anvil.users
import datetime

from ._auth import _require_user, _own_or_raise
from ._constants import (
    EDITABLE_FIELDS_NOTE, SUBJECT_GROUPS, CANONICAL_SUBJECTS,
    ENGLISH_GROUP, MATHS_GROUP, LEGACY_SUBJECT_RENAMES,
    MAX_TITLE_LENGTH, MAX_NOTE_CONTENT_LENGTH, MAX_TAG_LENGTH,
    MAX_TAGS_PER_NOTE, MAX_SUBJECTS_PER_STUDENT,
    MIN_REMINDER_DAY, MAX_REMINDER_DAY, MAX_REMINDER_DAYS_PER_ASSESSMENT,
    MIN_TERM_NUMBER, MAX_TERM_NUMBER,
)
from ._validation import (
    require_bool, require_choice, require_email, require_int_in_range,
    require_iso_date_text, require_list, require_not_after, require_text,
    require_timezone,
    safe_bool, safe_choice, safe_date, safe_list, safe_number, safe_text,
    safe_timezone, is_positive_int, is_valid_reminder_day,
)

# Defaults for a freshly created user_settings row (spec §1). 'subjects' is
# deliberately ABSENT: naming the column in add_row would raise
# NoSuchColumnError on a database whose 'subjects' migration hasn't been
# applied yet (auto_create_missing_columns is off), which would break signup
# itself. An unset column reads back as None, which _row_value already treats
# as "not onboarded" — so the OnboardingForm gate works either way.
_SETTINGS_DEFAULTS = {
    'theme': 'light',
    'default_reminder_days': [7, 2],
    'notifications_enabled': True,
    'school_year': None,
    'school_terms': [],
    'timezone': 'Australia/Melbourne',  # Pending Decision 2 (A)
}

# Whitelist of client-updatable settings keys (spec §2 update_settings).
_SETTINGS_FIELDS = (
    'theme', 'default_reminder_days', 'notifications_enabled',
    'school_year', 'school_terms', 'timezone',
)

# The themes the app actually offers. This is the same pair the Settings
# dropdown is built from (SettingsForm._build_display_card) and the same pair
# common.apply_theme() knows how to draw, so validating against it here is what
# stops a stored theme the client cannot render. Kept local rather than in
# _constants because the theme is a client-presentation concern, not a stored
# enum shared across server modules the way VALID_TYPES is.
_VALID_THEMES = frozenset(('light', 'dark'))
_DEFAULT_THEME = 'light'

# Plausible window for user_settings.school_year. Wide on purpose: the point is
# to catch a slipped digit (20226, or 226) rather than to police which year a
# student may track.
_MIN_SCHOOL_YEAR = 2000
_MAX_SCHOOL_YEAR = 2100

# Shortest password create_account will accept. Anvil's signup_with_email
# imposes no length rule of its own, so without this the app would happily
# create an account behind a one-character password.
_MIN_PASSWORD_LENGTH = 8


# --- settings helpers ------------------------------------------------------

def _get_or_create_settings(user) -> "tables.Row":
    """Return the user's settings row, creating it with defaults if absent.

    This is the SOLE inserter into `user_settings` (spec §1 uniqueness mandate):
    no other code path may call `app_tables.user_settings.add_row(...)`. Anvil
    server modules have no read-then-write transactional isolation, so two
    concurrent first-logins for the same user could theoretically both insert;
    the risk is negligible for this single-user app and is accepted in §1.
    """
    row = app_tables.user_settings.get(user=user)
    if row is not None:
        return row
    return app_tables.user_settings.add_row(user=user, **_SETTINGS_DEFAULTS)


def _row_value(row, key, default=None):
    """row[key], tolerating a column that doesn't exist yet (pre-migration DB)."""
    try:
        value = row[key]
    except Exception:
        return default
    return default if value is None else value


# --- read guards for the two simpleObject settings columns -----------------
# Both columns below hold free-form JSON, so the Data Tables console (and an
# import whose settings patch failed part-way) can leave literally anything in
# them. safe_list drops unusable ELEMENTS, but neither column can be described
# by a one-line predicate alone, so each gets a small helper here.

def _normalise_term_keys(term):
    """Accept both spellings of a term's date keys; return the app's spelling.

    This module, nlp._try_parse_week_phrase and the Settings screen all read
    `start_date` / `end_date`, but SAT 5 §4.2.3 documents the same two fields as
    `start` / `end`. Rather than pick a winner and silently reject the other
    spelling, BOTH are accepted on the way in and normalised to
    `start_date` / `end_date` on the way out — so a hand-authored or
    document-conformant terms list imports instead of failing a format check
    whose cause the student cannot see. Anything that is not a dict is handed
    straight back for the caller to reject or drop.
    """
    if not isinstance(term, dict):
        return term
    out = dict(term)
    # pop, not read: the alias must not survive alongside the canonical key, or a
    # reader could pick up the stale spelling of a date the student has since fixed.
    start_alias = out.pop('start', None)
    end_alias = out.pop('end', None)
    if out.get('start_date') is None and start_alias is not None:
        out['start_date'] = start_alias
    if out.get('end_date') is None and end_alias is not None:
        out['end_date'] = end_alias
    return out


def _is_usable_term(term) -> bool:
    """Element predicate for safe_list: a school term the app can actually use.

    "Usable" is defined by the one consumer that matters — nlp resolves
    "Term X Week Y" by counting weeks forward from `start_date` and then testing
    `start <= due <= end`. A term missing either date, numbered outside 1-4, or
    stored back-to-front therefore contributes nothing but a silent failure, so
    it is dropped on read rather than published to the client.
    """
    if not isinstance(term, dict):
        return False
    number = term.get('term')
    if not isinstance(number, int) or isinstance(number, bool):
        return False
    if not (MIN_TERM_NUMBER <= number <= MAX_TERM_NUMBER):
        return False
    start = safe_date(term.get('start_date'))
    end = safe_date(term.get('end_date'))
    if start is None or end is None:
        return False
    return start <= end


def _safe_school_terms(stored) -> list:
    """Read guard for user_settings.school_terms; never raises.

    SAT 5 §6 names this column as the one most at risk of console corruption,
    because it is the only stored value a student is ever told to hand-edit.
    """
    normalised = [_normalise_term_keys(t) for t in safe_list(stored)]
    # Project each surviving term down to exactly the three keys every reader
    # expects, so a console edit cannot smuggle extra fields out to the client.
    return [{'term': t['term'],
             'start_date': t['start_date'],
             'end_date': t['end_date']}
            for t in safe_list(normalised, _is_usable_term)]


def _safe_subjects(stored) -> list:
    """Read guard for user_settings.subjects: canonical catalog names only.

    Renamed studies are coerced to their current VCAA name BEFORE the catalog
    filter runs, so a row written before a rename keeps the subject instead of
    having it quietly dropped (and then rejected as unknown the next time the
    student opens the subject picker).
    """
    renamed = []
    for subject in safe_list(stored):
        if isinstance(subject, str):
            subject = LEGACY_SUBJECT_RENAMES.get(subject, subject)
        # The rename table can map two stored names onto one canonical name, so
        # de-duplicate here rather than showing the student the same study twice.
        if subject not in renamed:
            renamed.append(subject)
    return safe_list(renamed, lambda s: s in CANONICAL_SUBJECTS)


def _settings_row_to_dict(row) -> dict:
    """Plain-dict view of a settings row (no live Row object leaves the server).

    Every value is routed through a `safe_*` read guard — the "validate inputs
    from the DATABASE" half of SAT criterion 7.3. Nothing in here raises, and that
    is deliberate: this function is on the path of every screen in the app,
    including the Settings screen that is the only place a bad value can be
    corrected, so a damaged cell must degrade to a documented default rather than
    lock the student out of the one page that could fix it.

    `_row_value` wraps each read because a column added by a later migration does
    not merely read back as None on an older database — `row[key]` raises
    NoSuchColumnError. That protection previously covered `subjects` alone; all
    seven columns need it, since any of them can be the one a deploy is ahead of.
    """
    # safe_number returns a float, but the Settings screen renders this value with
    # str() into a text box, and "2026.0" is not a school year — so it comes back
    # as an int once the range check has passed.
    stored_year = safe_number(
        _row_value(row, 'school_year'), default=None,
        minimum=_MIN_SCHOOL_YEAR, maximum=_MAX_SCHOOL_YEAR)
    return {
        'theme': safe_choice(
            _row_value(row, 'theme'), _VALID_THEMES, _DEFAULT_THEME),
        # is_valid_reminder_day is the SAME predicate the write path enforces and the
        # SAME one reminders.run_reminder_check applies
        # to this column. Using anything else here would let the Settings screen
        # show a reminder day the dispatcher silently ignores.
        'default_reminder_days': safe_list(
            _row_value(row, 'default_reminder_days'), is_valid_reminder_day),
        # default=False deliberately matches reminders.run_reminder_check as well:
        # when the two readers disagreed, the switch could read "off" on screen
        # while the dispatcher kept emailing.
        'notifications_enabled': safe_bool(
            _row_value(row, 'notifications_enabled'), default=False),
        'school_year': int(stored_year) if stored_year is not None else None,
        'school_terms': _safe_school_terms(_row_value(row, 'school_terms')),
        'timezone': safe_timezone(_row_value(row, 'timezone')),
        'subjects': _safe_subjects(_row_value(row, 'subjects')),
    }


# --- settings callables ----------------------------------------------------

@anvil.server.callable
def get_settings() -> dict:
    """Return the current user's settings, lazily creating the row on first call."""
    user = _require_user()
    row = _get_or_create_settings(user)
    return _settings_row_to_dict(row)


@anvil.server.callable
def update_settings(fields: dict) -> dict:
    """Whitelist-filter, validate, and persist a settings patch."""
    user = _require_user()
    row = _get_or_create_settings(user)
    patch = {k: v for k, v in (fields or {}).items() if k in _SETTINGS_FIELDS}
    # Validate the WHOLE patch before writing any of it: _validate_settings returns
    # the values to persist rather than mutating in place, so a save that fails on
    # its third field leaves the first two unwritten instead of half-applying.
    clean = _validate_settings(patch)
    if clean:
        row.update(**clean)
    return _settings_row_to_dict(row)


# --- subject onboarding (spec §11) ------------------------------------------

@anvil.server.callable
def get_subject_catalog() -> list:
    """The picker catalog: [{'group': <area>, 'subjects': [<canonical>, ...]}, ...]."""
    _require_user()
    return [{'group': g, 'subjects': list(subs)} for g, subs in SUBJECT_GROUPS]


def _clean_subjects(subjects) -> list:
    """Validate a subject selection against the catalog and VCE rules.

    Rules (spec §11): every entry must be a catalog subject; at least one
    mathematics study is required (DotPoint client mandate); if no
    English-group study was chosen, 'English' is appended automatically —
    every VCE program includes an English-group study (VCAA), so the app
    never lets a student track a program without one.
    """
    require_list(subjects, 'Subjects')

    catalog = set(CANONICAL_SUBJECTS)
    seen, clean = set(), []
    for s in subjects:
        # MAX_TITLE_LENGTH is only a sanity bound on the text; membership of the
        # catalog on the next line is what actually constrains a subject name.
        name = require_text(s, 'Subject', MAX_TITLE_LENGTH)
        # Rows and exports written before a VCAA study rename carry the old name;
        # coerce so a legacy selection is kept rather than rejected as unknown.
        name = LEGACY_SUBJECT_RENAMES.get(name, name)
        if name not in catalog:
            raise ValueError(
                '"%s" is not a subject DotPoint offers. '
                'Choose your studies from the list.' % name)
        if name not in seen:
            seen.add(name)
            clean.append(name)

    if not any(s in MATHS_GROUP for s in clean):
        raise ValueError(
            "Select at least one mathematics study (Foundation, General, "
            "Methods or Specialist).")

    if not any(s in ENGLISH_GROUP for s in clean):
        # Reserve a slot for the auto-added English BEFORE appending, so the
        # error names what actually happened rather than blaming the user for
        # a 13th subject they never picked.
        if len(clean) >= MAX_SUBJECTS_PER_STUDENT:
            raise ValueError(
                "English is added automatically (every VCE program includes "
                "an English study) — pick at most %d other subjects."
                % (MAX_SUBJECTS_PER_STUDENT - 1))
        clean.append('English')

    if len(clean) > MAX_SUBJECTS_PER_STUDENT:
        raise ValueError(
            "That is more than %d subjects — choose the studies you are "
            "actually enrolled in." % MAX_SUBJECTS_PER_STUDENT)

    return clean


@anvil.server.callable
def set_subjects(subjects: list) -> dict:
    """Validate and lock in the user's VCE subjects; the sole writer of
    user_settings.subjects. Used by onboarding and the Settings change flow."""
    user = _require_user()
    row = _get_or_create_settings(user)
    clean = _clean_subjects(subjects)
    try:
        row.update(subjects=clean)
    except Exception:
        # The only expected failure here is the 'subjects' column not existing
        # yet (Data Tables migration not applied after deploy).
        raise ValueError(
            "The database schema hasn't been migrated yet — apply the "
            "'subjects' column migration in the Anvil Data Tables view.")
    return _settings_row_to_dict(row)


# --- validation ------------------------------------------------------------

def _validate_settings(fields: dict) -> dict:
    """Validate a whitelisted settings patch; return the values to persist.

    Only keys PRESENT in `fields` appear in the result — this is a patch, not a
    whole record, and an absent key must leave the stored value alone.
    """
    clean = dict(fields)

    if 'theme' in clean:
        clean['theme'] = require_choice(clean['theme'], _VALID_THEMES, 'Theme')

    if 'default_reminder_days' in clean:
        days = require_list(clean['default_reminder_days'], 'Reminder days')
        # RANGE. The upper bound is the point: this list is read straight back by
        # reminders.run_reminder_check, so with no maximum a value like 999999 made
        # every assessment permanently "due soon" and emailed the student about all
        # of them on the first scheduler tick.
        clean['default_reminder_days'] = [
            require_int_in_range(
                d, 'Each reminder day', MIN_REMINDER_DAY, MAX_REMINDER_DAY)
            for d in days]
        if len(clean['default_reminder_days']) > MAX_REMINDER_DAYS_PER_ASSESSMENT:
            raise ValueError(
                'Choose at most %d reminder days (you chose %d).'
                % (MAX_REMINDER_DAYS_PER_ASSESSMENT,
                   len(clean['default_reminder_days'])))

    if 'notifications_enabled' in clean:
        clean['notifications_enabled'] = require_bool(
            clean['notifications_enabled'], 'Email reminders')

    if 'school_year' in clean and clean['school_year'] is not None:
        # None is a legitimate value: it is how the Settings screen clears the box.
        clean['school_year'] = require_int_in_range(
            clean['school_year'], 'School year',
            _MIN_SCHOOL_YEAR, _MAX_SCHOOL_YEAR)

    if 'school_terms' in clean:
        clean['school_terms'] = _validate_school_terms(clean['school_terms'])

    if 'timezone' in clean:
        # Routed through the shared helper so the message a student sees for a bad
        # timezone is identical here, in the importer and on any future screen.
        clean['timezone'] = require_timezone(clean['timezone'], 'Timezone')

    return clean


def _validate_school_terms(terms) -> list:
    """Validate the school-terms list; return it normalised, or raise."""
    require_list(terms, 'School terms')

    clean = []
    for index, raw in enumerate(terms):
        if not isinstance(raw, dict):
            raise ValueError(
                'Each school term needs a term number and two dates — '
                'entry %d does not.' % (index + 1))
        term = _normalise_term_keys(raw)
        number = require_int_in_range(
            term.get('term'), 'Term number', MIN_TERM_NUMBER, MAX_TERM_NUMBER)
        clean.append({
            'term': number,
            'start_date': require_iso_date_text(
                term.get('start_date'), 'Term %d start date' % number),
            'end_date': require_iso_date_text(
                term.get('end_date'), 'Term %d end date' % number),
        })

    # --- REASONABLENESS: the checks that need the WHOLE value ---------------
    # Every field above is individually valid by this point. The three checks
    # below are the ones field-by-field validation cannot reach, and they were
    # missing entirely: a term whose dates run backwards, two terms claiming the
    # same weeks, and two entries both calling themselves Term 2.
    #
    # WHY THIS MATTERS MORE THAN IT LOOKS. nlp._try_parse_week_phrase resolves
    # "Term 2 Week 5" by counting weeks forward from start_date and then testing
    # start <= due <= end. A term stored back-to-front passes every type and
    # format check above and then makes EVERY "Term X Week Y" phrase for that
    # term unresolvable — with no error raised anywhere, no message to the
    # student, and FR15 simply not working. Catching it on the way in is the
    # only place it can be reported to someone who can fix it.
    for term in clean:
        require_not_after(
            datetime.date.fromisoformat(term['start_date']),
            datetime.date.fromisoformat(term['end_date']),
            'Term %d start date' % term['term'],
            'Term %d end date' % term['term'])

    seen_numbers = set()
    for term in clean:
        if term['term'] in seen_numbers:
            raise ValueError(
                'Term %d is listed twice. Each term can only have one set of '
                'dates.' % term['term'])
        seen_numbers.add(term['term'])

    # Overlap. 'YYYY-MM-DD' strings sort chronologically, and require_iso_date_text
    # above guarantees every value is in that exact shape, so sorting the strings
    # is the same as sorting the dates.
    ordered = sorted(clean, key=lambda t: t['start_date'])
    for earlier, later in zip(ordered, ordered[1:]):
        if later['start_date'] <= earlier['end_date']:
            raise ValueError(
                'Term %d and Term %d overlap. School terms cannot share dates — '
                'check their start and end dates.'
                % (earlier['term'], later['term']))

    return clean


# --- note CRUD + search (spec section 2, step 6) ---------------------------

def _is_tag_text(value) -> bool:
    """Element predicate for safe_list: a tag the app can search and display.

    search_notes lowercases every tag it compares, so a stored number or dict —
    which a simpleObject column accepts without complaint — used to raise
    AttributeError there and take the whole Notes screen down rather than
    hiding one unusable tag.
    """
    return isinstance(value, str) and value.strip() != ''


def _note_row_to_dict(row) -> dict:
    """Plain-dict view of a note row; timestamps as ISO strings, incl. 'id'.

    Read guards throughout, for the same reason as _settings_row_to_dict: one
    corrupt cell must cost the student one field, not the whole Notes screen.
    """
    def iso(stored):
        # A guard, not a formatter: .isoformat() on a cell that holds a string
        # raises AttributeError, and this runs once per note in the list.
        if isinstance(stored, (datetime.datetime, datetime.date)):
            return stored.isoformat()
        return None
    return {
        'id': row.get_id(),
        'title': safe_text(row['title']),
        'content': safe_text(row['content']),
        'tags': safe_list(row['tags'], _is_tag_text),
        'is_pinned': safe_bool(row['is_pinned'], default=False),
        'created_at': iso(row['created_at']),
        'updated_at': iso(row['updated_at']),
    }


def _validate_note_fields(fields: dict) -> dict:
    """Validate a note create/update patch; return a cleaned copy or raise.

    A patch, so each field is checked only when present — update_note sends just
    the fields the student edited, while create_note and the importer send all four.
    """
    out = dict(fields)

    if 'title' in out:
        out['title'] = require_text(out['title'], 'Title', MAX_TITLE_LENGTH)

    if 'content' in out:
        # allow_blank: a note that is all title and no body is a legitimate note.
        out['content'] = require_text(
            out['content'], 'Content', MAX_NOTE_CONTENT_LENGTH, allow_blank=True)

    if 'tags' in out:
        tags = out['tags']
        if tags is None:
            tags = []  # clearing every tag arrives as None from the editor
        require_list(tags, 'Tags')
        # De-duplicate case-insensitively, preserving order, dropping blanks.
        seen, deduped = set(), []
        for t in tags:
            key = require_text(t, 'Tag', MAX_TAG_LENGTH, allow_blank=True)
            if key and key.lower() not in seen:
                seen.add(key.lower())
                deduped.append(key)
        # Counted AFTER de-duplication, so typing the same tag twice is not held
        # against the student's allowance.
        if len(deduped) > MAX_TAGS_PER_NOTE:
            raise ValueError(
                'A note can have at most %d tags (this one has %d).'
                % (MAX_TAGS_PER_NOTE, len(deduped)))
        out['tags'] = deduped

    if 'is_pinned' in out:
        out['is_pinned'] = require_bool(out['is_pinned'], 'Pinned')

    return out


@anvil.server.callable
def create_note(record: dict) -> str:
    """Create a note owned by the current user; return its row id (FR10)."""
    user = _require_user()
    record = record or {}
    clean = _validate_note_fields({
        'title': record.get('title'),
        'content': record.get('content') or '',
        'tags': record.get('tags') or [],
        'is_pinned': bool(record.get('is_pinned', False)),
    })
    now = datetime.datetime.now(datetime.timezone.utc)
    row = app_tables.notes.add_row(
        title=clean['title'], content=clean['content'], tags=clean['tags'],
        is_pinned=clean['is_pinned'], user=user, created_at=now, updated_at=now)
    return row.get_id()


@anvil.server.callable
def update_note(row_id: str, fields: dict) -> dict:
    """Whitelist-filter, validate and apply an edit to an owned note (FR10)."""
    user = _require_user()
    row = app_tables.notes.get_by_id(row_id)
    if row is None:
        raise ValueError(
            "That note no longer exists — it may have already been deleted.")
    _own_or_raise(row, user)
    clean = _validate_note_fields(
        {k: v for k, v in (fields or {}).items() if k in EDITABLE_FIELDS_NOTE})
    if clean:
        clean['updated_at'] = datetime.datetime.now(datetime.timezone.utc)
        row.update(**clean)
    return _note_row_to_dict(row)


@anvil.server.callable
def delete_note(row_id: str) -> bool:
    """Delete an owned note, first unlinking it from any of the user's assessments.

    Returns True on success; a missing note RAISES rather than returning False, so
    that a delete and a pin of a note someone else already removed fail the same
    way (toggle_pin raised while this returned a quiet False for the same cause).
    """
    user = _require_user()
    row = app_tables.notes.get_by_id(row_id)
    if row is None:
        raise ValueError(
            "That note no longer exists — it may have already been deleted.")
    _own_or_raise(row, user)
    with tables.Transaction():
        for a in app_tables.assessments.search(user=user):
            linked = safe_list(a['linked_note_ids'])
            if row_id in linked:
                a.update(linked_note_ids=[n for n in linked if n != row_id])
        row.delete()
    return True


@anvil.server.callable
def toggle_pin(row_id: str) -> bool:
    """Flip a note's pinned state; return the new value (FR10)."""
    user = _require_user()
    row = app_tables.notes.get_by_id(row_id)
    if row is None:
        raise ValueError(
            "That note no longer exists — it may have already been deleted.")
    _own_or_raise(row, user)
    # safe_bool, not `not row['is_pinned']`: a cell holding None or a string would
    # otherwise flip to True from a state the Notes list was already drawing as
    # unpinned, so the first click would appear to do nothing.
    new_value = not safe_bool(row['is_pinned'], default=False)
    row.update(is_pinned=new_value,
               updated_at=datetime.datetime.now(datetime.timezone.utc))
    return new_value


@anvil.server.callable
def search_notes(query: str = None, tag: str = None, pinned_only: bool = False) -> list:
    """Return the user's notes (pinned-first, then recent) filtered by query/tag (FR11)."""
    user = _require_user()
    # `query` and `tag` arrive from the search box, so they get the require_* family:
    # allow_blank because an empty box means "no filter", not an error.
    needle = require_text(
        query, 'Search', MAX_TITLE_LENGTH, allow_blank=True).lower()
    want = require_text(tag, 'Tag', MAX_TAG_LENGTH, allow_blank=True).lower()
    pinned_only = bool(pinned_only)

    rows = list(app_tables.notes.search(user=user))

    def _sort_key(r):
        # This key runs for every note, so it uses the safe_* family: a timestamp
        # cell that is not a datetime would otherwise raise AttributeError here and
        # take the whole Notes screen down instead of mis-sorting one row.
        updated = r['updated_at']
        ts = updated.timestamp() if isinstance(updated, datetime.datetime) else 0
        return (0 if safe_bool(r['is_pinned'], default=False) else 1, -ts)
    rows.sort(key=_sort_key)

    if needle:
        rows = [r for r in rows
                if needle in (safe_text(r['title']) + ' '
                              + safe_text(r['content'])).lower()]
    if want:
        rows = [r for r in rows
                if any(want == t.lower()
                       for t in safe_list(r['tags'], _is_tag_text))]
    if pinned_only:
        rows = [r for r in rows if safe_bool(r['is_pinned'], default=False)]

    return [_note_row_to_dict(r) for r in rows]


# --- custom auth (workaround) ----------------------------------------------
# Anvil's client-initiated signup_with_form / login_with_form raise
# "PermissionDenied: Cannot access this table from server code" on the users
# table (a Users-service<->table binding issue). Running the same operations
# from a trusted server-module callable uses this module's full users-table
# access, which sidesteps that path. Returns True on success; raises ValueError
# with a user-facing message otherwise.

@anvil.server.callable
def create_account(email: str, password: str) -> bool:
    # FORMAT. In DotPoint the account IS the email address: there is no username
    # and no password-reset that does not go through it, so a typo like
    # "sam@gmail.con" creates an account the student can never sign into and never
    # be told about. This is the only place in the app that check can be made.
    email = require_email(email, 'Email address').lower()
    # RANGE. Anvil's signup_with_email imposes no length rule of its own, so
    # without this a one-character password is accepted silently.
    if not isinstance(password, str) or len(password) < _MIN_PASSWORD_LENGTH:
        raise ValueError(
            "Your password needs to be at least %d characters long."
            % _MIN_PASSWORD_LENGTH)
    try:
        new_user = anvil.users.signup_with_email(email, password, remember=True)
    except anvil.users.UserExists:
        raise ValueError("An account with that email already exists — try signing in.")
    anvil.users.force_login(new_user)
    _get_or_create_settings(new_user)
    return True


@anvil.server.callable
def sign_in_with_email(email: str, password: str) -> bool:
    # Existence only, deliberately. Applying the format and length rules from
    # create_account here would lock out any account created before those rules
    # existed, and an address that fails the pattern still deserves the honest
    # "incorrect email or password" answer rather than a different one that would
    # confirm to a stranger which addresses are registered.
    email = (email or '').strip().lower()
    if not email or not password:
        raise ValueError("Enter both your email address and your password.")
    try:
        user = anvil.users.login_with_email(email, password, remember=True)
    except anvil.users.AuthenticationFailed:
        raise ValueError("Incorrect email or password.")
    _get_or_create_settings(user)
    return True
