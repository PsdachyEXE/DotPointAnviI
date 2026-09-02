"""Tripwires for the copies of server constants that live in the client.

Anvil client code cannot import server modules, so several tables are duplicated into
the forms with a comment saying "keep in sync". A comment is not a mechanism: at the
time this suite was written, BOTH client copies of MATHS_GROUP were missing
'Mathematics', so a student whose only mathematics study is the one literally called
Mathematics was blocked by the client from an app the server would have accepted.

These tests are what "keep in sync" actually means. They read the client forms with
`ast` rather than importing them, because importing a form pulls in Anvil's UI
components, which do not exist outside the browser.

Also here: the design-system tripwires from the UI overhaul (no hardcoded colour in
client code; every role= has a matching stylesheet rule), which protect a different
kind of drift in the same spirit.
"""

import ast
import os
import re

from .harness import load_server_code, REPO_ROOT

load_server_code()

from server_code import _constants


CLIENT_DIR = os.path.join(REPO_ROOT, 'client_code')


def _module_constants(relative_path):
    """Return {NAME: value} for every literal module-level constant in a client file.

    Uses ast.literal_eval, so only genuine literals are returned — anything computed is
    skipped rather than guessed at.
    """
    path = os.path.join(CLIENT_DIR, relative_path)
    tree = ast.parse(open(path, encoding='utf-8').read())
    found = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id.isupper():
                try:
                    found[target.id] = ast.literal_eval(node.value)
                except (ValueError, SyntaxError):
                    pass
    return found


# --- the mirrors -----------------------------------------------------------

# 'Mathematics' is NOT a VCE study. It is the parser's generic catch-all: bare
# "maths"/"math" maps onto it, and nlp._match_subject then rewrites it to the student's
# own locked maths study when they have exactly one. _constants.py says so at the top
# of SUBJECT_GROUPS — "deliberately NOT in the picker: students lock in a specific
# maths study" — and it appears there as a GROUP HEADING, not as a selectable subject.
#
# It therefore appears in the server's MATHS_GROUP and in both client mirrors, and that
# is fine: those lists are only ever used to answer "has the student picked a maths
# study?" against what they actually selected, and the sentinel can never be selected.
# The guarantee that matters is enforced one level down, in suite_picker_catalog, at
# the catalog the picker is really built from.
PARSER_ONLY_SUBJECTS = frozenset(('Mathematics',))


def suite_subject_group_mirrors(results):
    """Each client copy of a subject group must equal the server's exactly.

    The client copies are used for ONE thing — answering "has the student picked a
    maths / an English study?" against what they selected — so equality with the
    server is the right invariant: the two must give the same answer to the same
    question. suite_picker_catalog below is what guarantees the sentinel can never
    reach a student, and it does so at the place that actually decides: the catalog.
    """
    mirrors = [
        ('OnboardingForm/__init__.py', 'MATHS_GROUP', _constants.MATHS_GROUP),
        ('OnboardingForm/__init__.py', 'ENGLISH_GROUP', _constants.ENGLISH_GROUP),
        ('SettingsForm/__init__.py', 'MATHS_GROUP', _constants.MATHS_GROUP),
        ('SettingsForm/__init__.py', 'ENGLISH_GROUP', _constants.ENGLISH_GROUP),
    ]
    for relative_path, name, server_value in mirrors:
        client_constants = _module_constants(relative_path)
        results.ok(name in client_constants,
                   '%s defines %s' % (relative_path, name))
        if name not in client_constants:
            continue
        # Compared as sets: the ORDER is a display choice each form may make, but the
        # MEMBERSHIP is the rule and must not differ by one entry.
        results.equal(set(client_constants[name]), set(server_value),
                      '%s %s matches server_code/_constants.%s'
                      % (relative_path, name, name))


def suite_picker_catalog(results):
    """The subject picker must never offer the parser-only catch-all as a study.

    This is the assertion that actually protects the student, and it is made where
    the decision is really taken. Both forms build their picker from
    notes.get_subject_catalog(), which serves _constants.SUBJECT_GROUPS — so what
    that returns is the whole truth about what can be selected, regardless of what
    any client-side mirror happens to contain.
    """
    from server_code import notes
    from .harness import make_user
    from . import anvil_stub

    # The catalog is a @anvil.server.callable, so it refuses a signed-out caller like
    # every other one. Assert that first — it is a cheap proof of the ownership model
    # (NFR03) at the one endpoint that returns no personal data and might plausibly
    # have been left open.
    anvil_stub.set_current_user(None)
    results.raises(Exception, notes.get_subject_catalog,
                   'the subject catalog refuses a signed-out caller')

    make_user()
    catalog = notes.get_subject_catalog()
    offered = set()
    for group in catalog:
        offered.update(group['subjects'])

    for sentinel in PARSER_ONLY_SUBJECTS:
        results.ok(sentinel not in offered,
                   'the picker never offers %r, which is not a real VCE study'
                   % sentinel)

    # Everything the picker DOES offer must be a canonical study, or a student could
    # lock in a subject that nothing else in the app recognises.
    for subject in offered:
        results.ok(subject in _constants.CANONICAL_SUBJECTS,
                   'the picker offers %r, which is a canonical study' % subject)

    # And the catalog must cover every canonical study — a study missing from the
    # picker is one no student can ever track.
    results.equal(offered, set(_constants.CANONICAL_SUBJECTS),
                  'the picker offers every canonical study and nothing else')


def suite_enum_mirrors(results):
    """Client dropdown option lists must offer exactly the values the server accepts.

    A client that offers a value the server rejects produces a save that always fails;
    a client that omits one the server accepts makes stored data uneditable — which is
    the bug that silently rewrote a stored type on save.
    """
    # The two forms name these constants differently — AssessmentEditorForm uses TYPES
    # and STATUSES, DashboardForm prefixes both with an underscore because they are
    # private to the module. So each is looked up under BOTH names.
    #
    # This matters: the first version of this suite looked only for the unprefixed
    # names and wrapped the DashboardForm half in `if name in dashboard:`, so those two
    # assertions were silently SKIPPED and the file went untested while the suite
    # reported green. A test that quietly passes is worse than no test, so a constant
    # that cannot be found is now a FAILURE rather than a shrug.
    for form_file, candidate_names in (
            ('AssessmentEditorForm/__init__.py', ('TYPES', '_TYPES')),
            ('DashboardForm/__init__.py', ('TYPES', '_TYPES')),
    ):
        _assert_enum_mirror(results, form_file, candidate_names,
                            _constants.VALID_TYPES, 'type')
    for form_file, candidate_names in (
            ('AssessmentEditorForm/__init__.py', ('STATUSES', '_STATUSES')),
            ('DashboardForm/__init__.py', ('STATUSES', '_STATUSES')),
    ):
        _assert_enum_mirror(results, form_file, candidate_names,
                            _constants.VALID_STATUSES, 'status')


def _assert_enum_mirror(results, form_file, candidate_names, server_values, label):
    """Assert one client enum mirror matches the server, whatever it is called.

    `candidate_names` are the names the constant might carry in that form. Not finding
    any of them is a failure, not a skip — see the note in suite_enum_mirrors.
    """
    constants = _module_constants(form_file)
    found = [name for name in candidate_names if name in constants]
    results.ok(found,
               '%s defines its %s list under one of %s'
               % (form_file, label, ' / '.join(candidate_names)))
    if not found:
        return

    # These are (display label, stored value) pairs; the STORED value is what has to
    # match the server, not the label the student reads on screen.
    entries = constants[found[0]]
    stored_values = set(
        entry[1] if isinstance(entry, (tuple, list)) else entry
        for entry in entries)
    results.equal(stored_values, set(server_values),
                  '%s %s offers exactly the server\'s values'
                  % (form_file, found[0]))


def suite_reminder_option_mirrors(results):
    """The two forms offering reminder days must offer the same set.

    They are separate constants in separate files, and the comment in each claims the
    other is kept in step.
    """
    editor = _module_constants('AssessmentEditorForm/__init__.py')
    settings = _module_constants('SettingsForm/__init__.py')
    if 'REMINDER_DAY_OPTIONS' in editor and 'REMINDER_DAY_OPTIONS' in settings:
        results.equal(tuple(editor['REMINDER_DAY_OPTIONS']),
                      tuple(settings['REMINDER_DAY_OPTIONS']),
                      'both forms offer the same reminder-day options')

    # And every option offered must be inside the range the server will now accept.
    for source, constants in (('AssessmentEditorForm', editor), ('SettingsForm', settings)):
        for day in constants.get('REMINDER_DAY_OPTIONS', ()):
            results.ok(_constants.MIN_REMINDER_DAY <= day <= _constants.MAX_REMINDER_DAY,
                       '%s offers reminder day %r, which the server accepts' % (source, day))


# --- server-side self-consistency ------------------------------------------

def suite_server_constants(results):
    """The shared constant tables must be internally coherent."""
    # Every subject in a group must be a canonical study — except the documented
    # parser-only catch-all, which is a group heading rather than a study.
    for group_name, group in (('MATHS_GROUP', _constants.MATHS_GROUP),
                              ('ENGLISH_GROUP', _constants.ENGLISH_GROUP)):
        for subject in group:
            results.ok(subject in _constants.CANONICAL_SUBJECTS
                       or subject in PARSER_ONLY_SUBJECTS,
                       '%s member %r is a canonical study or the documented catch-all'
                       % (group_name, subject))

    # The catch-all must be exactly that: a heading in SUBJECT_GROUPS and never a
    # selectable study. If it ever became canonical it would appear in the picker and
    # a student could lock in a subject that is not a real VCE study.
    group_headings = set(heading for heading, _studies in _constants.SUBJECT_GROUPS)
    for sentinel in PARSER_ONLY_SUBJECTS:
        results.ok(sentinel not in _constants.CANONICAL_SUBJECTS,
                   '%r is not a selectable study' % sentinel)
        results.ok(sentinel in group_headings,
                   '%r exists as a learning-area heading' % sentinel)

    # Every alias must resolve to a canonical study, or to the catch-all that
    # nlp._match_subject then rewrites to the student's own maths study. An alias
    # pointing anywhere else silently stops the parser matching that subject forever.
    for alias, canonical in _constants.SUBJECT_ALIASES.items():
        results.ok(canonical in _constants.CANONICAL_SUBJECTS
                   or canonical in PARSER_ONLY_SUBJECTS,
                   'alias %r resolves to a canonical study or the catch-all' % alias)

    # The catch-all is only reachable through the maths aliases; nothing else may
    # emit it, or the rewrite in nlp._match_subject would not know what to do with it.
    catch_all_aliases = set(a for a, v in _constants.SUBJECT_ALIASES.items()
                            if v in PARSER_ONLY_SUBJECTS)
    results.equal(catch_all_aliases, {'math', 'maths', 'mathematics'},
                  'only the bare maths words map onto the catch-all')

    # Every legacy rename must land on a current study, or a legacy row stays broken.
    for old_name, new_name in _constants.LEGACY_SUBJECT_RENAMES.items():
        results.ok(new_name in _constants.CANONICAL_SUBJECTS,
                   'legacy rename %r -> %r lands on a canonical study'
                   % (old_name, new_name))

    # Every editable field must be a real column, or the whitelist silently drops a
    # legitimate edit.
    assessment_columns = {
        'title', 'subject', 'type', 'due_date', 'start_date', 'weight', 'status',
        'description', 'reminder_days', 'linked_note_ids', 'term_info',
        'confidence', 'source_text', 'user', 'created_at', 'updated_at'}
    for field in _constants.EDITABLE_FIELDS_ASSESSMENT:
        results.ok(field in assessment_columns,
                   'editable field %r is a real assessments column' % field)
    # And the audit-trail columns must NOT be editable from a browser.
    for protected in ('user', 'created_at', 'confidence', 'source_text'):
        results.ok(protected not in _constants.EDITABLE_FIELDS_ASSESSMENT,
                   '%r is not client-editable, so the parser audit trail survives edits'
                   % protected)

    # The bounds must make sense against each other.
    results.ok(_constants.MIN_WEIGHT < _constants.MAX_WEIGHT, 'weight bounds are ordered')
    results.ok(_constants.MIN_REMINDER_DAY < _constants.MAX_REMINDER_DAY,
               'reminder-day bounds are ordered')
    results.ok(_constants.MIN_TERM_NUMBER == 1 and _constants.MAX_TERM_NUMBER == 4,
               'the Victorian school year has four terms')

    # The urgency table must be ordered nearest-deadline-first, because _urgency_band
    # returns the FIRST match and the ordering IS the rule.
    thresholds = [threshold for threshold, _band in _constants.URGENCY_THRESHOLDS]
    results.equal(thresholds, sorted(thresholds),
                  'URGENCY_THRESHOLDS is ordered ascending, as _urgency_band assumes')

    results.ok(_constants.STATUS_COMPLETED in _constants.VALID_STATUSES,
               'STATUS_COMPLETED is a member of VALID_STATUSES')
    results.ok(_constants.STATUS_DEFAULT in _constants.VALID_STATUSES,
               'STATUS_DEFAULT is a member of VALID_STATUSES')


# --- design-system tripwires ------------------------------------------------

_HEX_COLOUR = re.compile(r'#[0-9a-fA-F]{3,8}\b')


def suite_no_client_colours(results):
    """No client form may hardcode a colour.

    Every colour is a CSS variable in the stylesheet so the light and dark palettes
    both work. A hex value baked into Python cannot change with the theme, which is
    how the same parser confidence once rendered in two different colours on two
    different screens.
    """
    # One assertion per FILE, not per line: a per-line assertion would inflate the
    # suite's total into the thousands and make the assertion count meaningless as
    # evidence. The offending line numbers still appear in the failure message.
    for directory, _subdirs, filenames in os.walk(CLIENT_DIR):
        if '__pycache__' in directory:
            continue
        for filename in filenames:
            if not filename.endswith('.py'):
                continue
            path = os.path.join(directory, filename)
            relative = os.path.relpath(path, REPO_ROOT)
            offenders = []
            for line_number, line in enumerate(open(path, encoding='utf-8'), 1):
                code = line.split('#')[0]          # ignore anything in a comment
                if _HEX_COLOUR.search(code):
                    offenders.append('%s:%d' % (relative, line_number))
            results.ok(not offenders,
                       'no hardcoded colour in %s (found: %s)'
                       % (relative, ', '.join(offenders) or 'none'))


def suite_roles_have_styles(results):
    """Every role= used in client code must have a rule in the stylesheet.

    A role with no rule is not an error at runtime — the component simply renders
    unstyled, and nobody notices until that screen is opened. This is the only
    mechanical way to catch it.
    """
    stylesheet = open(os.path.join(REPO_ROOT, 'anvil.yaml'), encoding='utf-8').read()

    used_roles = set()
    for directory, _subdirs, filenames in os.walk(CLIENT_DIR):
        if '__pycache__' in directory:
            continue
        for filename in filenames:
            if not filename.endswith('.py'):
                continue
            source = open(os.path.join(directory, filename), encoding='utf-8').read()
            used_roles.update(re.findall(r"role\s*=\s*'([a-z0-9-]+)'", source))
            used_roles.update(re.findall(r'role\s*=\s*"([a-z0-9-]+)"', source))

    for role in sorted(used_roles):
        results.ok('anvil-role-%s' % role in stylesheet,
                   'role %r has a matching stylesheet rule' % role)


SUITES = [
    ('subject group mirrors', suite_subject_group_mirrors),
    ('picker catalog', suite_picker_catalog),
    ('enum mirrors', suite_enum_mirrors),
    ('reminder option mirrors', suite_reminder_option_mirrors),
    ('server constants', suite_server_constants),
    ('no hardcoded client colours', suite_no_client_colours),
    ('roles have styles', suite_roles_have_styles),
]
