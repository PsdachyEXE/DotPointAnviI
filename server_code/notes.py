import anvil.secrets
import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""Four unrelated server surfaces in one module: settings, subject
onboarding, note CRUD/search, and custom email authentication.

The four have almost nothing to do with each other, so each gets a banner of
its own below — read the banners first if you are hunting for something. They
share this one file because all four need `_get_or_create_settings`, and
spec §1's uniqueness mandate says exactly ONE function in the whole app is
allowed to insert a user_settings row.

SURFACE 1 — Settings (§10 step 1): get_settings, update_settings
(+ _get_or_create_settings, _row_value, the _safe_* read guards,
_settings_row_to_dict, _validate_settings, _validate_school_terms).
What is stored here drives other modules: school_terms is what nlp's
"Term X Week Y" resolver counts from (FR15); notifications_enabled and
default_reminder_days are what the dispatcher obeys (FR13, FR14); timezone
is what _datetime resolves "today" against; theme is §12.

SURFACE 2 — Subject onboarding (§11): get_subject_catalog, set_subjects
(+ _clean_subjects). user_settings.subjects holds the student's locked-in VCE
studies; it is deliberately NOT in the update_settings whitelist —
set_subjects is the only writer, so the VCE program rules (>=1 maths, English
group always present) cannot be bypassed.

SURFACE 3 — Note CRUD + search (§10 step 6): create_note, update_note,
delete_note, toggle_pin, search_notes (+ _note_row_to_dict,
_validate_note_fields). FR10 covers create/edit/delete/pin and FR11 covers
search plus tag filter. delete_note also unlinks the note from any of the
user's assessments, which is the write half of FR12's linked_note_ids.

SURFACE 4 — Custom authentication (§5 workaround, FR20): create_account,
sign_in_with_email. Anvil's client-initiated signup/login forms cannot reach
the users table from server code, so both operations run here instead — see
the banner above create_account. Neither function ever sees or stores a
password of its own: Anvil's Users service does the hashing and the checking.
They also live here because they are the only other writers of a
user_settings row (via _get_or_create_settings).

Every @anvil.server.callable below calls _require_user() first, and every one
that fetches a row by id calls _own_or_raise() straight after, so no row ever
reaches a user who does not own it (NFR03). The two exceptions are surface 4's
create_account and sign_in_with_email, which cannot demand a signed-in user
because they are how somebody becomes one; _auth's own docstring names them as
the app's only two documented exemptions, and neither takes a row id nor reads
a user-owned table.

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
#
# KNOWN SPEC DIFFERENCE: 'theme' is 'light' here, where the §1 user_settings
# table gives the column a default of 'dark'. The code is at least consistent
# with itself — this value and _DEFAULT_THEME (the read guard's fallback) are
# the same 'light', so a new account and a damaged cell land on one theme — and
# §12's Settings switch changes it either way. Written down rather than left
# silent, since an undocumented split between spec and code is exactly what
# 7.3's "no inconsistencies" is looking for.
_SETTINGS_DEFAULTS = {
    'theme': 'light',
    'default_reminder_days': [7, 2],
    'notifications_enabled': True,
    'school_year': None,
    'school_terms': [],
    'timezone': 'Australia/Melbourne',  # Pending Decision 2 (A)
}

# Whitelist of client-updatable settings keys (spec §2 update_settings). A
# whitelist rather than a blacklist so a column added later is closed to the
# client by DEFAULT and has to be opened deliberately. 'subjects' is the
# standing example of a column kept out on purpose: set_subjects is its only
# writer, and routing it through here would skip the VCE program rules.
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


# ===========================================================================
# SURFACE 1 of 4 — SETTINGS   (spec §10 step 1; table: user_settings, §1)
# ===========================================================================
# Everything from here to the "SUBJECT ONBOARDING" banner belongs to the
# Settings screen. Two rules hold across the whole surface:
#   * the READ path never raises — a damaged cell degrades to a documented
#     default, because Settings is the only screen that can repair one;
#   * the WRITE path validates the entire patch before a single field is
#     written, so a save cannot half-apply.

# --- settings helpers ------------------------------------------------------

def _get_or_create_settings(user) -> "tables.Row":
    """Return the user's settings row, creating it with defaults if absent.

    `user` is always the Users-table Row that _require_user() (or, on signup,
    anvil.users.signup_with_email) handed back — never an id or an email
    string, because the `user` column is a link to that table. Returns the
    live user_settings Row; callers turn it into a plain dict with
    _settings_row_to_dict rather than letting a Row cross to the client.

    This is the SOLE inserter into `user_settings` (spec §1 uniqueness mandate):
    no other code path may call `app_tables.user_settings.add_row(...)`. Anvil
    server modules have no read-then-write transactional isolation, so two
    concurrent first-logins for the same user could theoretically both insert;
    the risk is negligible for this single-user app and is accepted in §1.
    """
    # Created lazily on first access rather than at signup, because a user row
    # can also appear from the Anvil Users console or predate the settings
    # table entirely — those accounts must still end up with defaults instead
    # of a missing row that every later read would have to special-case.
    row = app_tables.user_settings.get(user=user)
    if row is not None:
        return row
    # The concurrency window the docstring accepts sits exactly here: two
    # first-logins that both got None above would both insert, and Anvil's
    # .get() then refuses to choose between two matching rows and raises. One
    # student on one browser cannot realistically hit it, so it is documented
    # rather than locked.
    #
    # _SETTINGS_DEFAULTS is unpacked instead of being spelled out, so the
    # signup path, the sign-in path and get_settings cannot drift apart —
    # they all land on this one line.
    return app_tables.user_settings.add_row(user=user, **_SETTINGS_DEFAULTS)


def _row_value(row, key, default=None):
    """row[key], tolerating a column that doesn't exist yet (pre-migration DB).

    `row` is any Data Tables Row, `key` a column name, `default` whatever the
    caller wants when there is nothing usable to return. Returns the stored
    value otherwise. Never raises.

    It exists for one Anvil behaviour a reader would not guess: a row written
    BEFORE a column was added to the table does not read that column back as
    None — `row[key]` RAISES NoSuchColumnError. So a deploy that is one Data
    Tables migration ahead of the database turns every settings read into a
    crash unless each individual lookup is guarded. Guarding here, at the
    narrowest scope, is this module's answer to the brief's "guard the inputs
    coming from the database" requirement.
    """
    try:
        value = row[key]
    except Exception:
        # Bare on purpose. NoSuchColumnError is the case being defended
        # against, but the right answer for ANY failure to read a settings
        # cell is the documented default: this helper is on the path of every
        # screen in the app, so it must never be the thing that takes the app
        # down.
        return default
    # A column that exists but holds None is folded onto the same answer, so
    # callers deal with one "nothing usable here" case instead of two.
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
    # 1. Not a dict: hand it straight back untouched. Rejecting it here would
    #    force this helper to know whether its caller wants to raise
    #    (_validate_school_terms does) or to drop (_safe_school_terms does).
    if not isinstance(term, dict):
        return term
    # 2. Copy first. The caller's dict may be a value read straight out of a
    #    simpleObject column, and mutating that in place would edit the row
    #    object the rest of the request is still reading from.
    out = dict(term)
    # 3. pop, not read: the alias must not survive alongside the canonical key,
    #    or a reader could pick up the stale spelling of a date the student has
    #    since fixed.
    start_alias = out.pop('start', None)
    end_alias = out.pop('end', None)
    # 4. The canonical key WINS when both spellings are present. A dict
    #    carrying both came from a part-finished hand edit, and start_date is
    #    the one the Settings screen itself last wrote, so it is the one to
    #    trust.
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

    `term` is one element of the stored school_terms list — a dict already
    through _normalise_term_keys, so its date keys are start_date/end_date —
    but a simpleObject column will hold anything, so nothing about its shape
    can be assumed. Returns True/False only; it never raises, because
    safe_list calls it once per stored element on the read path.
    """
    # 1. A simpleObject list can hold bare strings, numbers or nested lists,
    #    none of which can be indexed by the checks below.
    if not isinstance(term, dict):
        return False
    # 2. The term number is what nlp matches "Term 2" against. bool is
    #    excluded explicitly because bool subclasses int in Python, so a
    #    stored True would otherwise pass as Term 1 and quietly answer for a
    #    term the student never entered.
    number = term.get('term')
    if not isinstance(number, int) or isinstance(number, bool):
        return False
    # 3. The Victorian school year has exactly four terms, so a number outside
    #    1-4 can never be reached by any phrase a student types.
    if not (MIN_TERM_NUMBER <= number <= MAX_TERM_NUMBER):
        return False
    # 4. safe_date, not require_date: this is a READ guard. An unreadable date
    #    has to drop the term quietly, not raise into whichever screen happened
    #    to ask for settings. safe_date accepts both a real date object and the
    #    'YYYY-MM-DD' text the write path stores.
    start = safe_date(term.get('start_date'))
    end = safe_date(term.get('end_date'))
    if start is None or end is None:
        return False
    # 5. Ordering is checked last because it is the only test that needs both
    #    dates at once. A back-to-front term passes every check above and then
    #    makes every "Term X Week Y" phrase for that term unresolvable, with
    #    nothing shown to the student anywhere — FR15 simply stops working.
    return start <= end


def _safe_school_terms(stored) -> list:
    """Read guard for user_settings.school_terms; never raises.

    SAT 5 §6 names this column as the one most at risk of console corruption,
    because it is the only stored value a student is ever told to hand-edit.

    `stored` is the raw cell — a list of term dicts if all is well, but
    possibly a scalar, a dict, or a list with one good term and one wrecked
    one. Returns a list of {'term': int, 'start_date': ..., 'end_date': ...}
    dicts, empty if nothing survived. Never raises.
    """
    # The inner safe_list with NO predicate is doing one job: guaranteeing
    # `stored` is iterable, so the comprehension cannot raise on a cell that
    # holds a bare string. Normalising runs BEFORE the usability filter so a
    # terms list written with the document's start/end spelling is judged on
    # its dates rather than dropped for its key names.
    normalised = [_normalise_term_keys(t) for t in safe_list(stored)]
    # Project each surviving term down to exactly the three keys every reader
    # expects, so a console edit cannot smuggle extra fields out to the client.
    # _is_usable_term has already proved all three keys are present and sane,
    # which is why these are plain subscripts rather than .get() calls.
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

    `stored` is the raw user_settings.subjects cell: a list of canonical
    subject names when written by set_subjects, None when the student has not
    been through onboarding yet, or anything at all after a console edit.
    Returns a list of names guaranteed to be in CANONICAL_SUBJECTS, so an
    empty result and "not onboarded" are the same answer to every caller —
    which is what the OnboardingForm gate in Main is testing.
    """
    # A plain loop rather than a comprehension because two things happen per
    # element (rename, then de-duplicate) and the de-duplication has to look
    # at what earlier iterations already kept.
    renamed = []
    for subject in safe_list(stored):
        # Guarded by isinstance because a dict is unhashable and would raise
        # inside .get(); a non-string simply falls through to the catalog
        # filter below, which rejects it.
        if isinstance(subject, str):
            subject = LEGACY_SUBJECT_RENAMES.get(subject, subject)
        # The rename table can map two stored names onto one canonical name, so
        # de-duplicate here rather than showing the student the same study twice.
        if subject not in renamed:
            renamed.append(subject)
    # The catalog check runs LAST, on the post-rename names, so a legacy name
    # is kept as its current one instead of failing membership and vanishing.
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

    `row` is a user_settings Row (always one _get_or_create_settings returned).
    Returns exactly seven keys, and the degraded value each falls back to is
    chosen so that a damaged cell is SAFE rather than merely present:

        theme                 str, 'light' | 'dark'        -> 'light'
        default_reminder_days list[int], each 1-365        -> [] (no reminders)
        notifications_enabled bool                         -> False (silent)
        school_year           int 2000-2100, or None       -> None (unset)
        school_terms          list of term dicts           -> [] (FR15 off)
        timezone              IANA name                    -> app default
        subjects              list[str] from the catalog   -> [] (re-onboard)

    Every one of those defaults errs towards doing LESS: an unreadable cell
    turns a feature off and shows the student an empty field they can refill,
    which is recoverable. A default that erred towards doing more would email
    the student, or claim a term configuration that is not really there.
    """
    # safe_number returns a float, but the Settings screen renders this value with
    # str() into a text box, and "2026.0" is not a school year — so it comes back
    # as an int once the range check has passed.
    stored_year = safe_number(
        _row_value(row, 'school_year'), default=None,
        minimum=_MIN_SCHOOL_YEAR, maximum=_MAX_SCHOOL_YEAR)
    return {
        # A theme the client cannot draw is worse than the wrong theme: the
        # dropdown would show a blank selection and common.apply_theme would
        # not know which stylesheet to toggle. _DEFAULT_THEME always renders.
        'theme': safe_choice(
            _row_value(row, 'theme'), _VALID_THEMES, _DEFAULT_THEME),
        # is_valid_reminder_day is the SAME predicate the write path enforces and the
        # SAME one reminders.run_reminder_check applies
        # to this column. Using anything else here would let the Settings screen
        # show a reminder day the dispatcher silently ignores.
        'default_reminder_days': safe_list(
            _row_value(row, 'default_reminder_days'), is_valid_reminder_day),
        # OFF is the default even though _SETTINGS_DEFAULTS creates the row
        # with True, and that is the whole point: default=False here is the
        # SAME default reminders.run_reminder_check applies to the same cell.
        # When the two readers disagreed, a damaged cell let the Settings
        # switch read "off" on screen while the dispatcher kept emailing —
        # and an unwanted email cannot be taken back, whereas a missing one is
        # visible on the dashboard anyway.
        'notifications_enabled': safe_bool(
            _row_value(row, 'notifications_enabled'), default=False),
        # None is a real, expected value here (the student has not filled the
        # box in), so it is passed through rather than substituted. The int()
        # is the float-to-int narrowing described above; it is safe because
        # safe_number has already proved the value is inside the year window.
        'school_year': int(stored_year) if stored_year is not None else None,
        # Degrades term-by-term, not all-or-nothing: three good terms and one
        # wrecked one leave the student with three working terms rather than a
        # blank grid. FR15 goes quiet only for the term that is actually bad.
        'school_terms': _safe_school_terms(_row_value(row, 'school_terms')),
        # safe_timezone falls back to the app default rather than to None,
        # because _datetime has to resolve "today" for EVERY screen and there
        # is no sensible way to render a dashboard without a timezone.
        'timezone': safe_timezone(_row_value(row, 'timezone')),
        # An empty list reads as "not onboarded" to the router in Main, so a
        # ruined subjects cell sends the student back through the picker —
        # annoying, but it ends with the list correct.
        'subjects': _safe_subjects(_row_value(row, 'subjects')),
    }


# --- settings callables ----------------------------------------------------

@anvil.server.callable
def get_settings() -> dict:
    """Return the current user's settings, lazily creating the row on first call.

    Takes nothing — the user is the session's, never a parameter, so one
    account can never ask for another's settings (NFR03). Returns the
    seven-key dict documented on _settings_row_to_dict. Raises
    AuthenticationFailed if nobody is signed in.

    Called once per browser session by common.get_session_settings, which
    caches the result; the router then reads the cache on every navigation for
    the onboarding gate and the theme, so this is not a per-page round trip
    (NFR01).
    """
    user = _require_user()
    row = _get_or_create_settings(user)
    return _settings_row_to_dict(row)


@anvil.server.callable
def update_settings(fields: dict) -> dict:
    """Whitelist-filter, validate, and persist a settings patch.

    `fields` is a PATCH, not a whole record: SettingsForm sends only the keys
    it actually built, and a key that is absent leaves the stored value alone.
    Any key outside _SETTINGS_FIELDS is dropped silently rather than refused,
    because a @anvil.server.callable is reachable from anything holding a
    session cookie — the client's key set is simply not trusted. 'subjects' is
    the key this actually protects: set_subjects is its only writer, so
    filtering it out here is what stops the VCE program rules being bypassed
    by a hand-made call.

    Returns the freshly re-read settings dict, so the caller stores what the
    database actually holds rather than what it hoped it sent. Raises
    AuthenticationFailed when signed out and ValueError, with a message meant
    for the student, on any failed validation. Writes to user_settings:
    theme, default_reminder_days, notifications_enabled, school_year,
    school_terms, timezone.
    """
    user = _require_user()
    row = _get_or_create_settings(user)
    # `fields or {}` covers a client that sends None for "nothing changed";
    # the comprehension then filters rather than raises, per the docstring.
    patch = {k: v for k, v in (fields or {}).items() if k in _SETTINGS_FIELDS}
    # Validate the WHOLE patch before writing any of it: _validate_settings returns
    # the values to persist rather than mutating in place, so a save that fails on
    # its third field leaves the first two unwritten instead of half-applying.
    clean = _validate_settings(patch)
    # Guarded because row.update() with no keyword arguments is a pointless
    # write, and this callable is reached by the theme dropdown on every
    # toggle. An empty patch is a legitimate no-op, not an error.
    if clean:
        row.update(**clean)
    # Re-read rather than returning `clean`: the response has to carry all
    # seven keys (the client replaces its whole session cache with it), and it
    # must be the guarded view, not the raw values just written.
    return _settings_row_to_dict(row)


# ===========================================================================
# SURFACE 2 of 4 — SUBJECT ONBOARDING   (spec §11; column: user_settings.subjects)
# ===========================================================================
# Every account locks in its actual VCE studies once, and that list then drives
# the editor's subject dropdown, the dashboard filter, the parser's alias
# ranking and the exam timetable. Main routes any signed-in user whose settings
# carry no subjects into OnboardingForm whatever hash they asked for; §12's
# Settings flow re-runs set_subjects to change them later.
#
# NOTE ON ORDERING: the settings VALIDATION helpers (_validate_settings,
# _validate_school_terms) sit below this section, not above it — they belong to
# surface 1. Their own banner says so.

# --- subject onboarding (spec §11) ------------------------------------------

@anvil.server.callable
def get_subject_catalog() -> list:
    """The picker catalog: [{'group': <area>, 'subjects': [<canonical>, ...]}, ...].

    Takes nothing and touches no table — SUBJECT_GROUPS is a module constant in
    _constants, transcribed from the VCAA study designs. Returns all 56
    studies in 10 learning areas, in display order, which is what
    common.SubjectPicker draws its grouped checkboxes from.

    _require_user() is still called and its result still thrown away: this is
    not secret data, but leaving one callable open would make "every callable
    starts with _require_user" untrue, and a rule with an exception is a rule
    nobody checks. Raises AuthenticationFailed when signed out.
    """
    _require_user()
    # list(subs), because SUBJECT_GROUPS stores each group as a TUPLE. The
    # catalog is module-level state shared by every request in the server
    # session (NFR06 forbids that state being mutable), so the client gets a
    # fresh list per call that it can sort or filter freely, and the shape
    # crossing the wire is an ordinary JSON array either way.
    return [{'group': g, 'subjects': list(subs)} for g, subs in SUBJECT_GROUPS]


def _clean_subjects(subjects) -> list:
    """Validate a subject selection against the catalog and the VCE rules.

    `subjects` is the raw list the picker posted: strings, in any order,
    possibly with duplicates, possibly using a pre-rename study name. Returns
    a de-duplicated list of canonical names, order preserved, at most
    MAX_SUBJECTS_PER_STUDENT (12) long and guaranteed to satisfy both program
    rules. Raises ValueError — with a sentence written for the student, not a
    validation code — on anything it cannot accept.

    THE TWO PROGRAM RULES COME FROM DIFFERENT PLACES, and the distinction is
    worth keeping straight because only one of them is a real VCE rule:

      * ENGLISH is VCAA's. Satisfactory completion of the VCE requires studies
        from the English group, so a program without one is not a VCE program
        at all. If the student picked none, 'English' is appended for them
        rather than refused — the client has already warned them in a confirm
        dialog, so the auto-add is never a surprise.

      * MATHEMATICS is NOT a VCAA rule. VCAA does not require any maths for
        the VCE; this is DotPoint's own client mandate — Will's students all
        take a maths, and the parser's bare "maths"/"math" aliases resolve to
        the student's single locked maths study, which needs one to exist.
        Because it is the client's rule and not the curriculum's, it is
        REFUSED rather than auto-filled: the app has no business picking which
        maths someone is enrolled in.

    _constants records the same distinction beside ENGLISH_GROUP and
    MATHS_GROUP, with the VCAA source URL. Keep the two in step.
    """
    require_list(subjects, 'Subjects')

    # 1. Per-entry: text check, legacy rename, catalog membership, dedupe.
    #    Order matters — the rename has to happen before the catalog test, or
    #    a stored 'Further Mathematics' would be reported as an unknown study.
    catalog = set(CANONICAL_SUBJECTS)
    # A set for the membership test and a list for the answer: the set keeps
    # the duplicate check O(1) per entry, the list keeps the picker's order,
    # which is the order the student will see their chips in.
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

    # 2. MATHEMATICS (client mandate, not VCAA). Refused, never auto-added —
    #    see the docstring. The message names the four studies so the student
    #    does not have to go hunting through the picker for what counts.
    if not any(s in MATHS_GROUP for s in clean):
        raise ValueError(
            "Select at least one mathematics study (Foundation, General, "
            "Methods or Specialist).")

    # 3. ENGLISH (VCAA requirement). Auto-added rather than refused, because
    #    every VCE program has one and 'English' is the safe default of the
    #    four; a student taking Literature or EAL will have ticked it already.
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

    # 4. RANGE, checked last so it sees the final list including any
    #    auto-added English. 12 is generous for a 4-6 study VCE program; the
    #    bound exists to catch a picker that submitted its whole catalog, not
    #    to police an unusually heavy load.
    if len(clean) > MAX_SUBJECTS_PER_STUDENT:
        raise ValueError(
            "That is more than %d subjects — choose the studies you are "
            "actually enrolled in." % MAX_SUBJECTS_PER_STUDENT)

    return clean


@anvil.server.callable
def set_subjects(subjects: list) -> dict:
    """Validate and lock in the user's VCE subjects; the sole writer of
    user_settings.subjects. Used by onboarding and the Settings change flow.

    `subjects` is the picker's raw selection — a list of study-name strings.
    Returns the whole settings dict (all seven keys), not just the subjects,
    because both callers push the response straight into the session cache
    that the router reads on every navigation. Raises AuthenticationFailed
    when signed out and ValueError for any rule _clean_subjects enforces.

    Writes exactly one column: user_settings.subjects. It is kept out of the
    update_settings whitelist so that this is the ONLY way in, which is what
    makes the two program rules unavoidable.
    """
    user = _require_user()
    row = _get_or_create_settings(user)
    # Validate BEFORE the write, so a rejected selection leaves whatever was
    # already locked in untouched — the student keeps a working app while they
    # fix the picker.
    clean = _clean_subjects(subjects)
    try:
        row.update(subjects=clean)
    except Exception:
        # try/except rather than a pre-check, because there is no cheap way to
        # ask an Anvil table whether a column exists — attempting the write IS
        # the test. The only expected failure is the 'subjects' column not
        # existing yet (Data Tables migration not applied after deploy), so the
        # raw platform error is replaced with the one instruction that fixes
        # it. See the _SETTINGS_DEFAULTS note: signup deliberately does not
        # name this column, which is why the failure surfaces here first.
        raise ValueError(
            "The database schema hasn't been migrated yet — apply the "
            "'subjects' column migration in the Anvil Data Tables view.")
    return _settings_row_to_dict(row)


# ===========================================================================
# SETTINGS WRITE VALIDATION  (surface 1 continued — used only by update_settings)
# ===========================================================================
# The write-side twins of the _safe_* read guards further up. These use the
# require_* family, so they RAISE: on the way IN there is a student watching a
# Save button who can be told what is wrong, which is exactly the situation the
# read guards do not have.

def _validate_settings(fields: dict) -> dict:
    """Validate a whitelisted settings patch; return the values to persist.

    `fields` has already been filtered to _SETTINGS_FIELDS by update_settings,
    so every key here is one this function knows. Returns a NEW dict of the
    cleaned values, ready to hand to row.update(**...). Raises ValueError with
    a student-facing message on the first field that fails.

    Only keys PRESENT in `fields` appear in the result — this is a patch, not a
    whole record, and an absent key must leave the stored value alone.
    """
    # Copy rather than mutate: update_settings still holds `fields`, and
    # returning a separate dict is what lets it write all-or-nothing.
    clean = dict(fields)

    # Every branch below is `if <key> in clean`, never `if clean.get(<key>)`:
    # the presence of the key is what says "the student changed this", and a
    # falsy new value (False, 0, an empty list) is a real change too.

    # CHOICE. _VALID_THEMES is the same pair the dropdown is built from, so a
    # theme that reaches the row is always one common.apply_theme can draw.
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
    """Validate the school-terms list; return it normalised, or raise.

    The write-side twin of _safe_school_terms, and the most consequential
    validator in this module, because school_terms is the ONLY stored value a
    student is ever told to type by hand and the only one another module
    silently depends on. nlp._try_parse_week_phrase resolves "Term 2 Week 5"
    (FR15) by counting weeks forward from `start_date` and then testing
    start <= due <= end, so a term that is merely well-FORMED but not sensible
    breaks FR15 with no error raised anywhere and nothing shown to the student.
    This function is the only place that can be caught and reported.

    `terms` is the raw list from the Settings screen (or from an import). Each
    element must be a dict carrying:

        term        int, MIN_TERM_NUMBER..MAX_TERM_NUMBER (1-4 — the Victorian
                    school year has exactly four)
        start_date  'YYYY-MM-DD' text, or the 'start' alias
        end_date    'YYYY-MM-DD' text, or the 'end' alias

    BOTH KEY SPELLINGS ARE ACCEPTED because the documents disagree: this
    module, nlp and the Settings screen all read start_date / end_date, while
    SAT 5 §4.2.3 documents the same two fields as start / end.
    _normalise_term_keys folds the aliases onto the canonical names rather than
    rejecting one spelling, so a hand-authored or document-conformant list
    imports instead of failing a format check whose cause the student cannot
    see.

    Returns a NEW list of dicts with exactly the three canonical keys, dates
    still as 'YYYY-MM-DD' TEXT — that is the shape §1 specifies for the
    simpleObject column, and the shape nlp expects to read back. Raises
    ValueError, with a student-facing sentence naming the term at fault, on the
    first problem found.
    """
    require_list(terms, 'School terms')

    # PASS 1 — per-entry checks: shape, term number, and the two dates. `clean`
    # collects the normalised entries so the whole-value checks below can work
    # on validated data instead of re-testing types.
    clean = []
    for index, raw in enumerate(terms):
        # Reported by POSITION, not by term number: a non-dict entry has no
        # term number to name it with, so 1-based `index + 1` is the only
        # handle the student has on which row of the Settings grid is wrong.
        if not isinstance(raw, dict):
            raise ValueError(
                'Each school term needs a term number and two dates — '
                'entry %d does not.' % (index + 1))
        # Aliases folded FIRST, so the three .get() calls below only have to
        # know one spelling each.
        term = _normalise_term_keys(raw)
        # The number is validated BEFORE the dates because it is what the two
        # date messages are labelled with: getting it first means the student
        # is told "Term 2 start date", not "entry 3's start date".
        number = require_int_in_range(
            term.get('term'), 'Term number', MIN_TERM_NUMBER, MAX_TERM_NUMBER)
        # require_iso_date_text, not require_date: this value must STAY a
        # 'YYYY-MM-DD' string. A real date object in a simpleObject column
        # would be serialised by Anvil however it pleased, and nlp reads these
        # back expecting text.
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

    # CHECK 1 — ORDERING. Each term must not end before it starts. Parsed back
    # out of the stored text with fromisoformat, which is safe without a
    # try/except only because require_iso_date_text above has already proved
    # every value is a real 'YYYY-MM-DD' date. Routed through the shared
    # require_not_after so a backwards school term and a backwards assessment
    # window read the same to the student.
    for term in clean:
        require_not_after(
            datetime.date.fromisoformat(term['start_date']),
            datetime.date.fromisoformat(term['end_date']),
            'Term %d start date' % term['term'],
            'Term %d end date' % term['term'])

    # CHECK 2 — UNIQUENESS. nlp looks a term up by NUMBER and takes the first
    # match, so two entries both calling themselves Term 2 make "Term 2 Week 5"
    # resolve against whichever happens to be stored first — a coin toss the
    # student never sees. `seen_numbers` is a set purely for the O(1)
    # membership test; the message names the number rather than the position,
    # because that is what the student typed twice.
    seen_numbers = set()
    for term in clean:
        if term['term'] in seen_numbers:
            raise ValueError(
                'Term %d is listed twice. Each term can only have one set of '
                'dates.' % term['term'])
        seen_numbers.add(term['term'])

    # CHECK 3 — OVERLAP. Two terms claiming the same weeks means a date in the
    # shared stretch belongs to both, and week counting from either start
    # answers differently for the same phrase.
    #
    # 'YYYY-MM-DD' strings sort chronologically, and require_iso_date_text
    # above guarantees every value is in that exact shape, so sorting the strings
    # is the same as sorting the dates — no parsing needed, and no risk of the
    # sort and the comparison below disagreeing about what a date is.
    ordered = sorted(clean, key=lambda t: t['start_date'])
    # Comparing only ADJACENT pairs is enough once the list is sorted by start
    # date: if any two terms overlap at all, then some neighbouring pair in
    # this order does too, so a single pass finds it. `ordered` is a separate
    # sorted copy, so the returned list keeps the order the student entered.
    for earlier, later in zip(ordered, ordered[1:]):
        # <= not <, because the dates are INCLUSIVE bounds — nlp tests
        # start <= due <= end — so a term starting on the day the previous one
        # ends really does share that day.
        if later['start_date'] <= earlier['end_date']:
            raise ValueError(
                'Term %d and Term %d overlap. School terms cannot share dates — '
                'check their start and end dates.'
                % (earlier['term'], later['term']))

    # `clean`, not `ordered`: the entries are returned in the order they were
    # given, so the Settings grid redraws the rows where the student put them.
    return clean


# ===========================================================================
# SURFACE 3 of 4 — NOTE CRUD + SEARCH   (spec §10 step 6; table: notes, §1)
# ===========================================================================
# FR10 is create / edit / delete / pin; FR11 is the free-text search plus the
# tag filter, combined with AND. Everything from here to the AUTHENTICATION
# banner serves the Notes screen (NotesForm draws the list, NoteEditorForm the
# dialog) and the linked-note picker inside AssessmentEditorForm, which reuses
# search_notes rather than growing a second query of its own.
#
# THREE RULES HOLD ACROSS THE WHOLE SURFACE:
#   * every callable opens with _require_user(), and every by-id path calls
#     _own_or_raise() on the row it just fetched — get_by_id() reaches straight
#     into the table and does NOT apply the user= scoping a search would have
#     (NFR03);
#   * a note the client names but which is no longer there RAISES ValueError
#     rather than returning a quiet False, so edit, delete and pin all fail the
#     same way and NotesForm has one message to write;
#   * nothing but a plain dict from _note_row_to_dict crosses back to the
#     client — a live Row would hand the browser write access to the table.
#
# The four note columns are read WITHOUT _row_value, unlike every settings
# read above. That is deliberate: §1 lists the notes table with "Migration:
# None", so its columns have existed since the table did and the
# NoSuchColumnError case _row_value defends against cannot arise here. The
# safe_* guards are still applied, because a cell's CONTENTS can still be
# wrong (an import, or a console edit).

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

    The read-guard boundary for the notes table, and the twin of
    _settings_row_to_dict: every cell is routed through a `safe_*` guard so one
    corrupt value costs the student one FIELD, not the whole Notes screen.
    Nothing in here raises.

    `row` is a notes Row the caller has already proved the user owns
    (_own_or_raise), because this function does not look at the `user` column
    at all — it is a formatter, not a gate.

    Returns exactly seven keys. What a damaged cell degrades to, and why that
    is the right answer in each case:

        id          str         row.get_id()          -> never degrades; it is
                                                         the handle every later
                                                         update/delete/pin call
                                                         comes back with
        title       str         non-text cell         -> '' (the note still
                                                         lists, with a blank
                                                         title the student can
                                                         see is wrong and fix)
        content     str         non-text cell         -> '' (an empty body is
                                                         already legitimate, so
                                                         the screen renders)
        tags        list[str]   bad ELEMENTS dropped  -> [] (an unusable tag is
                                                         dropped one at a time;
                                                         see _is_tag_text)
        is_pinned   bool        non-bool cell         -> False (unpinned: the
                                                         note stays in the list
                                                         instead of being
                                                         claimed as pinned at
                                                         the top of it)
        created_at  str | None  non-datetime cell     -> None
        updated_at  str | None  non-datetime cell     -> None

    The two timestamps become ISO strings rather than staying datetimes: they
    cross the wire to NotesForm, which formats them for display, and NFR08
    fixes that display format as 'DD MMM YYYY' — a string is the shape that
    survives the trip unambiguously.
    """
    def iso(stored):
        """One timestamp cell as an ISO string, or None if it is unreadable."""
        # A guard, not a formatter: .isoformat() on a cell that holds a string
        # raises AttributeError, and this runs once per note in the list.
        #
        # datetime is named FIRST inside the isinstance tuple only for
        # readability — a tuple test is an OR, so unlike safe_date (where the
        # two are separate branches and datetime subclassing date really does
        # decide the answer) the order here changes nothing.
        if isinstance(stored, (datetime.datetime, datetime.date)):
            return stored.isoformat()
        # None, not '' — the client tests this key for presence to decide
        # whether to show a "last edited" line at all, and an empty string
        # would render as a blank one instead of being skipped.
        return None
    return {
        # get_id(), not a stored column: Anvil's own row handle. It is what
        # every later update_note / delete_note / toggle_pin call names, and
        # what an assessment's linked_note_ids holds (FR12).
        'id': row.get_id(),
        'title': safe_text(row['title']),
        'content': safe_text(row['content']),
        # Filtered per ELEMENT, so one unusable tag costs one tag rather than
        # the whole list — and search_notes lowercases what it compares, so a
        # non-string surviving here would raise there instead.
        'tags': safe_list(row['tags'], _is_tag_text),
        'is_pinned': safe_bool(row['is_pinned'], default=False),
        'created_at': iso(row['created_at']),
        'updated_at': iso(row['updated_at']),
    }


def _validate_note_fields(fields: dict) -> dict:
    """Validate a note create/update patch; return a cleaned copy or raise.

    A patch, so each field is checked only when present — update_note sends just
    the fields the student edited, while create_note and the importer send all four.

    `fields` is a dict whose keys are drawn from EDITABLE_FIELDS_NOTE
    ('title', 'content', 'tags', 'is_pinned'); update_note has already filtered
    it to that whitelist, so nothing here has to police unknown keys. Permitted
    values per key:

        title      str, 1-MAX_TITLE_LENGTH (200) chars after stripping
        content    str, 0-MAX_NOTE_CONTENT_LENGTH (20000); blank is allowed
        tags       list of str, each 1-MAX_TAG_LENGTH (40) after stripping,
                   at most MAX_TAGS_PER_NOTE (20) once de-duplicated; None is
                   accepted and means "clear them all"
        is_pinned  bool, and only a real bool

    Returns a NEW dict carrying the same keys with cleaned values, ready for
    row.update(**...). Raises ValueError, with a sentence written for the
    student rather than a validation code, on the first field that fails.
    """
    # Copy rather than mutate: the caller still holds `fields`, and building a
    # separate dict is what lets create_note and update_note write all-or-
    # nothing — a failure on 'tags' leaves 'title' unwritten as well.
    out = dict(fields)

    # Each branch tests `in out`, never `out.get(...)`: presence of the key is
    # what says "the student changed this", and False / '' / [] are all real
    # new values a student can legitimately have chosen.

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
        # `seen` holds the LOWERCASED spellings and exists only for the
        # membership test; `deduped` holds the spellings actually stored. Two
        # containers because §1 specifies the tags column as "case-preserved,
        # comparisons case-insensitive": search_notes lowercases both sides of
        # its tag filter, so 'Maths' and 'maths' are one tag to FR11 and
        # keeping both would give the student two chips that behave alike —
        # but the capitals they typed are theirs to keep, so the FIRST
        # spelling wins and is the one written.
        seen, deduped = set(), []
        for t in tags:
            # allow_blank here, then dropped by the `if key` below: a blank tag
            # is the trailing empty box the editor's tag row leaves behind, not
            # a mistake worth stopping a save for.
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
    """Create a note owned by the current user; return its row id (FR10).

    `record` is the four-field dict NoteEditorForm builds in 'create' mode:
    'title' (required text), 'content' (markdown, may be blank), 'tags' (list
    of strings) and 'is_pinned' (bool). Any other key it might carry is
    ignored — the four are picked out by name below rather than passed through.

    Returns the new row's id as a string, which is what the editor hands back
    to NotesForm so the freshly saved note can be selected in the reloaded
    list. The whole note is NOT returned: the caller reloads through
    search_notes anyway, so sending the record twice would be wasted payload.

    Writes one row to `notes`: title, content, tags, is_pinned, user,
    created_at, updated_at. Raises AuthenticationFailed when signed out and
    ValueError, with a student-facing message, on any failed validation.
    """
    user = _require_user()
    # `record or {}` covers a client that sends None for an empty form; the
    # .get() calls below then answer None rather than raising AttributeError.
    record = record or {}
    # Every field is named explicitly rather than the dict being passed
    # through, so a hand-made call cannot smuggle in a column the student is
    # not allowed to set — 'user' and 'created_at' above all, which decide
    # ownership (NFR03) and would let a forged record claim someone else's.
    #
    # The `or` defaults make create_note total where update_note is a patch:
    # every column of a new row has to be given a value, and blank content, no
    # tags and unpinned are the right ones for a note the student just opened.
    clean = _validate_note_fields({
        'title': record.get('title'),
        'content': record.get('content') or '',
        'tags': record.get('tags') or [],
        'is_pinned': bool(record.get('is_pinned', False)),
    })
    # One `now` for both timestamps, read once: created_at and updated_at have
    # to be EQUAL on a brand new note, because NotesForm shows "edited" only
    # when the two differ. Two separate calls could land microseconds apart and
    # make a note look edited the moment it was created.
    now = datetime.datetime.now(datetime.timezone.utc)
    # UTC, not the student's timezone: the column is compared and sorted
    # across rows (search_notes orders on it), so it has to be one clock. The
    # conversion to Melbourne time happens at display, not at storage.
    row = app_tables.notes.add_row(
        title=clean['title'], content=clean['content'], tags=clean['tags'],
        is_pinned=clean['is_pinned'], user=user, created_at=now, updated_at=now)
    return row.get_id()


@anvil.server.callable
def update_note(row_id: str, fields: dict) -> dict:
    """Whitelist-filter, validate and apply an edit to an owned note (FR10).

    `row_id` is a notes row id as returned by create_note or carried in a
    _note_row_to_dict — a string, and one that comes from the CLIENT, so it is
    never trusted to be the caller's own. `fields` is a PATCH: only the keys
    the student actually changed, drawn from EDITABLE_FIELDS_NOTE ('title',
    'content', 'tags', 'is_pinned'). An absent key leaves the stored value
    alone; anything outside the whitelist is dropped in silence, because a
    @anvil.server.callable is reachable by anything holding a session cookie
    and the client's key set is simply not trusted (FR04's whitelist rule,
    applied to notes).

    Returns the SAVED note as a dict (the seven keys of _note_row_to_dict), so
    NoteEditorForm redraws from what the database actually holds rather than
    from what it hoped it sent. Writes to `notes`: whichever of the four
    editable columns were patched, plus updated_at. Raises
    AuthenticationFailed when signed out, ValueError when the note is gone or
    a field fails validation, and PermissionError when the note is somebody
    else's.
    """
    user = _require_user()
    row = app_tables.notes.get_by_id(row_id)
    # Missing is reported BEFORE ownership is tested, and both answers are
    # deliberately different sentences: a note the student deleted in another
    # tab is an ordinary race, whereas _own_or_raise's flat "Not your record"
    # is the security answer and says nothing about whether the row exists.
    if row is None:
        raise ValueError(
            "That note no longer exists — it may have already been deleted.")
    # get_by_id() does NOT scope by user the way search(user=user) would, so
    # this is the only thing standing between a stale or guessed id and
    # somebody else's note (NFR03).
    _own_or_raise(row, user)
    # Filter first, validate second: validating an unknown key would waste the
    # student's error message on a field they cannot see, and the whitelist is
    # what keeps 'user' and 'created_at' unwritable from the client.
    clean = _validate_note_fields(
        {k: v for k, v in (fields or {}).items() if k in EDITABLE_FIELDS_NOTE})
    if clean:
        # updated_at is stamped HERE rather than accepted from the client, so
        # the ordering search_notes sorts on cannot be forged, and it is added
        # only when something really changed — an empty patch is a legitimate
        # no-op (the editor saves on close whether or not a key was pressed)
        # and must not push an untouched note to the top of the list.
        clean['updated_at'] = datetime.datetime.now(datetime.timezone.utc)
        row.update(**clean)
    # Re-read through the guard rather than returning `clean`: the response has
    # to carry all seven keys including the ones this patch did not touch, and
    # it must be the guarded view of the row, not the raw values just written.
    return _note_row_to_dict(row)


@anvil.server.callable
def delete_note(row_id: str) -> bool:
    """Delete an owned note, first unlinking it from any of the user's assessments.

    `row_id` is the client-supplied notes row id (a string). Returns True on
    success; a missing note RAISES rather than returning False, so that a
    delete and a pin of a note someone else already removed fail the same way
    (toggle_pin raised while this returned a quiet False for the same cause).
    This is a deliberate departure from spec §2, which specifies `return False`
    for the missing case.

    Touches TWO tables: it deletes the `notes` row, and it rewrites
    `assessments.linked_note_ids` on every one of the user's assessments that
    referenced it — the write half of FR12. Raises AuthenticationFailed when
    signed out, ValueError when the note is already gone, and PermissionError
    when it belongs to someone else.
    """
    user = _require_user()
    row = app_tables.notes.get_by_id(row_id)
    if row is None:
        raise ValueError(
            "That note no longer exists — it may have already been deleted.")
    _own_or_raise(row, user)
    # WHY A TRANSACTION. linked_note_ids is a simpleObject list of note ids
    # (FR12), not a database foreign key, so nothing at the platform level
    # cleans up a reference when its note disappears. The unlink and the delete
    # therefore have to succeed or fail together: if the delete landed and the
    # unlink did not, every affected assessment would keep pointing at an id
    # that no longer resolves, and the editor's linked-note panel would show a
    # phantom entry the student could never remove.
    with tables.Transaction():
        # Scoped by user=, so the loop can only ever touch this student's
        # assessments — it is also what bounds the cost, since NFR01 sizes the
        # dataset at up to 100 assessments per user.
        #
        # A full scan rather than a query on linked_note_ids: Anvil cannot
        # index inside a simpleObject column, so "which rows mention this id?"
        # has no server-side query to ask.
        for a in app_tables.assessments.search(user=user):
            # safe_list because the column is simpleObject and may hold
            # anything at all; `in` on a bare string would otherwise match on a
            # SUBSTRING of it and unlink the wrong thing.
            linked = safe_list(a['linked_note_ids'])
            # Guarded so only the assessments that actually referenced this
            # note are written. Rewriting all of them would be a needless write
            # per assessment inside a transaction that is holding the table.
            if row_id in linked:
                # Rebuilt by comprehension rather than list.remove(): the copy
                # is a new list, so the row's own cached cell is never mutated
                # in place, and it also drops a duplicated id in one pass where
                # .remove() would strip only the first.
                a.update(linked_note_ids=[n for n in linked if n != row_id])
        # Deleted LAST, so if a linked_note_ids write raises, the transaction
        # rolls back with the note still there and the student can retry.
        row.delete()
    return True


@anvil.server.callable
def toggle_pin(row_id: str) -> bool:
    """Flip a note's pinned state; return the new value (FR10).

    A toggle rather than a setter: the client sends only the id and is told
    what the state became, so two tabs cannot fight over a stale value they
    each believed was current. FR10 requires pinned notes to sort to the top,
    which search_notes does on this column.

    `row_id` is the client-supplied notes row id (a string). Returns the NEW
    is_pinned value, True or False, which NotesForm uses to redraw the one pin
    icon without reloading the list. Writes `notes`: is_pinned and updated_at.
    Raises AuthenticationFailed when signed out, ValueError when the note is
    gone, and PermissionError when it is somebody else's.
    """
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
    """Return the user's notes (pinned-first, then recent) filtered by query/tag (FR11).

    The one read path for notes anywhere in the app: NotesForm fills its whole
    screen from a single call, NoteEditorForm uses it to load one note (there
    is no single-note getter), and AssessmentEditorForm's linked-note picker
    reuses it rather than growing a second query (FR12).

    All three parameters are OPTIONAL filters, combined with AND per FR11:

        query       str or None — case-insensitive SUBSTRING match against
                    title + ' ' + content. None or blank means "no filter".
                    Bounded to MAX_TITLE_LENGTH (200) characters.
        tag         str or None — case-insensitive EXACT match against one
                    entry of the tags list (not a substring, so 'chem' does not
                    match 'chemistry'). Bounded to MAX_TAG_LENGTH (40).
        pinned_only bool — keep only pinned notes. Any truthy value works.

    Returns a list of _note_row_to_dict dicts, ordered pinned-first and then
    most-recently-updated-first (FR10's "pinned notes always sort to the top").
    An empty list is a normal answer, not an error: the client draws its own
    "no notes match" message. Reads `notes` only — no writes. Raises
    AuthenticationFailed when signed out and ValueError if a filter argument is
    the wrong type or too long.
    """
    user = _require_user()
    # `query` and `tag` arrive from the search box, so they get the require_* family:
    # allow_blank because an empty box means "no filter", not an error.
    # Both are lowercased ONCE here rather than inside the comprehensions
    # below, which would redo the same work per row.
    needle = require_text(
        query, 'Search', MAX_TITLE_LENGTH, allow_blank=True).lower()
    want = require_text(tag, 'Tag', MAX_TAG_LENGTH, allow_blank=True).lower()
    # bool(), not require_bool: this is a display flag that is never stored, so
    # a truthy value from a checkbox is a clear enough intention. require_bool
    # is reserved for values that reach a column, where a loose type would
    # outlive the request.
    pinned_only = bool(pinned_only)

    # user=user is the NFR03 scoping — the query itself can only return this
    # student's notes, so nothing below needs a second ownership test.
    # list() materialises it because Anvil returns a lazy search iterator and
    # .sort() needs a real list to sort in place.
    rows = list(app_tables.notes.search(user=user))

    def _sort_key(r):
        """Sort position for one note row: (unpinned?, negated timestamp)."""
        # This key runs for every note, so it uses the safe_* family: a timestamp
        # cell that is not a datetime would otherwise raise AttributeError here and
        # take the whole Notes screen down instead of mis-sorting one row.
        updated = r['updated_at']
        ts = updated.timestamp() if isinstance(updated, datetime.datetime) else 0
        # A tuple key, ascending on both parts, gives the two-level ordering in
        # one pass: 0 sorts before 1 so pinned notes lead, and NEGATING the
        # timestamp turns "largest first" into an ascending compare, which is
        # what lets one sort() do the work of two.
        #
        # 0 is the fallback for an unreadable timestamp, and -0 is the largest
        # value this half can take — so a damaged row sinks to the bottom of
        # its group rather than claiming the top.
        return (0 if safe_bool(r['is_pinned'], default=False) else 1, -ts)
    # Sorted BEFORE the filters, not after. The three filters below are
    # order-preserving comprehensions, so the answer is identical either way;
    # doing it first keeps FR10's ordering rule in one place at the top,
    # ahead of the optional filtering. NFR01 sizes this at 50 notes, so the
    # comparisons the filters would have saved are not measurable.
    rows.sort(key=_sort_key)

    # Three separate `if` blocks, each rebinding `rows`, rather than one
    # combined predicate: FR11 specifies AND, and chaining the filters is the
    # cheapest way to get it — each one only sees what survived the last, and
    # a filter that was not asked for costs nothing at all.
    if needle:
        # title and content are joined with a space so a phrase cannot match
        # across the seam between them ('...my titlethe body...'). safe_text on
        # both, because a non-text cell would raise on the + and .lower().
        rows = [r for r in rows
                if needle in (safe_text(r['title']) + ' '
                              + safe_text(r['content'])).lower()]
    if want:
        # `==` not `in`: FR11 defines the tag filter as an exact match, so
        # filtering on 'chem' must not sweep up every note tagged 'chemistry'.
        # _is_tag_text drops non-strings first, since .lower() would raise.
        rows = [r for r in rows
                if any(want == t.lower()
                       for t in safe_list(r['tags'], _is_tag_text))]
    if pinned_only:
        # The same default=False the sort key used, so a damaged cell is
        # treated as unpinned by both and cannot sort to the top of a list it
        # was then filtered out of.
        rows = [r for r in rows if safe_bool(r['is_pinned'], default=False)]

    # Converted to dicts LAST, so the guard runs only over the rows that
    # actually survived rather than over every note the student owns.
    return [_note_row_to_dict(r) for r in rows]


# ===========================================================================
# SURFACE 4 of 4 — CUSTOM AUTHENTICATION   (spec §5 workaround; FR20)
# ===========================================================================
# WHY THESE EXIST AT ALL. Spec §5 says to call
# anvil.users.login_with_form(allow_signup=True) straight from LoginForm and be
# done with it. In this app that raises "PermissionDenied: Cannot access this
# table from server code" against the users table — a Users-service-to-table
# binding problem, not something the calling code can fix. Running the SAME two
# operations from a trusted server module instead uses this module's full
# users-table access and sidesteps that path entirely. LoginForm therefore
# draws its own two prompts and calls these.
#
# WHAT THIS APP NEVER HANDLES. Neither function stores a password, hashes one,
# or writes anything to the users table itself. The plaintext the student typed
# is passed straight to anvil.users.signup_with_email / login_with_email, and
# the Users service does the hashing and the checking behind its own API; the
# only column DotPoint ever reads off a user row is the email address. That is
# the whole reason FR20 says "authenticate via the Anvil Users service" rather
# than describing a scheme of our own.
#
# THESE TWO ARE THE ONLY CALLABLES IN THE APP WITHOUT _require_user(), and they
# have to be: they are how somebody BECOMES a signed-in user. _auth's module
# docstring names them as the two documented exemptions. Neither takes a row id
# and neither reads a user-owned table, so there is nothing for NFR03 to scope.
#
# Both end by calling _get_or_create_settings, which is what guarantees a
# usable settings row exists before Main's router asks for one — and both
# return True on success and raise ValueError, carrying a sentence written for
# a student, on every failure.

@anvil.server.callable
def create_account(email: str, password: str) -> bool:
    """Sign a new student up, log them straight in, and give them settings.

    `email` is the raw text of the sign-up box; it is validated for format and
    stored lowercased, because it doubles as the account's identity and a
    student who typed one capital on Tuesday must still sign in on Wednesday.
    `password` is the plaintext the student typed. It is checked for a minimum
    length and then handed to Anvil — this app never stores it, hashes it, or
    keeps it past the end of the call.

    Returns True; the caller (LoginForm) treats any return at all as success
    and reloads Main, because force_login below has already established the
    session. Writes one `user_settings` row via _get_or_create_settings, and
    causes Anvil's Users service to write one `users` row.

    Raises ValueError, never a platform exception, on all three failure modes:
    a malformed address, a password under _MIN_PASSWORD_LENGTH, and an email
    already registered. Anvil's UserExists is caught and re-raised as a
    sentence, because the raw platform error would reach LoginForm's alert box
    as a class name the student cannot act on.
    """
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
        # The password leaves this function here and is not kept: Anvil's Users
        # service hashes it behind its own API, and no column DotPoint reads or
        # writes ever holds it. remember=True issues the "remember me" cookie
        # that _require_user()'s allow_remembered=True later honours, so a
        # student who just signed up is not bounced to login on the next load.
        new_user = anvil.users.signup_with_email(email, password, remember=True)
    except anvil.users.UserExists:
        # Translated into a sentence rather than let through: LoginForm shows
        # whatever this raises in an alert box, and "anvil.users.UserExists"
        # tells a student nothing they can do. It is safe to be specific HERE,
        # unlike in sign_in_with_email — a signup form has to say the address
        # is taken or there is no way forward, and it is the person who owns
        # the address asking.
        raise ValueError("An account with that email already exists — try signing in.")
    # signup_with_email creates the row but does NOT start a session, so
    # without this the student would be sent back to the login screen to type
    # the password they just chose.
    anvil.users.force_login(new_user)
    # Called eagerly rather than left to the first get_settings, so the row is
    # in place before Main's router asks for it. It also gives the new account
    # an EMPTY subjects list, which is exactly what the router reads as "not
    # onboarded" and what sends the student into OnboardingForm (§11).
    _get_or_create_settings(new_user)
    return True


@anvil.server.callable
def sign_in_with_email(email: str, password: str) -> bool:
    """Sign an existing student in and make sure they have a settings row.

    `email` is the raw text of the sign-in box, trimmed and lowercased so it
    matches whatever create_account stored. `password` is the plaintext the
    student typed; as in create_account it is handed straight to Anvil's Users
    service, which does the checking against its own hash — this app never sees
    a stored password and has no way to compare one itself.

    Returns True on a successful sign-in; login_with_email has already
    established the session by then, so LoginForm just reloads Main. Ensures a
    `user_settings` row exists via _get_or_create_settings, which is the whole
    reason the function does not simply end at the login call.

    Raises ValueError on both failure modes, and the WORDING is the security
    decision here: FR20 requires a generic failure message so that email
    addresses cannot be enumerated, so a wrong password and an address that was
    never registered are given the identical "Incorrect email or password."
    Anvil's AuthenticationFailed is caught and replaced for that reason as much
    as for readability.
    """
    # Existence only, deliberately. Applying the format and length rules from
    # create_account here would lock out any account created before those rules
    # existed, and an address that fails the pattern still deserves the honest
    # "incorrect email or password" answer rather than a different one that would
    # confirm to a stranger which addresses are registered.
    # `email or ''` so a client sending None reaches .strip() as a string;
    # .lower() to match what create_account stored, since a student who
    # capitalised the first letter today is the same account as yesterday.
    email = (email or '').strip().lower()
    # A DIFFERENT message from the one below, and that is not an enumeration
    # leak: an empty box says nothing about which addresses are registered, and
    # "Incorrect email or password" for a form the student left blank would be
    # actively misleading.
    if not email or not password:
        raise ValueError("Enter both your email address and your password.")
    try:
        # remember=True pairs with _require_user()'s allow_remembered=True: it
        # is what makes the "stay signed in" behaviour work across visits.
        user = anvil.users.login_with_email(email, password, remember=True)
    except anvil.users.AuthenticationFailed:
        # ONE message for both "no such account" and "wrong password", which is
        # FR20's requirement: two different answers would let anyone test an
        # address and learn whether it is registered here.
        raise ValueError("Incorrect email or password.")
    # The reason this function is not just the login call. An account can
    # predate the user_settings table, or have been added through the Anvil
    # Users console, and Main's router reads settings on the very first
    # navigation, so the row is guaranteed here rather than left to fail late.
    _get_or_create_settings(user)
    return True
