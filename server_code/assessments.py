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
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('%s must be text.' % field_label)
    return value.strip()[:MAX_SOURCE_TEXT_LENGTH] or None


def _validate_assessment_payload(record: dict, user, today=None) -> dict:
    """Validate a whole assessment record; return the persistable fields or raise.

    The single write-side gate for this module (see the module docstring). Does not
    set user/created_at/updated_at.

    `today` is the user's local today and switches on the due-date horizon check.
    It is None on the import path, where that check deliberately does not apply —
    see _validate_import_payload.
    """
    record = record if isinstance(record, dict) else {}
    validated_fields = {}

    validated_fields['title'] = require_text(
        record.get('title'), 'Title', MAX_TITLE_LENGTH)

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

    validated_fields['type'] = require_choice(
        record.get('type'), VALID_TYPES, 'Type')

    validated_fields['due_date'] = require_date(record.get('due_date'), 'Due date')

    start_date = record.get('start_date')
    validated_fields['start_date'] = (
        require_date(start_date, 'Start date') if start_date else None)

    weight = record.get('weight')
    if weight is None or weight == '':
        # Weight is optional: not every assessment carries a published percentage.
        validated_fields['weight'] = None
    else:
        # Rounded only after the range check, so the stored value and the value the
        # student sees can never disagree.
        validated_fields['weight'] = round_percentage(
            require_number_in_range(weight, 'Weight (%)', MIN_WEIGHT, MAX_WEIGHT))

    validated_fields['status'] = require_choice(
        record.get('status') or STATUS_DEFAULT, VALID_STATUSES, 'Status')

    description = require_text(
        record.get('description'), 'Description', MAX_DESCRIPTION_LENGTH,
        allow_blank=True)
    # Stored as None rather than '' so an empty cell keeps meaning "no description"
    # instead of "a description that happens to be empty".
    validated_fields['description'] = description or None

    reminder_days = record.get('reminder_days')
    if reminder_days is None:
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

    confidence = record.get('confidence')
    validated_fields['confidence'] = (
        require_choice(confidence, VALID_CONFIDENCE, 'Confidence')
        if confidence is not None else None)

    validated_fields['source_text'] = _trim_parser_text(
        record.get('source_text'), 'Source text')
    validated_fields['term_info'] = _trim_parser_text(
        record.get('term_info'), 'Term information')

    # --- whole-record checks: reasonableness and completeness ---------------
    # Everything above judges ONE field against its own rule. The checks below can
    # only run once every field is individually valid, because each of them reads
    # more than one field (or compares a field against the world). That is what
    # makes this a record-level layer rather than three more field checks.

    # Completeness: the dict about to be persisted must still carry the four fields
    # an assessment cannot exist without. Each was checked individually above, so
    # this is the closing gate rather than the first line of defence — it is what
    # would catch a later edit that adds a branch leaving one of them unset, before
    # a half-built row reaches add_row().
    require_complete_record(
        validated_fields,
        [('title', 'Title'), ('subject', 'Subject'), ('type', 'Type'),
         ('due_date', 'Due date')],
        'This assessment')

    # Reasonableness: work cannot start after it is due. Both dates are valid on
    # their own; only the pair is wrong.
    require_not_after(
        validated_fields['start_date'], validated_fields['due_date'],
        'Start date', 'Due date')

    # Reasonableness: a due date years from today is a mistyped year, not a plan.
    # `today` is None on the import path, where require_within_horizon does nothing
    # so that a genuine old export still restores.
    require_within_horizon(validated_fields['due_date'], today, 'Due date')

    return validated_fields


def _validate_field(key, value, user, existing=None, today=None):
    """Validate a single editable field for update_assessment (reuses payload rules).

    Builds the smallest record that will pass the shared validator, overwrites the
    one field being edited, and returns just that field back. `existing` is the row
    as it stands (from _row_to_dict) so a cross-field check has the row's real dates
    to compare against instead of a placeholder.
    """
    existing = existing or {}
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

    Every cell passes through a safe_* read-guard rather than being trusted. A row
    can predate a rule or have been edited in the Anvil Data Tables console, and
    this dict feeds the dashboard, the export file and the reminder emails: one
    damaged cell has to degrade to a documented default, not take a screen down.
    """
    def iso_date(value):
        """A stored date as 'YYYY-MM-DD', or None if the cell holds anything else."""
        date_value = safe_date(value)
        return date_value.isoformat() if date_value is not None else None

    def iso_timestamp(value):
        """A stored timestamp in full ISO form; the time component is kept."""
        if isinstance(value, (datetime.datetime, datetime.date)):
            return value.isoformat()
        return None

    return {
        'id': row.get_id(),
        'title': safe_text(row['title']),
        'subject': safe_text(row['subject']),
        'type': safe_choice(row['type'], VALID_TYPES, 'other'),
        'due_date': iso_date(row['due_date']),
        'start_date': iso_date(row['start_date']),
        'weight': safe_number(row['weight'], minimum=MIN_WEIGHT, maximum=MAX_WEIGHT),
        'status': safe_choice(row['status'], VALID_STATUSES, STATUS_DEFAULT),
        'description': safe_text(row['description'], default=None),
        'reminder_days': safe_list(row['reminder_days'],
                                   element_check=is_valid_reminder_day),
        'linked_note_ids': safe_list(row['linked_note_ids'],
                                     element_check=_is_text_value),
        'term_info': safe_text(row['term_info'], default=None),
        'confidence': safe_choice(row['confidence'], VALID_CONFIDENCE, None),
        'source_text': safe_text(row['source_text'], default=None),
        'created_at': iso_timestamp(row['created_at']),
        'updated_at': iso_timestamp(row['updated_at']),
    }


def _decorate(d: dict, today: datetime.date) -> dict:
    """Attach computed display fields: days_remaining, urgency_band, due_display."""
    # safe_date rather than a bare fromisoformat: this runs for every card on the
    # dashboard, and a due date the app cannot read must cost that one card its
    # countdown, not the whole page.
    due_date = safe_date(d.get('due_date'))
    if due_date is not None:
        days = (due_date - today).days
        d['days_remaining'] = days
        d['urgency_band'] = _urgency_band(days)
        d['due_display'] = _format_date_au(due_date)
    else:
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
    """Validate and insert an assessment owned by the current user; return its id."""
    user = _require_user()
    today = _user_today(_get_or_create_settings(user))
    payload = _validate_assessment_payload(record, user, today=today)
    now = datetime.datetime.now(datetime.timezone.utc)
    payload['user'] = user
    payload['created_at'] = now
    payload['updated_at'] = now
    row = app_tables.assessments.add_row(**payload)
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

    'index' is the record's position in the list the client sent, which is what the
    client turns into a line number.
    """
    user = _require_user()
    records = records or []
    require_list(records, 'The assessments to add')
    if len(records) > MAX_BULK_LINES:
        raise ValueError(
            'That is too many lines at once — add at most %d in one go '
            '(you sent %d).' % (MAX_BULK_LINES, len(records)))

    today = _user_today(_get_or_create_settings(user))

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

    now = datetime.datetime.now(datetime.timezone.utc)
    ids = []
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
                ids.append(row.get_id())
    return {'inserted': len(ids), 'ids': ids, 'rejected': rejected}


@anvil.server.callable
def get_assessment(row_id: str) -> dict:
    """Return one owned assessment as a dict; raise ValueError if it is gone."""
    user = _require_user()
    row = _lookup_assessment(row_id)
    _own_or_raise(row, user)
    return _row_to_dict(row)


@anvil.server.callable
def update_assessment(row_id: str, fields: dict) -> dict:
    """Whitelist-filter, validate and apply an edit to an owned assessment."""
    user = _require_user()
    row = _lookup_assessment(row_id)
    _own_or_raise(row, user)

    fields = fields if isinstance(fields, dict) else {}
    # The row as it stands, so a cross-field check can compare the edited date
    # against the date already stored.
    existing = _row_to_dict(row)
    # The horizon check only applies to a due date being set now, so the settings
    # row is only read when that is what is being edited.
    today = _user_today(_get_or_create_settings(user)) if 'due_date' in fields else None

    clean = {}
    for key, value in fields.items():
        if key in EDITABLE_FIELDS_ASSESSMENT:
            clean[key] = _validate_field(key, value, user, existing=existing, today=today)
    if clean:
        clean['updated_at'] = datetime.datetime.now(datetime.timezone.utc)
        row.update(**clean)
    return _row_to_dict(row)


@anvil.server.callable
def delete_assessment(row_id: str) -> bool:
    """Delete an owned assessment. Reminder logs are retained for audit (Decision 3).

    Returns True on success; a row that is already gone raises, the same way
    get_assessment and update_assessment report it.
    """
    user = _require_user()
    row = _lookup_assessment(row_id)
    _own_or_raise(row, user)
    row.delete()
    return True


@anvil.server.callable
def list_assessments(filters: dict = None, sort: dict = None) -> list:
    """Return the current user's assessments as dicts, filtered/sorted, decorated.

    filters (all optional): subjects[list], types[list], statuses[list],
    show_completed[bool], month['YYYY-MM']. sort: {'by','direction'}.
    """
    user = _require_user()
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
