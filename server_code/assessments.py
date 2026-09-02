import anvil.secrets
import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""Assessment CRUD, bulk add and export/import (FR02, FR03, FR04, FR05, FR06,
FR07, FR09, FR18, FR19).

Every path that writes an assessment — create_assessment, create_bulk_assessments,
update_assessment (via _validate_field) and import_user_data — funnels through
_validate_assessment_payload(), so the app has exactly one definition of what a
valid assessment is and one set of messages for breaking it (SAT criterion 7.3).

Every callable resolves the user via _require_user() first; every update/delete
re-checks ownership with _own_or_raise. No live Row ever leaves the server —
results go through _row_to_dict, which read-guards every cell on the way out.

See IMPLEMENTATION_SPEC.md section 2 (server_code/assessments.py).
"""

import anvil
import anvil.server
import datetime
import json

from ._auth import _require_user, _own_or_raise
from ._datetime import _user_today, _format_date_au, _urgency_band
from ._constants import (
    ALLOWED_SORT_KEYS, EDITABLE_FIELDS_ASSESSMENT, SUBJECT_ALIASES,
    LEGACY_SUBJECT_RENAMES, VALID_TYPES, VALID_STATUSES, VALID_CONFIDENCE,
    STATUS_COMPLETED, STATUS_DEFAULT,
    MAX_TITLE_LENGTH, MAX_DESCRIPTION_LENGTH, MAX_SOURCE_TEXT_LENGTH,
    MIN_WEIGHT, MAX_WEIGHT,
    MIN_REMINDER_DAY, MAX_REMINDER_DAY, MAX_REMINDER_DAYS_PER_ASSESSMENT,
    MAX_BULK_LINES,
)
from ._validation import (
    require_present, require_text, require_choice, require_date, require_list,
    require_number_in_range, require_int_in_range, round_percentage,
    require_not_after, require_within_horizon, require_complete_record,
    safe_text, safe_bool, safe_number, safe_choice, safe_list, safe_date,
    is_positive_int, is_valid_reminder_day,
)
from .notes import (
    _get_or_create_settings, _settings_row_to_dict, _note_row_to_dict,
    _validate_note_fields, update_settings as _apply_settings,
)

# Every canonical subject a picker choice or a parser match can produce. Built once
# from SUBJECT_ALIASES so this set cannot drift away from the alias table.
_CANONICAL_SUBJECT_VALUES = frozenset(SUBJECT_ALIASES.values())

# Applied when a record carries no reminder_days of its own: one week's notice and
# a final two-day warning.
_DEFAULT_REMINDER_DAYS = (7, 2)

# One message for "that row is gone", shared by get/update/delete so the three
# by-id paths cannot describe the same condition differently (rubric 7.3).
_MISSING_ASSESSMENT_MESSAGE = (
    'That assessment no longer exists — it may have already been deleted.')


# --- validation ------------------------------------------------------------

def _trim_parser_text(value, field_label):
    """Bound a parser AUDIT field (source_text, term_info) without rejecting the row.

    These two columns hold the parser's own echo of what it read, not something the
    student typed into a labelled box. A wrong type is still refused, but an
    over-long value is trimmed rather than raised: refusing a whole assessment over
    its provenance note would help nobody, and an export written before this cap
    existed still has to import.

    value        the stored/incoming audit text, or None.
    field_label  the field's name as the student would see it, used in the message.

    Returns the trimmed text (at most MAX_SOURCE_TEXT_LENGTH characters) or None.
    Feeds assessments.source_text and assessments.term_info. Raises ValueError
    only on a wrong type.
    """
    # 1. Absent is not an error. A manually-entered assessment never went through
    #    the parser, so it has no echo to record and None passes straight through.
    if value is None:
        return None
    # 2. Type is still enforced, even though length is not: a non-string means the
    #    caller sent the wrong shape entirely, and .strip() below would raise a
    #    bare AttributeError instead of a message the student can act on.
    if not isinstance(value, str):
        raise ValueError('%s must be text.' % field_label)
    # 3. Strip BEFORE slicing, so the cap counts real characters rather than
    #    padding. Text that was nothing but whitespace becomes '', and the trailing
    #    `or None` collapses that to None — the same "no audit text" state as an
    #    absent value, so the column has one empty representation instead of two.
    return value.strip()[:MAX_SOURCE_TEXT_LENGTH] or None


def _validate_assessment_payload(record: dict, user, today=None) -> dict:
    """Validate a whole assessment record; return the persistable fields or raise.

    The single write-side gate for this module (see the module docstring). Every
    path that writes an assessment — create_assessment, create_bulk_assessments,
    _validate_field (and through it update_assessment) and import_user_data —
    comes through here, so the app has exactly one definition of a valid
    assessment and one set of messages for breaking it (SAT criterion 7.3).

    record  the untrusted dict from a client form, the parser preview, a bulk line
            or an export file. Keys are the assessment column names; anything that
            is not a dict is treated as an empty one, so a junk call reports
            "Title is required." rather than dying on .get().
    user    the owner row, used only to prove the linked notes belong to them
            (NFR03). None on the import path, which empties linked_note_ids first
            so no ownership check is reached — see _validate_import_payload.
    today   the user's local today (datetime.date), which switches ON the due-date
            horizon check. None means "skip that check", which is what the import
            path passes.

    Returns a dict of exactly the columns this module persists:
      title str, subject str (canonical), type str (VALID_TYPES),
      due_date date, start_date date|None, weight float|None (0-100, 2 dp),
      status str (VALID_STATUSES), description str|None,
      reminder_days list[int] (each 1-365, at most 6),
      linked_note_ids list[str], confidence 'HIGH'|'MEDIUM'|'LOW'|None,
      source_text str|None, term_info str|None.
    It deliberately does NOT set user, created_at or updated_at: the bulk and
    import paths stamp one shared timestamp across a whole batch, so the caller
    owns those three columns.

    Reads the `notes` table to resolve linked_note_ids; the returned dict is what
    the caller writes to `assessments`. Raises ValueError with a student-readable
    message on the first field that fails, and PermissionError if a linked note
    belongs to somebody else.
    """
    # 1. Normalise the container before touching it. A non-dict (a client bug, or
    #    a hand-edited export holding a list where a record belongs) becomes {},
    #    which then fails the field checks below with a message about the missing
    #    field rather than an AttributeError from .get().
    record = record if isinstance(record, dict) else {}
    # Accumulates ONLY the columns that passed. Building a fresh dict rather than
    # editing `record` in place is what stops an unknown key the client invented
    # from reaching add_row() — the whitelist is the act of copying across.
    validated_fields = {}

    # 2. Title — existence + type + range in one call (require_text does all
    #    three, because for text they are the same complaint to the student).
    validated_fields['title'] = require_text(
        record.get('title'), 'Title', MAX_TITLE_LENGTH)

    # 3. Subject — range check against the canonical catalogue, but only AFTER the
    #    legacy rename coercion below.
    subject = record.get('subject')
    # Rows and exports written before a VCAA study rename carry the old name. The
    # coercion runs BEFORE the choice check, which is what keeps legacy data
    # editable and old export files importable.
    subject = LEGACY_SUBJECT_RENAMES.get(subject, subject)
    # Existence is asked separately here, unlike type and status, because
    # require_choice lists every permitted value and the subject catalogue is the
    # whole VCE study list. "Subject is required." is the useful answer for a bulk
    # line the parser could not name; the full list is only worth printing when the
    # student actually supplied something that is not on it.
    require_present(subject, 'Subject')
    validated_fields['subject'] = require_choice(
        subject, _CANONICAL_SUBJECT_VALUES, 'Subject')

    # 4. Type — range check on its own: VALID_TYPES has six members, so the
    #    "choose one of ..." message require_choice builds is short enough to be
    #    the whole answer, and no separate existence check is needed (a missing
    #    type is simply not in the set).
    validated_fields['type'] = require_choice(
        record.get('type'), VALID_TYPES, 'Type')

    # 5. Due date — format check. require_date accepts a date, a datetime (Anvil
    #    hands one back for some columns) or an ISO 'YYYY-MM-DD' string (what an
    #    export file carries), and normalises all three to datetime.date so the
    #    comparisons at the end of this function have one type to work with.
    validated_fields['due_date'] = require_date(record.get('due_date'), 'Due date')

    # 6. Start date — the same format check, but optional. The truthiness test
    #    covers both None and the empty string a cleared DatePicker sends, and an
    #    absent start date is stored as None rather than defaulting to today: the
    #    student has not said when they will start, and guessing would put a false
    #    date in front of them.
    start_date = record.get('start_date')
    validated_fields['start_date'] = (
        require_date(start_date, 'Start date') if start_date else None)

    # 7. Weight — optional, then type + range (0-100), then format (2 dp).
    weight = record.get('weight')
    if weight is None or weight == '':
        # Weight is optional: not every assessment carries a published percentage.
        validated_fields['weight'] = None
    else:
        # Rounded only after the range check, so the stored value and the value the
        # student sees can never disagree. require_number_in_range accepts numeric
        # TEXT as well, because the manual form's weight box is a TextBox; it
        # rejects bool outright, since bool subclasses int in Python and a stored
        # True would otherwise sail through as a perfectly legal weight of 1%.
        validated_fields['weight'] = round_percentage(
            require_number_in_range(weight, 'Weight (%)', MIN_WEIGHT, MAX_WEIGHT))

    # 8. Status — range check, defaulted rather than required. A new assessment
    #    that says nothing about its status has not been started, so `or` supplies
    #    STATUS_DEFAULT; that also converts an empty-string status from a form into
    #    the default instead of a "not a valid status" complaint the student did
    #    not earn.
    validated_fields['status'] = require_choice(
        record.get('status') or STATUS_DEFAULT, VALID_STATUSES, 'Status')

    # 9. Description — type + range only. allow_blank, because most assessments
    #    genuinely have nothing to add beyond the title.
    description = require_text(
        record.get('description'), 'Description', MAX_DESCRIPTION_LENGTH,
        allow_blank=True)
    # Stored as None rather than '' so an empty cell keeps meaning "no description"
    # instead of "a description that happens to be empty".
    validated_fields['description'] = description or None

    # 10. Reminder days — type check on the list, range check on its length, then
    #     type + range on every element. Note the asymmetry with status above:
    #     `is None` rather than falsiness, because an empty list is a deliberate
    #     "do not email me about this one" and must not be quietly refilled with
    #     the default pair.
    reminder_days = record.get('reminder_days')
    if reminder_days is None:
        # list() of the module constant: _DEFAULT_REMINDER_DAYS is a tuple so that
        # nothing can edit the shared default in place, but the column stores a
        # list and require_list below demands one, so it is copied out per record.
        reminder_days = list(_DEFAULT_REMINDER_DAYS)
    require_list(reminder_days, 'Reminder days')
    if len(reminder_days) > MAX_REMINDER_DAYS_PER_ASSESSMENT:
        raise ValueError(
            'An assessment can have at most %d reminders (you set %d).'
            % (MAX_REMINDER_DAYS_PER_ASSESSMENT, len(reminder_days)))
    # Each offset is bounded as well as typed: an unbounded value such as 999999
    # made every assessment permanently "due soon" and emailed the student about
    # all of them on the first scheduler tick.
    validated_fields['reminder_days'] = [
        require_int_in_range(day, 'Reminder days', MIN_REMINDER_DAY, MAX_REMINDER_DAY)
        for day in reminder_days
    ]

    # 11. Linked notes (FR12) — type check on the list and on each element, then
    #     an existence check against the notes table and an ownership check on
    #     every hit. The ids arrive from the client, so "does this note exist" and
    #     "is it yours" both have to be asked here rather than trusted: a row id is
    #     just a string, and one guessed or copied from another account would
    #     otherwise create a live cross-user link (NFR03).
    linked_note_ids = record.get('linked_note_ids') or []
    require_list(linked_note_ids, 'Linked notes')
    for note_id in linked_note_ids:
        if not isinstance(note_id, str):
            raise ValueError('Linked notes must be chosen from your own notes.')
        try:
            note = app_tables.notes.get_by_id(note_id)
        except Exception:
            # A malformed id makes Anvil raise rather than return None; to the
            # student a stale link and a broken one mean the same thing.
            note = None
        if note is None:
            raise ValueError(
                'One of the linked notes no longer exists. Unlink it and try again.')
        _own_or_raise(note, user)
    validated_fields['linked_note_ids'] = linked_note_ids

    # 12. Confidence (FR17) — range check, but only when a value is present. None
    #     is the documented value for a manually-entered row: it means "no parser
    #     was involved", which is different from a parse that scored LOW, so the
    #     two must not be collapsed into one another by defaulting.
    confidence = record.get('confidence')
    validated_fields['confidence'] = (
        require_choice(confidence, VALID_CONFIDENCE, 'Confidence')
        if confidence is not None else None)

    # 13. The parser's audit trail. Both are trimmed rather than refused — see
    #     _trim_parser_text. source_text is NOT in EDITABLE_FIELDS_ASSESSMENT, so
    #     this is the only place it is ever written; term_info is editable,
    #     because the student may need to correct a term phrase the parser
    #     misread, and it is a description of the input rather than the input.
    validated_fields['source_text'] = _trim_parser_text(
        record.get('source_text'), 'Source text')
    validated_fields['term_info'] = _trim_parser_text(
        record.get('term_info'), 'Term information')

    # --- whole-record checks: reasonableness and completeness ---------------
    # Everything above judges ONE field against its own rule. The checks below can
    # only run once every field is individually valid, because each of them reads
    # more than one field (or compares a field against the world). That is what
    # makes this a record-level layer rather than three more field checks.

    # 14. Completeness: the dict about to be persisted must still carry the four
    #     fields an assessment cannot exist without. Each was checked individually
    #     above, so this is the closing gate rather than the first line of defence
    #     — it is what would catch a later edit that adds a branch leaving one of
    #     them unset, before a half-built row reaches add_row(). It also reports
    #     ALL the missing pieces at once, which matters most on a bulk line where
    #     the student would otherwise resubmit four times to find four omissions.
    require_complete_record(
        validated_fields,
        [('title', 'Title'), ('subject', 'Subject'), ('type', 'Type'),
         ('due_date', 'Due date')],
        'This assessment')

    # 15. Reasonableness: work cannot start after it is due. Both dates are valid
    #     on their own; only the pair is wrong, which is precisely why no
    #     field-level check could ever catch it. Does nothing when start_date is
    #     None, since one date is not a pair.
    require_not_after(
        validated_fields['start_date'], validated_fields['due_date'],
        'Start date', 'Due date')

    # 16. Reasonableness: a due date years from today is a mistyped year, not a
    #     plan — 2062 for 2026 passes every type and format check and then sorts
    #     to the end of the dashboard forever. This one compares a field against
    #     the world rather than against another field, so it needs `today`.
    #     `today` is None on the import path, where require_within_horizon returns
    #     immediately: an export is legitimately old, and refusing to restore a
    #     student's own backup because its dates are behind us would defeat the
    #     point of having one.
    require_within_horizon(validated_fields['due_date'], today, 'Due date')

    return validated_fields


def _validate_field(key, value, user, existing=None, today=None):
    """Validate a single editable field for update_assessment (reuses payload rules).

    Builds the smallest record that will pass the shared validator, overwrites the
    one field being edited, and returns just that field back. Editing one field
    therefore applies exactly the rule a create would, which is what stops the
    edit path and the create path from drifting apart (SAT criterion 7.3).

    key       the column being edited. The caller (update_assessment) has already
              checked it against the EDITABLE_FIELDS_ASSESSMENT whitelist, so this
              function may assume it is a real, editable column name.
    value     the new value, straight from the client and entirely untrusted.
    user      the owner row, needed only when `key` is 'linked_note_ids'.
    existing  the row as it stands, from _row_to_dict, so a cross-field check has
              the row's real dates to compare against instead of a placeholder.
              Its dates are ISO strings; require_date accepts those.
    today     the user's local today, or None to skip the horizon check.

    Returns the single validated value for `key`, ready to hand to row.update().
    Raises whatever _validate_assessment_payload raises.
    """
    existing = existing or {}
    # A stub that satisfies the four required fields, so the shared validator can
    # run at all. These placeholders are never persisted: only validated[key] is
    # returned. 'x' and 'other' are simply the cheapest legal values, and the
    # subject is taken from the canonical set rather than hard-coded so a future
    # VCAA rename cannot leave a dead literal here.
    stub = {
        'title': 'x', 'subject': next(iter(_CANONICAL_SUBJECT_VALUES)),
        'type': 'other', 'due_date': datetime.date.today(),
    }
    # Editing either date has to be judged against the OTHER date already stored,
    # otherwise "Start date cannot be after Due date" would compare the new value
    # against the placeholder above. Only the dates are carried over: a legacy
    # subject or an over-long stored title must not block an unrelated edit.
    if key in ('due_date', 'start_date'):
        if existing.get('due_date'):
            stub['due_date'] = existing['due_date']
        if existing.get('start_date'):
            stub['start_date'] = existing['start_date']
    # Applied last, so the field under edit overwrites whatever the stub (or the
    # carried-over date above) put there.
    stub[key] = value

    # The horizon rule belongs to the date being SET, not to whatever the row
    # already holds, so it applies only when the due date itself is being edited —
    # otherwise renaming a two-year-old assessment would be refused.
    horizon_today = today if key == 'due_date' else None
    validated = _validate_assessment_payload(stub, user, today=horizon_today)
    return validated[key]


# --- serialisation ---------------------------------------------------------

def _is_text_value(value):
    """safe_list element predicate: a non-empty string (a row id, or a filter value)."""
    return isinstance(value, str) and bool(value.strip())


def _row_to_dict(row) -> dict:
    """Plain-dict view of an assessment row; dates as ISO strings, incl. 'id'.

    This is the boundary where a database row becomes client data, and it exists
    for two reasons.

    First, no live Anvil Row may ever be returned to the client: a Row is a handle
    onto the table, so returning one would hand the browser a writable view of the
    database rather than a copy of one student's record (NFR03). Every callable in
    this module therefore returns dicts, and 'id' carries row.get_id() so the
    client can name the row again on the next call without holding it.

    Second, every cell passes through a safe_* read guard rather than being
    trusted. The write path above validates hard, but a stored row can still be
    wrong: it may predate a rule, or have been typed straight into the Anvil Data
    Tables console, which bypasses every validator in this file. reminder_days and
    linked_note_ids are simpleObject columns, which accept ANY JSON at all. This
    dict feeds the dashboard, the export file and the reminder emails, so one
    damaged cell has to degrade to a documented default, not take a screen down.

    row  a live `assessments` row. Returns a dict with the keys listed below;
    never raises.
    """
    def iso_date(value):
        """A stored date as 'YYYY-MM-DD', or None if the cell holds anything else."""
        date_value = safe_date(value)
        return date_value.isoformat() if date_value is not None else None

    def iso_timestamp(value):
        """A stored timestamp in full ISO form; the time component is kept."""
        # Unlike iso_date this keeps the time, because created_at/updated_at are
        # audit stamps: "which of these two edits was last" is the question they
        # answer, and a date alone cannot. A guard, not a formatter — .isoformat()
        # on a cell holding a string would raise AttributeError.
        if isinstance(value, (datetime.datetime, datetime.date)):
            return value.isoformat()
        return None

    # Dates leave as ISO 'YYYY-MM-DD' strings rather than date objects: this dict
    # is JSON-serialised straight into the export file (FR18), and the client
    # re-reads them. Display formatting to 'DD MMM YYYY' (NFR08) happens later, in
    # _decorate, so the machine-readable and human-readable forms stay separate.
    return {
        # The row's own Anvil id — the handle every by-id callable takes back.
        'id': row.get_id(),
        # Text degrades to '' rather than None, so a client that prints it without
        # checking shows an empty label instead of the word "None".
        'title': safe_text(row['title']),
        'subject': safe_text(row['subject']),
        # A type or status the app no longer recognises falls back to a real
        # member of the set, because both drive filters and colour choices: an
        # unknown value would silently vanish from every filtered list.
        'type': safe_choice(row['type'], VALID_TYPES, 'other'),
        'due_date': iso_date(row['due_date']),
        'start_date': iso_date(row['start_date']),
        # Re-bounded on the way OUT as well as on the way in: a console edit could
        # have left 500 in a percentage column, and an out-of-range weight would
        # distort the dashboard's workload totals rather than just look odd.
        'weight': safe_number(row['weight'], minimum=MIN_WEIGHT, maximum=MAX_WEIGHT),
        'status': safe_choice(row['status'], VALID_STATUSES, STATUS_DEFAULT),
        # default=None here, not '': the editor form distinguishes "no description"
        # from "an empty one", matching how the write path stores it.
        'description': safe_text(row['description'], default=None),
        # The two simpleObject columns. safe_list drops the unusable ELEMENTS and
        # keeps the rest, so one junk entry costs that entry only. The predicates
        # are the same rules the write path enforces (1-365 for an offset, a
        # non-empty string for a row id), which stops the read rule and the write
        # rule drifting apart.
        'reminder_days': safe_list(row['reminder_days'],
                                   element_check=is_valid_reminder_day),
        'linked_note_ids': safe_list(row['linked_note_ids'],
                                     element_check=_is_text_value),
        'term_info': safe_text(row['term_info'], default=None),
        # None is a legitimate confidence (a manually-entered row), so it is the
        # fallback as well as a permitted value.
        'confidence': safe_choice(row['confidence'], VALID_CONFIDENCE, None),
        'source_text': safe_text(row['source_text'], default=None),
        'created_at': iso_timestamp(row['created_at']),
        'updated_at': iso_timestamp(row['updated_at']),
    }


def _decorate(d: dict, today: datetime.date) -> dict:
    """Attach computed display fields: days_remaining, urgency_band, due_display.

    The second half of the database-to-client boundary: _row_to_dict says what is
    STORED, this says what is SHOWN. The three fields are computed server-side on
    purpose — days_remaining is FR09, urgency_band is the colour rule of FR21, and
    due_display is the fixed 'DD MMM YYYY' of NFR08 — so every screen and the
    reminder emails agree, and nothing depends on the browser's locale or clock.

    d      an assessment dict from _row_to_dict. MUTATED IN PLACE and also
           returned, so it can be used inside a comprehension.
    today  the user's local today from _user_today, not the server's date: the
           Anvil server runs in UTC, so using its date would flip 'days_remaining'
           by one for most of a Melbourne evening.

    Adds days_remaining int|None, urgency_band str, due_display str. Never raises.
    """
    # safe_date rather than a bare fromisoformat: this runs for every card on the
    # dashboard, and a due date the app cannot read must cost that one card its
    # countdown, not the whole page.
    due_date = safe_date(d.get('due_date'))
    if due_date is not None:
        # Negative means overdue, zero means due today; _urgency_band reads the
        # same number, so the countdown and the colour can never contradict
        # each other on screen.
        days = (due_date - today).days
        d['days_remaining'] = days
        d['urgency_band'] = _urgency_band(days)
        d['due_display'] = _format_date_au(due_date)
    else:
        # No readable due date. The keys are still SET rather than left out,
        # because the client reads all three unconditionally. 'distant' is the
        # calm band and '' the empty label: a card whose date could not be read
        # should look unremarkable, not alarming.
        d['days_remaining'] = None
        d['urgency_band'] = 'distant'
        d['due_display'] = ''
    return d


# --- callables -------------------------------------------------------------

def _lookup_assessment(row_id):
    """Fetch an assessment row by id, or raise the shared "no longer exists" error.

    Every by-id callable looks a row up through here so that get, update and delete
    report a missing row identically; each caller still applies _own_or_raise itself.
    """
    try:
        row = app_tables.assessments.get_by_id(row_id)
    except Exception:
        # A malformed id (a stale link, or a client bug) makes Anvil raise rather
        # than return None. Both mean the same thing to the student.
        row = None
    if row is None:
        raise ValueError(_MISSING_ASSESSMENT_MESSAGE)
    return row


@anvil.server.callable
def create_assessment(record: dict) -> str:
    """Validate and insert an assessment owned by the current user; return its id.

    Serves both FR03 (the manual form) and FR01 (a confirmed single-line parse) —
    by this point the parser preview has already been through, so the server sees
    one record either way.

    record  the untrusted dict; see _validate_assessment_payload for the fields
            and their permitted values.

    Returns the new row's Anvil id as a string, which is what the client uses to
    reopen the record. Writes one row to `assessments`. Raises
    AuthenticationFailed if nobody is logged in, ValueError on any invalid field,
    and PermissionError if a linked note is not the caller's.
    """
    # 1. Identity first, before anything is read or written. Every callable in
    #    this module opens this way (NFR03).
    user = _require_user()
    # 2. "Today" has to be the STUDENT'S today for the horizon check to be fair:
    #    the Anvil server runs in UTC, and their settings row carries the
    #    timezone. _get_or_create_settings also creates that row on a first ever
    #    save, so a brand-new account can add its first assessment.
    today = _user_today(_get_or_create_settings(user))
    # 3. All validation happens here, in the one shared gate. Nothing below this
    #    line re-checks a field, and nothing above it has touched the table.
    payload = _validate_assessment_payload(record, user, today=today)
    # 4. The three columns the validator deliberately leaves alone. created_at and
    #    updated_at are stamped from the SERVER clock in UTC, not from anything
    #    the client sent, so the audit trail cannot be back-dated by a caller; the
    #    same instant is used for both so a never-edited row reads as unedited.
    now = datetime.datetime.now(datetime.timezone.utc)
    payload['user'] = user
    payload['created_at'] = now
    payload['updated_at'] = now
    # 5. **payload rather than a written-out column list: the validator's returned
    #    keys ARE the schema, so adding a validated field cannot leave this line
    #    behind. Only keys the validator built are present, so a client-invented
    #    key can never reach add_row().
    row = app_tables.assessments.add_row(**payload)
    # 6. The id, not the Row: no live Row leaves the server (see _row_to_dict).
    return row.get_id()


@anvil.server.callable
def create_bulk_assessments(records: list) -> dict:
    """Insert many assessments, committing every line that validates (FR02).

    Per-line, not all-or-nothing: each record is validated, the valid ones are
    inserted inside one Transaction, and the rest come back as
    {'index', 'reason'} pairs so the student can fix just those lines. This is what
    FR02 asks for — "valid lines still commit so a single bad line does not block
    the rest" — and it is why one bad paste no longer discards a whole screen of
    correctly parsed assessments.

    records  a list of assessment dicts, at most MAX_BULK_LINES (100) of them.
             Each has the same shape create_assessment takes.

    Returns {'inserted': int, 'ids': list[str], 'rejected': list[dict]}, where
    each rejection is {'index': int, 'reason': str}. 'index' is the record's
    ZERO-BASED position in the list the client sent — the client is what turns it
    into the line number FR02 asks for, because it alone knows which pasted lines
    were blank and skipped before the call.

    Writes to `assessments`; reads `notes` and `user_settings`. Raises ValueError
    if the batch itself is unusable (not a list, or too long) and PermissionError
    if any line reaches for another user's note; a line that merely fails
    validation is reported, not raised.
    """
    # 1. Identity, then the shape of the BATCH. These two raise for the whole
    #    call, unlike a bad line: a caller who sent the wrong type or 5,000 lines
    #    has a bug or is probing, and there is no per-line answer to give.
    user = _require_user()
    records = records or []
    require_list(records, 'The assessments to add')
    if len(records) > MAX_BULK_LINES:
        # The cap protects the request from timing out under NFR01 and bounds the
        # transaction below. The message quotes both numbers so the student knows
        # how much to cut rather than guessing.
        raise ValueError(
            'That is too many lines at once — add at most %d in one go '
            '(you sent %d).' % (MAX_BULK_LINES, len(records)))

    # 2. Read the settings row ONCE for the whole batch rather than per line: the
    #    horizon check needs the student's today, and 100 lookups of a row that
    #    cannot change mid-call would be 100 round-trips for one answer (NFR01).
    today = _user_today(_get_or_create_settings(user))

    # 3. Validate every line first, splitting them into two lists, and write
    #    nothing yet. Separating the pass from the write is what makes the commit
    #    step below short enough to sit inside one transaction.
    #      validated — payload dicts, in input order, ready for add_row()
    #      rejected  — {'index', 'reason'} for the lines that failed
    validated = []
    rejected = []
    for index, record in enumerate(records):
        try:
            validated.append(_validate_assessment_payload(record, user, today=today))
        except ValueError as e:
            # ValueError only: a PermissionError means the batch is reaching for
            # another user's note, which is a security condition and must abort the
            # whole call rather than be reported as a bad line.
            rejected.append({'index': index, 'reason': str(e)})

    # 4. One timestamp for the entire batch, taken before the loop: every line of
    #    a single paste was created at the same moment as far as the student is
    #    concerned, and a shared stamp keeps them sorting together by created_at.
    now = datetime.datetime.now(datetime.timezone.utc)
    ids = []
    # 5. Commit. The `if` is not just an optimisation — with nothing validated
    #    there is nothing to protect, and opening an empty transaction would be a
    #    round-trip for no work.
    if validated:
        # The transaction covers the accepted lines only: it is here so a failure
        # part-way through the inserts cannot leave half a batch behind, not to tie
        # the good lines to the bad ones.
        with tables.Transaction():
            for payload in validated:
                payload['user'] = user
                payload['created_at'] = now
                payload['updated_at'] = now
                row = app_tables.assessments.add_row(**payload)
                # Collected in input order, so the client can match ids back to
                # the lines it sent once it has skipped the rejected indexes.
                ids.append(row.get_id())
    # 6. Both halves come back in one payload. 'inserted' counts ids actually
    #    written rather than len(validated), so a partial commit could never be
    #    reported as a complete one.
    return {'inserted': len(ids), 'ids': ids, 'rejected': rejected}


@anvil.server.callable
def get_assessment(row_id: str) -> dict:
    """Return one owned assessment as a dict; raise ValueError if it is gone.

    Backs the editor form opening an existing record.

    row_id  an Anvil row id string, as handed out by _row_to_dict's 'id'.

    Returns the dict _row_to_dict builds. Reads `assessments`. Raises ValueError
    if the row is missing or the id is malformed, PermissionError if it belongs to
    somebody else.
    """
    user = _require_user()
    row = _lookup_assessment(row_id)
    # Ownership is re-checked even though the id came from a list this user was
    # already served: that list may be minutes old in a stale browser tab, and a
    # row id is only a string, so nothing about holding one proves it is yours
    # (NFR03). This is the read half of the same rule update and delete apply.
    _own_or_raise(row, user)
    return _row_to_dict(row)


@anvil.server.callable
def update_assessment(row_id: str, fields: dict) -> dict:
    """Whitelist-filter, validate and apply an edit to an owned assessment.

    Implements FR04. The client sends only the fields the student actually
    changed, so this is a patch rather than a whole record.

    row_id  an Anvil row id string.
    fields  {column: new value}. Keys outside EDITABLE_FIELDS_ASSESSMENT are
            dropped silently rather than refused — see the loop below.

    Returns the row's fresh _row_to_dict view, re-read after the write, so the
    caller redraws from what was actually stored rather than from what it sent.
    Updates `assessments`; reads `notes` and `user_settings` while validating.
    Raises ValueError if the row is gone or a field is invalid, and
    PermissionError if the row (or a newly linked note) is not the caller's.
    """
    user = _require_user()
    row = _lookup_assessment(row_id)
    # Ownership before anything is read from the row, let alone written to it.
    _own_or_raise(row, user)

    fields = fields if isinstance(fields, dict) else {}
    # The row as it stands, so a cross-field check can compare the edited date
    # against the date already stored.
    existing = _row_to_dict(row)
    # The horizon check only applies to a due date being set now, so the settings
    # row is only read when that is what is being edited.
    today = _user_today(_get_or_create_settings(user)) if 'due_date' in fields else None

    # The whitelist (EC-SEC-03). A key not in EDITABLE_FIELDS_ASSESSMENT is
    # dropped, not refused: the client only ever sends editable keys, so anything
    # else is a bug or a probe, and neither deserves a message describing the
    # schema. Four columns are excluded on purpose —
    #   user        re-assigning it would hand the row to another account;
    #   created_at  is the audit stamp of when the record was made;
    #   confidence and source_text are the PARSER'S audit trail (FR17), and it
    #               has to survive an edit: the point of storing "this was parsed
    #               with LOW confidence from 'methods sac friday'" is lost if
    #               correcting the date can quietly rewrite or erase it.
    # `clean` accumulates only validated values, so the row.update() below can
    # never see anything this loop did not approve.
    clean = {}
    for key, value in fields.items():
        if key in EDITABLE_FIELDS_ASSESSMENT:
            clean[key] = _validate_field(key, value, user, existing=existing, today=today)
    # Every field is validated BEFORE any is written, so an edit that fails on its
    # third field leaves the first two unwritten instead of half-applying.
    if clean:
        # updated_at is stamped only when something is genuinely being written —
        # a call that changed nothing must not make the row look edited.
        clean['updated_at'] = datetime.datetime.now(datetime.timezone.utc)
        row.update(**clean)
    # Re-read rather than returning `clean`: the client redraws from what the
    # database now holds, including the fields it did not touch and the rounding
    # the validator applied.
    return _row_to_dict(row)


@anvil.server.callable
def delete_assessment(row_id: str) -> bool:
    """Delete an owned assessment. Reminder logs are retained for audit (Decision 3).

    Implements FR05. The confirmation prompt FR05 also asks for lives on the
    client, because a server callable has nobody to ask.

    row_id  an Anvil row id string.

    Returns True on success; a row that is already gone raises ValueError, the
    same way get_assessment and update_assessment report it. Deletes from
    `assessments` only — `reminder_logs` rows are NOT cascade-deleted. They keep
    the assessment_id of a row that no longer exists, on purpose, so the record of
    what was emailed survives the assessment it was about (spec Decision 3 (C)).
    Raises PermissionError if the row is not the caller's.
    """
    user = _require_user()
    row = _lookup_assessment(row_id)
    # The last check before an irreversible write. Ownership is asked here and not
    # inferred from the fact that the client displayed the row (NFR03).
    _own_or_raise(row, user)
    row.delete()
    # A plain True rather than the deleted dict: the row is gone, so there is
    # nothing honest left to return, and the client only needs to know it worked.
    return True


@anvil.server.callable
def list_assessments(filters: dict = None, sort: dict = None) -> list:
    """Return the current user's assessments as dicts, filtered/sorted, decorated.

    Implements FR06 (filter) and FR07 (sort). The thin callable wrapper exists so
    dashboard.get_dashboard_data can reach the same logic through
    _list_assessments_impl without paying for a second server round-trip (NFR01).

    filters (all optional): subjects[list of canonical names], types[list from
    VALID_TYPES], statuses[list from VALID_STATUSES], show_completed[bool],
    month['YYYY-MM']. sort: {'by': one of ALLOWED_SORT_KEYS, 'direction':
    'asc'|'desc'}.

    Returns a list of decorated assessment dicts (see _row_to_dict and _decorate),
    scoped to the caller. Reads `assessments` and `user_settings`.
    """
    user = _require_user()
    # Fetched here rather than inside the implementation because the dashboard
    # already holds this row and passes its own, avoiding a duplicate lookup.
    settings_row = _get_or_create_settings(user)
    return _list_assessments_impl(user, settings_row, filters, sort)


def _list_assessments_impl(user, settings_row, filters: dict = None, sort: dict = None) -> list:
    """Shared core of list_assessments; reused by dashboard.get_dashboard_data
    without a nested @anvil.server.callable round-trip (NFR01).

    Filters are read with safe_* guards rather than require_*: a filter is a view
    preference, so an unusable value should quietly not narrow the list. Refusing to
    show a student their assessments because a saved filter has gone stale would be
    a worse answer than showing them all of it.
    """
    filters = filters if isinstance(filters, dict) else {}
    sort = sort if isinstance(sort, dict) else {}

    sort_by = sort.get('by')
    if sort_by not in ALLOWED_SORT_KEYS:
        sort_by = 'due_date'
    ascending = sort.get('direction', 'asc') != 'desc'

    query = {'user': user}

    subjects = safe_list(filters.get('subjects'), element_check=_is_text_value)
    if subjects:
        query['subject'] = q.any_of(*subjects)

    types = [t for t in safe_list(filters.get('types'), element_check=_is_text_value)
             if t in VALID_TYPES]
    if types:
        query['type'] = q.any_of(*types)

    statuses = [s for s in safe_list(filters.get('statuses'), element_check=_is_text_value)
                if s in VALID_STATUSES]
    if statuses:
        query['status'] = q.any_of(*statuses)
    elif not safe_bool(filters.get('show_completed'), default=False):
        # Default: hide completed via a positive any_of (avoids not_-query edge
        # cases). Derived from VALID_STATUSES so adding a status cannot leave this
        # line behind.
        query['status'] = q.any_of(*sorted(VALID_STATUSES - {STATUS_COMPLETED}))

    month = filters.get('month')
    if month:
        first, last = _get_month_bounds(month)
        if first is not None:
            query['due_date'] = q.between(first, last, min_inclusive=True, max_inclusive=True)

    rows = app_tables.assessments.search(tables.order_by(sort_by, ascending=ascending), **query)

    today = _user_today(settings_row)
    return [_decorate(_row_to_dict(r), today) for r in rows]


def _get_month_bounds(month_str):
    """'YYYY-MM' -> (first_date, last_date) or (None, None)."""
    try:
        year, month = month_str.split('-')
        year, month = int(year), int(month)
        first = datetime.date(year, month, 1)
        if month == 12:
            last = datetime.date(year, 12, 31)
        else:
            last = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)
        return first, last
    except (ValueError, TypeError, AttributeError):
        return None, None


# --- export / import (FR18, FR19) ------------------------------------------

@anvil.server.callable
def export_user_data():
    """Return all of the current user's data as a downloadable JSON blob (FR18)."""
    user = _require_user()
    settings = _get_or_create_settings(user)
    assessments = [_row_to_dict(r) for r in app_tables.assessments.search(user=user)]
    notes = [_note_row_to_dict(r) for r in app_tables.notes.search(user=user)]
    payload = {
        'version': 1,
        'exported_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'assessments': assessments,   # reminder_logs deliberately excluded (FR18)
        'notes': notes,
        'settings': _settings_row_to_dict(settings),
    }
    blob = json.dumps(payload, indent=2).encode('utf-8')
    name = 'dotpoint-export-%s.json' % _user_today(settings).strftime('%Y-%m-%d')
    return anvil.BlobMedia('application/json', blob, name=name)


def _validate_import_payload(data: dict):
    """Validate a decoded export dict up-front (before any write). Returns
    (validated_notes, validated_assessments) or raises ValueError."""
    if (not isinstance(data, dict) or data.get('version') != 1
            or not isinstance(data.get('assessments'), list)
            or not isinstance(data.get('notes'), list)
            or not isinstance(data.get('settings'), dict)):
        raise ValueError(
            'That file is not a DotPoint export. Choose the .json file you '
            'downloaded from Export.')

    validated_notes = []
    for i, n in enumerate(data['notes']):
        try:
            clean = _validate_note_fields({
                'title': n.get('title'), 'content': n.get('content') or '',
                'tags': n.get('tags') or [],
                'is_pinned': bool(n.get('is_pinned', False)),
            })
        except ValueError as e:
            raise ValueError('Note %d in that file could not be imported. %s' % (i + 1, e))
        validated_notes.append((n.get('id'), clean))

    validated_assessments = []
    # Two sentinels, both meaning "this check cannot be made here":
    #  - deferred_link_owner is the user passed to the validator. The note links in
    #    the file point at the OLD note ids, which do not exist yet; they are
    #    emptied below and remapped after the notes are inserted, so there is no
    #    owner to check them against.
    #  - today stays None, which switches the due-date horizon check off. An export
    #    is legitimately old — refusing to restore a student's own backup because
    #    its dates are behind us would defeat the point of having one.
    deferred_link_owner = None
    horizon_today = None
    for i, a in enumerate(data['assessments']):
        rec = dict(a)
        old_links = rec.get('linked_note_ids') or []
        rec['linked_note_ids'] = []
        try:
            payload = _validate_assessment_payload(
                rec, deferred_link_owner, today=horizon_today)
        except ValueError as e:
            raise ValueError(
                'Assessment %d in that file could not be imported. %s' % (i + 1, e))
        validated_assessments.append((payload, old_links))

    return validated_notes, validated_assessments


@anvil.server.callable
def import_user_data(blob) -> dict:
    """Import a previously-exported JSON blob (FR19).

    Everything is validated first; a malformed file or any invalid row rejects
    the whole import (ValueError) with nothing written. Valid data is inserted
    inside one Transaction: notes first (building an old-id -> new-id map), then
    assessments with their linked_note_ids remapped. Title collisions for the
    user are suffixed with the import timestamp. Settings are applied best-effort.
    """
    user = _require_user()
    try:
        raw = blob.get_bytes().decode('utf-8')
        data = json.loads(raw)
    except Exception:
        raise ValueError(
            'That file could not be read. Choose the .json file you downloaded '
            'from Export.')

    validated_notes, validated_assessments = _validate_import_payload(data)

    now = datetime.datetime.now(datetime.timezone.utc)
    stamp = now.strftime('%Y-%m-%d %H:%M')
    id_map = {}
    renamed = []

    with tables.Transaction():
        for old_id, clean in validated_notes:
            row = app_tables.notes.add_row(
                title=clean['title'], content=clean['content'], tags=clean['tags'],
                is_pinned=clean['is_pinned'], user=user, created_at=now, updated_at=now)
            if old_id:
                id_map[old_id] = row.get_id()

        for payload, old_links in validated_assessments:
            existing = app_tables.assessments.search(user=user, title=payload['title'])
            if any(True for _ in existing):
                payload['title'] = '%s (imported %s)' % (payload['title'], stamp)
                renamed.append(payload['title'])
            payload['linked_note_ids'] = [id_map[o] for o in old_links if o in id_map]
            payload['user'] = user
            payload['created_at'] = now
            payload['updated_at'] = now
            app_tables.assessments.add_row(**payload)

    try:
        _apply_settings(data['settings'])   # whitelisted + validated inside notes
    except Exception:
        pass

    return {
        'notes_inserted': len(validated_notes),
        'assessments_inserted': len(validated_assessments),
        'renamed': renamed,
    }
