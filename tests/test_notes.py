"""Proves notes, settings, school terms and account creation validate correctly.

notes.py carries four separate surfaces, so this file is organised by surface rather
than by rubric heading. The school-terms suite is the most important one here: those
values drive the parser's "Term X Week Y" resolution, and a backwards term used to
break it silently, with nothing shown to the student anywhere.
"""

from .harness import load_server_code, make_user, make_settings
from . import anvil_stub

load_server_code()

from server_code import notes
from server_code._constants import (
    MAX_TITLE_LENGTH, MAX_NOTE_CONTENT_LENGTH, MAX_TAG_LENGTH, MAX_TAGS_PER_NOTE,
    MIN_REMINDER_DAY, MAX_REMINDER_DAY,
)


def _signed_in():
    user = make_user()
    make_settings(user, subjects=['Mathematical Methods', 'English', 'Physics'])
    return user


def _terms(**overrides):
    """Four well-formed, non-overlapping Victorian-shaped terms."""
    terms = [
        {'term': 1, 'start_date': '2026-01-28', 'end_date': '2026-04-02'},
        {'term': 2, 'start_date': '2026-04-20', 'end_date': '2026-06-26'},
        {'term': 3, 'start_date': '2026-07-13', 'end_date': '2026-09-18'},
        {'term': 4, 'start_date': '2026-10-05', 'end_date': '2026-12-18'},
    ]
    for index, replacement in overrides.items():
        terms[int(index[-1])] = replacement
    return terms


# --- notes -----------------------------------------------------------------

def suite_note_fields(results):
    """A note's title, content and tags are each bounded."""
    _signed_in()

    note_id = notes.create_note({'title': 'Calculus revision',
                                 'content': 'Chapter 7', 'tags': ['methods']})
    results.ok(isinstance(note_id, str) and note_id, 'a valid note is created')

    for description, record in (
            ('a blank title', {'title': '   ', 'content': 'x'}),
            ('an over-long title', {'title': 'x' * (MAX_TITLE_LENGTH + 1)}),
            ('over-long content',
             {'title': 'ok', 'content': 'x' * (MAX_NOTE_CONTENT_LENGTH + 1)}),
            ('too many tags',
             {'title': 'ok', 'tags': ['t%d' % i for i in range(MAX_TAGS_PER_NOTE + 1)]}),
            ('an over-long tag',
             {'title': 'ok', 'tags': ['x' * (MAX_TAG_LENGTH + 1)]}),
            ('tags that are not a list', {'title': 'ok', 'tags': 'methods'}),
            ('a non-string tag', {'title': 'ok', 'tags': [123]}),
    ):
        results.raises(ValueError, lambda r=record: notes.create_note(dict(r)),
                       'create_note refuses %s' % description)
        results.raises(ValueError,
                       lambda r=record: notes.update_note(note_id, dict(r)),
                       'update_note refuses %s' % description)


def suite_note_missing_row_consistency(results):
    """delete_note and toggle_pin must react the same way to an id that is not there."""
    _signed_in()
    absent_id = 'not_1'

    messages = []
    for name, call in (('delete_note', lambda: notes.delete_note(absent_id)),
                       ('toggle_pin', lambda: notes.toggle_pin(absent_id)),
                       ('update_note',
                        lambda: notes.update_note(absent_id, {'title': 'x'}))):
        try:
            call()
        except Exception as error:
            messages.append(str(error))
            results.ok(True, '%s raises for a missing note' % name)
        else:
            results.ok(False, '%s raises for a missing note' % name)

    results.equal(len(set(messages)), 1,
                  'all three report a missing note with the SAME message')


def suite_note_ownership(results):
    """A note belongs to exactly one account and is invisible to every other."""
    owner = _signed_in()
    note_id = notes.create_note({'title': 'Private revision notes'})

    intruder = make_user('other@example.com')
    make_settings(intruder)
    anvil_stub.set_current_user(intruder)

    results.raises(Exception, lambda: notes.update_note(note_id, {'title': 'x'}),
                   'another account cannot edit the note')
    results.raises(Exception, lambda: notes.delete_note(note_id),
                   'another account cannot delete the note')
    results.raises(Exception, lambda: notes.toggle_pin(note_id),
                   'another account cannot pin the note')
    results.equal(notes.search_notes(), [], "another account's note list is empty")

    anvil_stub.set_current_user(owner)
    results.equal(len(notes.search_notes()), 1, 'the owner still sees their note')


def suite_note_search_guards(results):
    """Searching must survive a tag column that is not a list of strings.

    search_notes lowercases every tag; a stored non-string used to raise
    AttributeError and take the whole Notes screen down.
    """
    user = _signed_in()
    notes.create_note({'title': 'Good note', 'tags': ['methods']})

    # Write a corrupt tags cell directly, as a Data Tables console edit would.
    row = list(anvil_stub.app_tables.notes.rows.values())[0]
    for corrupt in ([123], 'methods', {'a': 1}, None, [None, 'ok']):
        row['tags'] = corrupt
        results.does_not_raise(lambda: notes.search_notes(),
                               'search survives a tags column holding %r' % (corrupt,))
        results.does_not_raise(lambda: notes.search_notes(tag='methods'),
                               'tag filter survives a tags column holding %r' % (corrupt,))


# --- settings --------------------------------------------------------------

def suite_settings(results):
    """Settings values are validated, and the defaults are usable."""
    _signed_in()

    current = notes.get_settings()
    results.ok(isinstance(current, dict), 'get_settings returns a plain dict')

    results.does_not_raise(
        lambda: notes.update_settings({'default_reminder_days': [7, 2]}),
        'a valid reminder-day list is accepted')

    for description, fields in (
            ('an unbounded reminder day', {'default_reminder_days': [999999]}),
            ('a zero reminder day', {'default_reminder_days': [0]}),
            ('a boolean reminder day', {'default_reminder_days': [True]}),
            ('reminder days that are not a list', {'default_reminder_days': 7}),
            ('a non-boolean notifications switch', {'notifications_enabled': 'yes'}),
            ('an unresolvable timezone', {'timezone': 'Australia/Melbourn'}),
            ('a blank timezone', {'timezone': '   '}),
            ('a theme outside the offered set', {'theme': 'neon'}),
    ):
        results.raises(ValueError, lambda f=fields: notes.update_settings(dict(f)),
                       'update_settings refuses %s' % description)

    # A real alternative timezone must still be accepted — the check must not simply
    # pin everyone to Melbourne.
    results.does_not_raise(
        lambda: notes.update_settings({'timezone': 'Australia/Perth'}),
        'a different real timezone is accepted')


def suite_school_terms(results):
    """School terms drive FR15, and a bad set used to fail silently."""
    _signed_in()

    results.does_not_raise(
        lambda: notes.update_settings({'school_terms': _terms()}),
        'four well-formed terms are accepted')

    backwards = _terms()
    backwards[0] = {'term': 1, 'start_date': '2026-04-02', 'end_date': '2026-01-28'}
    results.raises(ValueError,
                   lambda: notes.update_settings({'school_terms': backwards}),
                   'a term that runs backwards is refused')

    overlapping = _terms()
    overlapping[1] = {'term': 2, 'start_date': '2026-03-01', 'end_date': '2026-06-26'}
    results.raises(ValueError,
                   lambda: notes.update_settings({'school_terms': overlapping}),
                   'two terms that overlap are refused')

    duplicate = _terms()
    duplicate[1] = {'term': 1, 'start_date': '2026-04-20', 'end_date': '2026-06-26'}
    results.raises(ValueError,
                   lambda: notes.update_settings({'school_terms': duplicate}),
                   'two terms with the same number are refused')

    for description, terms in (
            ('a term number of 0', [{'term': 0, 'start_date': '2026-01-28',
                                     'end_date': '2026-04-02'}]),
            ('a term number of 9', [{'term': 9, 'start_date': '2026-01-28',
                                     'end_date': '2026-04-02'}]),
            ('a date in the wrong format', [{'term': 1, 'start_date': '28/01/2026',
                                             'end_date': '2026-04-02'}]),
            ('a date that is not real', [{'term': 1, 'start_date': '2026-02-30',
                                          'end_date': '2026-04-02'}]),
            ('a term that is not a dict', ['term one']),
            ('terms that are not a list', 'term one'),
    ):
        results.raises(ValueError,
                       lambda t=terms: notes.update_settings({'school_terms': t}),
                       'school terms refuse %s' % description)

    # SAT 5 section 4.2.3 documents the keys as start/end while the code uses
    # start_date/end_date. Both shapes are accepted so a doc-conformant or
    # hand-authored file is not rejected.
    doc_shaped = [{'term': 1, 'start': '2026-01-28', 'end': '2026-04-02'}]
    results.does_not_raise(
        lambda: notes.update_settings({'school_terms': doc_shaped}),
        'the documented start/end key names are also accepted')


def suite_settings_read_guards(results):
    """A damaged settings row must still produce a usable payload for the client."""
    user = make_user()
    settings_row = make_settings(user)

    corruptions = {
        'default_reminder_days': [7, 'not a day', 999999, True, 2],
        'school_terms': 'not a list at all',
        'subjects': ['Mathematical Methods', 'Underwater Basket Weaving', 42],
        'timezone': 'Australia/Melbourn',
        'notifications_enabled': None,
        'theme': 'neon',
    }
    for column, value in corruptions.items():
        settings_row[column] = value

    payload = None
    try:
        payload = notes.get_settings()
        results.ok(True, 'a thoroughly corrupt settings row still loads')
    except Exception as error:
        results.ok(False, 'a thoroughly corrupt settings row still loads (%s)' % error)

    if payload:
        results.equal(payload['default_reminder_days'], [7, 2],
                      'unusable reminder days are dropped, the good ones kept')
        results.equal(payload['school_terms'], [],
                      'a non-list school_terms column degrades to empty')
        results.equal(payload['subjects'], ['Mathematical Methods'],
                      'subjects that are not VCE studies are dropped')
        results.equal(payload['timezone'], 'Australia/Melbourne',
                      'an unresolvable timezone falls back to the default')
        results.equal(payload['notifications_enabled'], False,
                      'an unset notifications switch reads as off, matching the emailer')


# --- subjects --------------------------------------------------------------

def suite_subjects(results):
    """The VCE program rules are enforced on the server, not just in the browser."""
    _signed_in()

    results.does_not_raise(
        lambda: notes.set_subjects(['Mathematical Methods', 'English', 'Physics']),
        'a valid program is accepted')

    results.raises(ValueError,
                   lambda: notes.set_subjects(['English', 'Physics']),
                   'a program with no mathematics study is refused',
                   message_contains='math')

    results.raises(ValueError,
                   lambda: notes.set_subjects(['Underwater Basket Weaving']),
                   'a subject that is not a VCE study is refused')

    results.raises(ValueError, lambda: notes.set_subjects([]),
                   'an empty program is refused')

    results.raises(ValueError, lambda: notes.set_subjects('Physics'),
                   'a program that is not a list is refused')

    # The catch-all is not a study and must not be settable.
    results.raises(ValueError,
                   lambda: notes.set_subjects(['Mathematics', 'English']),
                   "the parser's generic 'Mathematics' catch-all cannot be locked in")


# --- account creation ------------------------------------------------------

def suite_account(results):
    """Email format is checked before an unusable account can be created."""
    anvil_stub.reset()

    results.does_not_raise(
        lambda: notes.create_account('will@example.com', 'DotPointTest2026!'),
        'a well-formed email and password create an account')

    for bad_email in ('will@', '@example.com', 'will example.com', 'will', '   '):
        results.raises(ValueError,
                       lambda e=bad_email: notes.create_account(e, 'DotPointTest2026!'),
                       'a malformed email %r is refused' % bad_email)

    results.raises(ValueError,
                   lambda: notes.create_account('someone@example.com', 'x'),
                   'a too-short password is refused')

    # A duplicate must be reported in a sentence, not as a platform exception name.
    try:
        notes.create_account('will@example.com', 'DotPointTest2026!')
        results.ok(False, 'a duplicate email is refused')
    except Exception as error:
        message = str(error)
        results.ok(True, 'a duplicate email is refused')
        results.ok(message[:1].isupper() and message.rstrip().endswith(('.', '!', '?')),
                   'and is reported as a sentence: %r' % message)
        results.ok('UserExists' not in message,
                   'without leaking the platform exception name')


SUITES = [
    ('note fields', suite_note_fields),
    ('note missing-row consistency', suite_note_missing_row_consistency),
    ('note ownership (NFR03)', suite_note_ownership),
    ('note search guards', suite_note_search_guards),
    ('settings', suite_settings),
    ('school terms (FR15)', suite_school_terms),
    ('settings read guards', suite_settings_read_guards),
    ('subjects', suite_subjects),
    ('account creation', suite_account),
]
