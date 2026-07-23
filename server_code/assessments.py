import anvil.secrets
import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""Assessment CRUD server module (FR03, FR04, FR05, FR06, FR07, FR09).

Slice: create/get/update/delete/list implemented here (spec section 10 step 2).
create_bulk_assessments, export_user_data and import_user_data land in later
slices (steps 5 and 9).

Every callable resolves the user via _require_user() first; every update/delete
re-checks ownership with _own_or_raise. No live Row ever leaves the server —
results go through _row_to_dict.

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
)
from .notes import (
    _get_or_create_settings, _settings_row_to_dict, _note_row_to_dict,
    _validate_note_fields, update_settings as _apply_settings,
)

_VALID_TYPES = {'sac', 'sat', 'exam', 'project', 'homework', 'other'}
_VALID_STATUSES = {'not_started', 'in_progress', 'completed'}
_VALID_CONFIDENCE = {'HIGH', 'MEDIUM', 'LOW'}


# --- coercion / validation -------------------------------------------------

def _coerce_date(value):
    """Accept a date or 'YYYY-MM-DD' string; return a date. Raise ValueError."""
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        try:
            return datetime.date.fromisoformat(value)
        except (ValueError, TypeError):
            pass
    raise ValueError("invalid date: %r" % (value,))


def _validate_assessment_payload(record: dict, user) -> dict:
    """Validate a create payload; return the persistable field dict or raise.

    Shared by create_assessment (and, later, create_bulk_assessments /
    import_user_data). Does not set user/created_at/updated_at.
    """
    record = record or {}
    out = {}

    title = record.get('title')
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title required")
    if len(title) > 200:
        raise ValueError("title too long (max 200)")
    out['title'] = title.strip()

    subject = record.get('subject')
    if subject not in set(SUBJECT_ALIASES.values()):
        raise ValueError("invalid subject")
    out['subject'] = subject

    a_type = record.get('type')
    if a_type not in _VALID_TYPES:
        raise ValueError("invalid type")
    out['type'] = a_type

    out['due_date'] = _coerce_date(record.get('due_date'))

    start_date = record.get('start_date')
    out['start_date'] = _coerce_date(start_date) if start_date else None

    weight = record.get('weight')
    if weight is None or weight == '':
        out['weight'] = None
    else:
        try:
            weight = float(weight)
        except (ValueError, TypeError):
            raise ValueError("weight must be a number")
        if not (0.0 <= weight <= 100.0):
            raise ValueError("weight must be between 0 and 100")
        out['weight'] = weight

    status = record.get('status') or 'not_started'
    if status not in _VALID_STATUSES:
        raise ValueError("invalid status")
    out['status'] = status

    desc = record.get('description')
    if desc is not None and not isinstance(desc, str):
        raise ValueError("description must be text")
    out['description'] = desc

    reminder_days = record.get('reminder_days')
    if reminder_days is None:
        reminder_days = [7, 2]
    if not isinstance(reminder_days, list) or not all(
        isinstance(d, int) and not isinstance(d, bool) and d > 0 for d in reminder_days
    ):
        raise ValueError("reminder_days must be a list of positive ints")
    out['reminder_days'] = reminder_days

    linked = record.get('linked_note_ids') or []
    if not isinstance(linked, list) or not all(isinstance(n, str) for n in linked):
        raise ValueError("invalid linked_note_ids")
    for nid in linked:
        note = app_tables.notes.get_by_id(nid)
        if note is None:
            raise ValueError("invalid linked_note_ids")
        _own_or_raise(note, user)
    out['linked_note_ids'] = linked

    confidence = record.get('confidence')
    if confidence is not None and confidence not in _VALID_CONFIDENCE:
        raise ValueError("invalid confidence")
    out['confidence'] = confidence

    source_text = record.get('source_text')
    if source_text is not None and not isinstance(source_text, str):
        raise ValueError("source_text must be text")
    out['source_text'] = source_text

    term_info = record.get('term_info')
    if term_info is not None and not isinstance(term_info, str):
        raise ValueError("term_info must be text")
    out['term_info'] = term_info

    return out


def _validate_field(key, value, user):
    """Validate a single editable field for update_assessment (reuses payload rules)."""
    stub = {
        'title': 'x', 'subject': next(iter(SUBJECT_ALIASES.values())),
        'type': 'other', 'due_date': datetime.date.today(),
    }
    stub[key] = value
    if key == 'due_date':
        return _coerce_date(value)
    validated = _validate_assessment_payload(stub, user)
    return validated[key]


# --- serialisation ---------------------------------------------------------

def _row_to_dict(row) -> dict:
    """Plain-dict view of an assessment row; dates as ISO strings, incl. 'id'."""
    def iso(d):
        return d.isoformat() if d is not None else None
    return {
        'id': row.get_id(),
        'title': row['title'],
        'subject': row['subject'],
        'type': row['type'],
        'due_date': iso(row['due_date']),
        'start_date': iso(row['start_date']),
        'weight': row['weight'],
        'status': row['status'],
        'description': row['description'],
        'reminder_days': row['reminder_days'] or [],
        'linked_note_ids': row['linked_note_ids'] or [],
        'term_info': row['term_info'],
        'confidence': row['confidence'],
        'source_text': row['source_text'],
        'created_at': iso(row['created_at']),
        'updated_at': iso(row['updated_at']),
    }


def _decorate(d: dict, today: datetime.date) -> dict:
    """Attach computed display fields: days_remaining, urgency_band, due_display."""
    due = d.get('due_date')
    if due:
        due_date = datetime.date.fromisoformat(due)
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

@anvil.server.callable
def create_assessment(record: dict) -> str:
    """Validate and insert an assessment owned by the current user; return its id."""
    user = _require_user()
    payload = _validate_assessment_payload(record, user)
    now = datetime.datetime.now(datetime.timezone.utc)
    payload['user'] = user
    payload['created_at'] = now
    payload['updated_at'] = now
    row = app_tables.assessments.add_row(**payload)
    return row.get_id()


@anvil.server.callable
def create_bulk_assessments(records: list) -> dict:
    """Insert many assessments atomically (FR02).

    All-or-nothing: every record is validated first; if any fails, nothing is
    inserted and {'inserted': 0, 'rejected': [{'index', 'reason'}, ...]} is
    returned. If all pass, they are inserted inside one Transaction and
    {'inserted': N, 'ids': [...]} is returned. The caller (bulk UI) is expected
    to have pre-filtered LOW-confidence / incomplete parses.
    """
    user = _require_user()
    records = records or []

    validated = []
    rejected = []
    for i, rec in enumerate(records):
        try:
            validated.append(_validate_assessment_payload(rec, user))
        except ValueError as e:
            rejected.append({'index': i, 'reason': str(e)})
    if rejected:
        return {'inserted': 0, 'rejected': rejected}

    now = datetime.datetime.now(datetime.timezone.utc)
    ids = []
    with tables.Transaction():
        for payload in validated:
            payload['user'] = user
            payload['created_at'] = now
            payload['updated_at'] = now
            row = app_tables.assessments.add_row(**payload)
            ids.append(row.get_id())
    return {'inserted': len(ids), 'ids': ids}


@anvil.server.callable
def get_assessment(row_id: str) -> dict:
    """Return one owned assessment as a dict, or raise ValueError('not found')."""
    user = _require_user()
    row = app_tables.assessments.get_by_id(row_id)
    if row is None:
        raise ValueError("not found")
    _own_or_raise(row, user)
    return _row_to_dict(row)


@anvil.server.callable
def update_assessment(row_id: str, fields: dict) -> dict:
    """Whitelist-filter, validate and apply an edit to an owned assessment."""
    user = _require_user()
    row = app_tables.assessments.get_by_id(row_id)
    if row is None:
        raise ValueError("not found")
    _own_or_raise(row, user)

    clean = {}
    for k, v in (fields or {}).items():
        if k in EDITABLE_FIELDS_ASSESSMENT:
            clean[k] = _validate_field(k, v, user)
    if clean:
        clean['updated_at'] = datetime.datetime.now(datetime.timezone.utc)
        row.update(**clean)
    return _row_to_dict(row)


@anvil.server.callable
def delete_assessment(row_id: str) -> bool:
    """Delete an owned assessment. Reminder logs are retained for audit (Decision 3)."""
    user = _require_user()
    row = app_tables.assessments.get_by_id(row_id)
    if row is None:
        return False
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
    without a nested @anvil.server.callable round-trip (NFR01)."""
    filters = filters or {}
    sort = sort or {}

    sort_by = sort.get('by')
    if sort_by not in ALLOWED_SORT_KEYS:
        sort_by = 'due_date'
    ascending = sort.get('direction', 'asc') != 'desc'

    query = {'user': user}

    subjects = filters.get('subjects')
    if subjects:
        query['subject'] = q.any_of(*subjects)

    types = filters.get('types')
    if types:
        query['type'] = q.any_of(*types)

    statuses = filters.get('statuses')
    if statuses:
        query['status'] = q.any_of(*statuses)
    elif not filters.get('show_completed', False):
        # Default: hide completed via a positive any_of (avoids not_-query edge cases).
        query['status'] = q.any_of('not_started', 'in_progress')

    month = filters.get('month')
    if month:
        first, last = _month_bounds(month)
        if first is not None:
            query['due_date'] = q.between(first, last, min_inclusive=True, max_inclusive=True)

    rows = app_tables.assessments.search(tables.order_by(sort_by, ascending=ascending), **query)

    today = _user_today(settings_row)
    return [_decorate(_row_to_dict(r), today) for r in rows]


def _month_bounds(month_str):
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
        raise ValueError("invalid export format")

    validated_notes = []
    for i, n in enumerate(data['notes']):
        try:
            clean = _validate_note_fields({
                'title': n.get('title'), 'content': n.get('content') or '',
                'tags': n.get('tags') or [],
                'is_pinned': bool(n.get('is_pinned', False)),
            })
        except ValueError as e:
            raise ValueError("note[%d]: %s" % (i, e))
        validated_notes.append((n.get('id'), clean))

    validated_assessments = []
    return_user = None  # validation of linked_note_ids is deferred (remapped later)
    for i, a in enumerate(data['assessments']):
        rec = dict(a)
        old_links = rec.get('linked_note_ids') or []
        rec['linked_note_ids'] = []
        try:
            payload = _validate_assessment_payload(rec, return_user)
        except ValueError as e:
            raise ValueError("assessment[%d]: %s" % (i, e))
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
        raise ValueError("invalid export format")

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
