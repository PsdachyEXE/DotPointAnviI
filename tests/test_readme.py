"""Guards the user manual: two copies must not drift, and it must stay truthful.

`server_code/README.txt` is the deliverable the brief asks for — the user manual and
legal notice, placed beside the server modules. `theme/assets/README.txt` is a copy,
because Anvil's IDE lists only .py files under Server Code and a marker opening the
editor would otherwise never see it.

Two copies of one document is exactly the drift the constant-mirror suite exists to
catch elsewhere, so the same treatment applies here.

The later suites check claims the manual makes about the software. A user manual that
publishes a login that does not work, or quotes a limit the code does not enforce, is
worse than no manual — and unlike prose, those particular claims are checkable.
"""

import os
import re

from .harness import load_server_code, REPO_ROOT

load_server_code()

from server_code import _constants


SERVER_COPY = os.path.join(REPO_ROOT, 'server_code', 'README.txt')
ASSET_COPY = os.path.join(REPO_ROOT, 'theme', 'assets', 'README.txt')


def _manual_text():
    return open(SERVER_COPY, encoding='utf-8').read()


def suite_copies_match(results):
    """The asset copy must be byte-identical to the server copy."""
    results.ok(os.path.exists(SERVER_COPY),
               'server_code/README.txt exists (the deliverable the brief names)')
    results.ok(os.path.exists(ASSET_COPY),
               'theme/assets/README.txt exists (so the IDE shows it)')
    if not (os.path.exists(SERVER_COPY) and os.path.exists(ASSET_COPY)):
        return

    server_bytes = open(SERVER_COPY, 'rb').read()
    asset_bytes = open(ASSET_COPY, 'rb').read()
    results.equal(len(asset_bytes), len(server_bytes),
                  'the two copies are the same length')
    results.ok(server_bytes == asset_bytes,
               'the asset copy is byte-identical to the server copy')


def suite_required_sections(results):
    """Every section the teacher's brief asks for must actually be present."""
    text = _manual_text()

    # The brief: "Introduction to the app: its name, its purposes"; "Legal notice";
    # "How-to guide for the most common use cases" including login and CRUD.
    for heading, why in (
            ('WHAT DOTPOINT IS', 'introduction to the app'),
            ('GETTING IN', 'login instructions'),
            ('LEGAL NOTICE', 'the legal notice the brief requires'),
            ('ACKNOWLEDGEMENTS', 'third-party attribution'),
            ('ADDING ASSESSMENTS', 'the create use cases'),
            ('WORKING WITH YOUR ASSESSMENTS', 'the read/update/delete use cases'),
            ('NOTES', 'the notes use cases'),
            ('BACKING UP AND RESTORING', 'export and import'),
    ):
        results.ok(heading in text, 'the manual has a %s section (%s)' % (heading, why))

    # The legal notice has to cover the specific ground the brief names: how data is
    # treated, and how copyright and other users' privacy are respected.
    for phrase, topic in (
            ('never stores your password', 'password handling'),
            ('Nobody but you', 'user scoping / privacy'),
            ('VICTORIAN CURRICULUM AND ASSESSMENT AUTHORITY', 'VCAA attribution'),
            ('stays the property of whoever wrote it', 'third-party copyright'),
            ('kept until you delete it', 'retention'),
    ):
        results.ok(phrase in text,
                   'the legal notice covers %s' % topic)


def suite_login_published(results):
    """The brief requires a working plaintext login. It must be present and complete."""
    text = _manual_text()

    # An email and a password, adjacent, in the Getting In section.
    emails = re.findall(r'[\w.\-]+@[\w.\-]+\.\w+', text)
    results.ok(any(e.endswith('dotpoint.dev') for e in emails),
               'a test account email is published')
    results.ok('DotPointTest2026!' in text,
               'its password is published in plaintext, as the brief requires')

    # The brief asks for "different logins for different accounts if you have a
    # different admin account from a user account". This app has no admin role, and
    # saying so explicitly is what stops an assessor hunting for one.
    results.ok('NO admin account' in text or 'no admin' in text.lower(),
               'the manual states plainly that there is no admin account')


def suite_claims_match_the_code(results):
    """Limits quoted in the manual must be the limits the code actually enforces.

    These are the manual's only mechanically checkable claims, and they are the ones
    most likely to drift as the constants change.
    """
    text = _manual_text()

    for value, description in (
            (_constants.MAX_TITLE_LENGTH, 'the title cap'),
            (_constants.MAX_DESCRIPTION_LENGTH, 'the description cap'),
            (_constants.MAX_SUBJECTS_PER_STUDENT, 'the subject cap'),
            (_constants.MAX_REMINDER_DAY, 'the maximum reminder offset'),
    ):
        # Rendered with a thousands separator in prose ("20,000 characters"), so both
        # spellings count.
        plain = str(value)
        grouped = '{:,}'.format(value)
        results.ok(plain in text or grouped in text,
                   'the manual quotes %s (%s) correctly' % (description, plain))

    # The published URL the manual sends the reader to must be the one the app uses
    # to build its own reminder links.
    results.ok(_constants.APP_BASE_URL in text,
               'the manual quotes the published app URL the server actually uses')

    # The urgency bands described in the manual must be the ones the server can emit.
    for band in ('overdue', 'today', 'soon', 'distant'):
        emitted = [name for _threshold, name in _constants.URGENCY_THRESHOLDS]
        results.ok(band in emitted,
                   'the manual describes the %r band, which the server emits' % band)


SUITES = [
    ('copies match', suite_copies_match),
    ('required sections', suite_required_sections),
    ('login published', suite_login_published),
    ('claims match the code', suite_claims_match_the_code),
]
