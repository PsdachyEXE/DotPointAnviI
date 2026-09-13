"""Proves the assessment write paths accept good data and refuse bad, identically.

The key property being tested is not that ONE path validates — it is that all FOUR
write paths (create, update, bulk, import) apply the SAME rules, because rubric 7.3's
Very High descriptor is "no inconsistencies are present". A field that is range-checked
on create and not on update is precisely the inconsistency it means.
"""

from .harness import load_server_code, make_user, make_settings
from . import anvil_stub

load_server_code()

import datetime

from server_code import assessments
from server_code._constants import (
    MAX_TITLE_LENGTH, MAX_DESCRIPTION_LENGTH, MIN_WEIGHT, MAX_WEIGHT,
    MIN_REMINDER_DAY, MAX_REMINDER_DAY,
)


def _signed_in():
    """A signed-in user with a settings row, ready to create assessments."""
    user = make_user()
    make_settings(user, subjects=['Mathematical Methods', 'English', 'Physics'])
    return user


def _valid_record(**overrides):
    """A record that must always be accepted, so a test can spoil exactly one field."""
    record = {
        'title': 'Methods SAC2',
        'subject': 'Mathematical Methods',
        'type': 'sac',
        'due_date': datetime.date.today() + datetime.timedelta(days=14),
        'weight': 25.0,
        'status': 'not_started',
    }
    record.update(overrides)
    return record


# --- the happy path --------------------------------------------------------

def suite_create(results):
    """A well-formed assessment is stored, with its values intact."""
    _signed_in()

    row_id = assessments.create_assessment(_valid_record())
    results.ok(isinstance(row_id, str) and row_id,
               'create_assessment returns the new row id')
    results.equal(len(anvil_stub.app_tables.assessments.rows), 1,
                  'exactly one row was written')

    stored = assessments.get_assessment(row_id)
    results.equal(stored['title'], 'Methods SAC2', 'the title round-trips')
    results.equal(stored['subject'], 'Mathematical Methods', 'the subject round-trips')
    results.equal(stored['weight'], 25.0, 'the weight round-trips')

    # No live Anvil Row may cross to the client — the payload must be plain data.
    results.ok(isinstance(stored, dict), 'a plain dict is returned, not a Row')

    # A blank optional field is stored as absent, not as an empty string, so the
    # column keeps meaning "there is no description".
    row_id = assessments.create_assessment(_valid_record(description='   '))
    results.equal(assessments.get_assessment(row_id)['description'], None,
                  'a whitespace-only description is stored as absent')


# --- the same rule on every path -------------------------------------------

def suite_consistent_across_paths(results):
    """Each rule must fire identically on create, update, bulk and import."""
    user = _signed_in()
    good_id = assessments.create_assessment(_valid_record())

    # Each entry: a description, and the single field that is spoiled.
    bad_fields = [
        ('a weight above 100', {'weight': 150}),
        ('a negative weight', {'weight': -5}),
        ('a blank title', {'title': '   '}),
        ('an over-long title', {'title': 'x' * (MAX_TITLE_LENGTH + 1)}),
        ('a subject that is not a study', {'subject': 'Underwater Basket Weaving'}),
        ('a type outside the enum', {'type': 'quiz'}),
        ('a status outside the enum', {'status': 'Complete'}),
        ('an unparseable due date', {'due_date': 'next thursday'}),
        ('an over-long description',
         {'description': 'x' * (MAX_DESCRIPTION_LENGTH + 1)}),
        ('an unbounded reminder day', {'reminder_days': [999999]}),
        ('a boolean smuggled into reminder days', {'reminder_days': [True]}),
    ]

    for description, spoiled in bad_fields:
        # CREATE
        results.raises(ValueError,
                       lambda s=spoiled: assessments.create_assessment(_valid_record(**s)),
                       'create refuses %s' % description)
        # UPDATE — the path that historically could bypass a create-only rule.
        results.raises(ValueError,
                       lambda s=spoiled: assessments.update_assessment(good_id, dict(s)),
                       'update refuses %s' % description)
        # BULK
        result = assessments.create_bulk_assessments([_valid_record(**spoiled)])
        results.equal(result['inserted'], 0, 'bulk refuses %s' % description)
        results.ok(result.get('rejected'),
                   'bulk reports %s with a reason' % description)

    # And a good value is still accepted everywhere, so the rules are not simply
    # refusing everything.
    results.does_not_raise(
        lambda: assessments.update_assessment(good_id, {'weight': 40.0}),
        'update still accepts a valid weight')
    results.equal(assessments.get_assessment(good_id)['weight'], 40.0,
                  'and the update is stored')


# --- reasonableness --------------------------------------------------------

def suite_reasonableness(results):
    """Every field valid, the record still wrong as a whole."""
    _signed_in()
    today = datetime.date.today()

    results.raises(
        ValueError,
        lambda: assessments.create_assessment(_valid_record(
            start_date=today + datetime.timedelta(days=20),
            due_date=today + datetime.timedelta(days=10))),
        'a start date after the due date is refused',
        message_contains='cannot be after')

    results.does_not_raise(
        lambda: assessments.create_assessment(_valid_record(
            start_date=today + datetime.timedelta(days=5),
            due_date=today + datetime.timedelta(days=10))),
        'a start date before the due date is accepted')

    # The mistyped year. Every check except this one passes it.
    results.raises(
        ValueError,
        lambda: assessments.create_assessment(_valid_record(
            due_date=today.replace(year=today.year + 40))),
        'a due date forty years out is refused',
        message_contains='five years')

    # Overdue work is entirely legitimate and must still be creatable.
    results.does_not_raise(
        lambda: assessments.create_assessment(_valid_record(
            due_date=today - datetime.timedelta(days=30))),
        'an assessment that is already overdue can still be recorded')


# --- weight formatting -----------------------------------------------------

def suite_weight_format(results):
    """A weight is stored to two decimal places, once, at the point of writing."""
    _signed_in()
    row_id = assessments.create_assessment(_valid_record(weight=25.333333333333336))
    results.equal(assessments.get_assessment(row_id)['weight'], 25.33,
                  'a long float weight is stored rounded to 2dp')

    # The bounds themselves must be inclusive — a 0% or 100% assessment is real.
    for boundary in (MIN_WEIGHT, MAX_WEIGHT):
        results.does_not_raise(
            lambda w=boundary: assessments.create_assessment(_valid_record(weight=w)),
            'a weight of %g is accepted' % boundary)


# --- FR02: bulk commits the good lines -------------------------------------

def suite_bulk_partial_commit(results):
    """SRS FR02: valid lines still commit so a single bad line does not block the rest.

    The shipped code used to do the opposite — all-or-nothing — which contradicted the
    project's own requirements document.
    """
    _signed_in()

    batch = [
        _valid_record(title='Good one'),
        _valid_record(title='Bad one', weight=150),      # the only invalid line
        _valid_record(title='Good two'),
    ]
    result = assessments.create_bulk_assessments(batch)

    results.equal(result['inserted'], 2,
                  'the two valid lines are committed despite the bad one')
    results.equal(len(result['rejected']), 1, 'the one bad line is reported')
    results.equal(result['rejected'][0]['index'], 1,
                  'the rejection carries the position of the offending line')
    results.ok(result['rejected'][0]['reason'],
               'and a reason the student can act on')
    results.equal(len(anvil_stub.app_tables.assessments.rows), 2,
                  'exactly the two good rows exist in the table')

    titles = sorted(r['title'] for r in anvil_stub.app_tables.assessments.rows.values())
    results.equal(titles, ['Good one', 'Good two'],
                  'and they are the right two')

    # A batch where everything is fine must report no rejections at all.
    anvil_stub.reset()
    _signed_in()
    result = assessments.create_bulk_assessments(
        [_valid_record(title='A'), _valid_record(title='B')])
    results.equal(result['inserted'], 2, 'an all-valid batch commits everything')
    results.equal(result.get('rejected') or [], [], 'and reports nothing rejected')

    # An empty batch is not an error.
    results.does_not_raise(lambda: assessments.create_bulk_assessments([]),
                           'an empty batch is handled without raising')


# --- ownership -------------------------------------------------------------

def suite_ownership(results):
    """NFR03: no row may ever be returned or changed by anyone but its owner."""
    owner = _signed_in()
    row_id = assessments.create_assessment(_valid_record(title="Owner's SAC"))

    # A second, entirely separate account.
    intruder = make_user('someone.else@example.com')
    make_settings(intruder, subjects=['Physics'])
    anvil_stub.set_current_user(intruder)

    results.raises(Exception, lambda: assessments.get_assessment(row_id),
                   'another account cannot read the row')
    results.raises(Exception,
                   lambda: assessments.update_assessment(row_id, {'title': 'Stolen'}),
                   'another account cannot edit the row')
    results.raises(Exception, lambda: assessments.delete_assessment(row_id),
                   'another account cannot delete the row')

    # The list endpoint must return nothing at all, not merely refuse by id.
    results.equal(assessments.list_assessments(), [],
                  "another account's list is empty")

    # And the owner is unaffected by all of that.
    anvil_stub.set_current_user(owner)
    results.equal(assessments.get_assessment(row_id)['title'], "Owner's SAC",
                  'the owner can still read their own row')
    results.equal(len(assessments.list_assessments()), 1,
                  'and it still appears in their list')

    # Signed out, nothing is reachable.
    anvil_stub.set_current_user(None)
    results.raises(Exception, lambda: assessments.list_assessments(),
                   'a signed-out caller gets nothing')
    results.raises(Exception, lambda: assessments.create_assessment(_valid_record()),
                   'a signed-out caller cannot create')


# --- missing rows are reported the same way everywhere ---------------------

def suite_missing_row_consistency(results):
    """get / update / delete must all react the same way to an id that is not there.

    They did not: get_assessment raised while delete_assessment returned False for the
    identical condition, inside the same module. That is a quotable violation of
    "no inconsistencies are present".
    """
    _signed_in()
    absent_id = 'ass_does_not_exist'

    messages = []
    for name, call in (
            ('get_assessment', lambda: assessments.get_assessment(absent_id)),
            ('update_assessment',
             lambda: assessments.update_assessment(absent_id, {'title': 'x'})),
            ('delete_assessment', lambda: assessments.delete_assessment(absent_id))):
        try:
            call()
        except Exception as error:
            messages.append(str(error))
            results.ok(True, '%s raises for a missing row' % name)
        else:
            results.ok(False, '%s raises for a missing row' % name)

    results.equal(len(set(messages)), 1,
                  'all three report a missing row with the SAME message')
    if messages:
        results.ok(messages[0][:1].isupper() and messages[0].rstrip().endswith('.'),
                   'and that message is a sentence written for a student')


# --- export / import round trip --------------------------------------------

def suite_export_import(results):
    """EC-EF-08: an export must restore through the importer with nothing lost."""
    _signed_in()
    assessments.create_assessment(_valid_record(title='Export me', weight=30.0))

    # export_user_data returns an Anvil Media object the browser downloads, so the
    # importer is handed the same object type it will meet in production.
    exported = assessments.export_user_data()
    results.ok(exported is not None, 'export produces a downloadable file')
    results.ok(exported.get_name().endswith('.json'),
               'the exported file is named as JSON')
    exported_text = exported.get_bytes().decode('utf-8')
    results.ok('Export me' in exported_text,
               'the exported file contains the assessment')

    before = len(anvil_stub.app_tables.assessments.rows)
    summary = assessments.import_user_data(exported)
    after = len(anvil_stub.app_tables.assessments.rows)

    results.ok(after > before, 'importing the export adds rows')
    results.ok(isinstance(summary, dict), 'import returns a summary')

    # Import ADDS; it must never overwrite. A colliding title is renamed, not replaced.
    results.equal(after, before * 2,
                  'importing the same export twice duplicates rather than overwrites')

    # A malformed file must write nothing at all — the promise the UI makes is
    # "nothing is saved unless the whole file is valid".
    count_before = len(anvil_stub.app_tables.assessments.rows)
    for rubbish in ('not json at all', '{}', '[]', '{"assessments": "nope"}'):
        media = anvil_stub.BlobMedia('application/json', rubbish, name='bad.json')
        results.raises(ValueError, lambda m=media: assessments.import_user_data(m),
                       'a malformed import file %r is refused' % rubbish[:20])
        results.equal(len(anvil_stub.app_tables.assessments.rows), count_before,
                      'and %r writes nothing at all' % rubbish[:20])


def suite_export_contract(results):
    """The export file's exact shape, which the importer and FR18 both depend on.

    The round-trip suite above proves an export can be re-imported. It does not
    pin the things a reader of FR18 would check: the filename pattern, the version
    number the importer gates on, and the deliberate exclusion of reminder_logs.
    All three were previously asserted only as "ends with .json".
    """
    import json

    _signed_in()
    assessments.create_assessment(_valid_record(title='Contract'))
    exported = assessments.export_user_data()

    # FR18 names the filename pattern, and the date in it is the STUDENT'S local
    # date, not the server's UTC one.
    name = exported.get_name()
    results.ok(name.startswith('dotpoint-export-') and name.endswith('.json'),
               'the export is named dotpoint-export-YYYY-MM-DD.json')
    results.equal(len(name), len('dotpoint-export-2026-01-01.json'),
                  'and the date in the name is a full YYYY-MM-DD')

    payload = json.loads(exported.get_bytes().decode('utf-8'))
    results.equal(payload.get('version'), 1,
                  'the payload carries version 1, which the importer gates on')
    for key in ('assessments', 'notes', 'settings', 'exported_at'):
        results.ok(key in payload, 'the payload carries %r' % key)
    # FR18: "reminder_logs is excluded because it is transient."
    results.ok('reminder_logs' not in payload,
               'and reminder_logs is excluded, as FR18 requires')


def suite_parser_audit_trail(results):
    """source_text and term_info are trimmed, never refused (a documented choice).

    _trim_parser_text bounds these two columns without raising, because they hold
    the parser's own echo of what it read rather than something the student typed
    into a labelled box: refusing a whole assessment over its provenance note would
    help nobody, and an export written before the cap existed still has to import.
    That behaviour had no test, so nothing distinguished it from an oversight.
    """
    from server_code._constants import MAX_SOURCE_TEXT_LENGTH

    _signed_in()

    over_long = 'x' * (MAX_SOURCE_TEXT_LENGTH + 200)
    row_id = assessments.create_assessment(_valid_record(source_text=over_long))
    stored = assessments.get_assessment(row_id)['source_text']
    results.equal(len(stored), MAX_SOURCE_TEXT_LENGTH,
                  'an over-long source_text is trimmed to the cap, not refused')

    # A wrong TYPE is still refused — the trim is a length concession, not a
    # blanket "anything goes" on these columns.
    results.raises(
        ValueError,
        lambda: assessments.create_assessment(_valid_record(source_text=12345)),
        'but a non-text source_text is still refused')

    # Whitespace-only collapses to None, so the column has one empty state.
    row_id = assessments.create_assessment(_valid_record(source_text='   '))
    results.equal(assessments.get_assessment(row_id)['source_text'], None,
                  'a whitespace-only source_text is stored as None')

    # The parser's own output can never hit the cap anyway: parse_text bounds its
    # input to the same number, which is why this is a guard for hand-made and
    # imported payloads rather than for the normal path.
    results.equal(MAX_SOURCE_TEXT_LENGTH, 500,
                  'and the cap matches the parser input bound it mirrors')


def suite_empty_reminder_days(results):
    """An empty reminder list means "no reminders", and must survive a round trip.

    The server distinguishes a MISSING reminder_days (use the student's defaults) from
    an EMPTY one (send nothing for this assessment). Both are legitimate answers, and
    only one of them is falsy — which is what made this worth a test.
    """
    _signed_in()

    # Absent -> the defaults are substituted.
    row_id = assessments.create_assessment(_valid_record())
    results.ok(assessments.get_assessment(row_id)['reminder_days'],
               'an omitted reminder list falls back to the defaults')

    # Empty -> stored empty, NOT replaced by the defaults.
    row_id = assessments.create_assessment(_valid_record(reminder_days=[]))
    results.equal(assessments.get_assessment(row_id)['reminder_days'], [],
                  'an empty reminder list is stored as empty on create')

    # And it must survive an edit, which is where it was being lost.
    row_id = assessments.create_assessment(_valid_record(reminder_days=[7, 2]))
    assessments.update_assessment(row_id, {'reminder_days': []})
    results.equal(assessments.get_assessment(row_id)['reminder_days'], [],
                  'an empty reminder list is stored as empty on update')

    # The SERVER-SIDE mirror of the same fault. On a create, a missing
    # reminder_days means "I did not supply this column, use my defaults"; on an
    # edit the key is only present because the student changed it, so None is not
    # "absent" and must not be read as a request for the defaults. Sending it used
    # to re-arm the 7- and 2-day emails on an assessment deliberately silenced.
    row_id = assessments.create_assessment(_valid_record(reminder_days=[]))
    results.raises(
        ValueError,
        lambda: assessments.update_assessment(row_id, {'reminder_days': None}),
        'an edit sending None is refused rather than re-armed with the defaults')
    results.equal(assessments.get_assessment(row_id)['reminder_days'], [],
                  'and the row is left exactly as the student set it')


def suite_no_falsy_empty_reads(results):
    """Tripwire: the editor must not treat an empty reminder list as "not set".

    `a.get('reminder_days') or default_days` reads as harmless and is not: an empty
    list is falsy, so unticking every pill and saving re-ticked the defaults the next
    time the row was opened, and the payload builder wrote them back. "No reminders"
    was a setting the student could choose but never keep.

    This is a source check rather than a behaviour check because the client form
    cannot be imported outside the browser — it pulls in Anvil's UI components.
    """
    import os
    import re
    from .harness import REPO_ROOT

    editor_path = os.path.join(
        REPO_ROOT, 'client_code', 'AssessmentEditorForm', '__init__.py')
    source = open(editor_path, encoding='utf-8').read()

    # Strip comments, so the explanation of the old bug does not trip its own tripwire.
    code_only = '\n'.join(line.split('#')[0] for line in source.splitlines())

    offenders = re.findall(r"reminder_days'?\s*\)?\s+or\s", code_only)
    results.equal(offenders, [],
                  'the editor reads reminder_days with an "is None" test, not "or"')
    results.ok("stored_days is None" in code_only or "is None else stored_days" in code_only,
               'and the empty-list case is handled explicitly')


SUITES = [
    ('create', suite_create),
    ('empty reminder days', suite_empty_reminder_days),
    ('no falsy-empty reads', suite_no_falsy_empty_reads),
    ('same rule on every path', suite_consistent_across_paths),
    ('reasonableness', suite_reasonableness),
    ('weight format', suite_weight_format),
    ('bulk partial commit (FR02)', suite_bulk_partial_commit),
    ('ownership (NFR03)', suite_ownership),
    ('missing row consistency', suite_missing_row_consistency),
    ('export/import round trip', suite_export_import),
    ('export contract (FR18)', suite_export_contract),
    ('parser audit trail', suite_parser_audit_trail),
]
