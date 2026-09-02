import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""OnboardingForm - mandatory "What subjects do you do?" step (spec §11).

Shown by the Main router to any logged-in user whose user_settings.subjects
is empty, so it runs exactly once after signup (and for accounts created
before this feature shipped). The student multi-selects from the VCE catalog
(common.SubjectPicker); Confirm calls notes.set_subjects, which enforces the
program rules server-side: at least one mathematics study (client mandate),
and an English-group study always present (VCAA rule — 'English' is appended
automatically if none was chosen; the form warns first so it's never a
surprise). After locking in, subjects drive the editor dropdown, dashboard
filter, parser alias priority and the Exams view; they change only via the
deliberate Settings flow.

Structure (spec §14): the page title sits on the page body, then a single
centred card — make_page() > make_page_title() + make_card() — opened by
make_section_header('Your studies') and holding the two program rules as a
micro line, the pill picker and the two actions. That is the same shape as
every other screen (title on the page, card opened by its own section
header), so onboarding no longer reads as a one-off. There is deliberately NO
top bar: the router re-renders this form for every hash until subjects exist,
so nav links would promise pages the student cannot reach yet. The only ways
out are 'Lock in my subjects' and 'Sign out' — which is also why the
catalog-load failure below keeps both a retry and a sign out.
"""

import anvil
import anvil.server
from anvil import ColumnPanel, Label, Button, confirm, open_form

from ..common import (
    SubjectPicker, set_session_settings, apply_theme, _sign_out,
    navigate, toast, toast_error, toast_warn, friendly_error,
    make_page, make_card, make_page_title, make_section_header, make_row,
    make_empty_state,
)

# --- mirrors of server constants --------------------------------------------
# Anvil client code cannot import a server module, so these two tuples are hand
# copies. Each one names the constant it copies so the original is findable:
#
#   ENGLISH_GROUP  copies  server_code/_constants.py  ENGLISH_GROUP
#   MATHS_GROUP    copies  server_code/_constants.py  MATHS_GROUP
#
# They must hold EXACTLY what the server holds, because notes._clean_subjects
# applies the same two membership tests to the same selection: any entry missing
# here is a selection this form rejects and the server would have accepted.
# MATHS_GROUP was missing 'Mathematics' (the parser's generic maths study), which
# is exactly that bug. Copying 'Mathematics' cannot put a non-study in front of
# the student — the picker is built from the server's SUBJECT_GROUPS, which
# deliberately omits it — it only makes the client's answer to "have you picked
# a maths study?" identical to the server's.
ENGLISH_GROUP = ('English', 'English as an Additional Language',
                 'English Language', 'Literature')
MATHS_GROUP = ('Mathematics', 'Foundation Mathematics', 'General Mathematics',
               'Mathematical Methods', 'Specialist Mathematics')

# The two VCE program rules, worded ONCE. The same sentences appear in
# SettingsForm, and the maths sentence is word-for-word the one
# notes._clean_subjects raises, so a student meets one wording for one rule no
# matter which screen they are on and no matter which side caught them.
MATHS_RULE_MESSAGE = ('Select at least one mathematics study '
                      '(Foundation, General, Methods or Specialist).')
ENGLISH_RULE_MESSAGE = ("You haven't picked an English-group study. Every VCE "
                        "program includes one, so 'English' will be added "
                        "automatically. Continue?")
NO_SELECTION_MESSAGE = 'Pick your subjects first.'


class OnboardingForm(ColumnPanel):
    def __init__(self, **properties):
        super().__init__(**properties)
        self.spacing_above = 'none'
        self.spacing_below = 'none'

        body = make_page()
        self.add_component(body)

        # One hierarchy: page title -> what it's for -> the rules -> the choice.
        # The title belongs to the page, not to the card, so it lines up with
        # every other screen; the card below is opened by its own section
        # header instead of borrowing the h1.
        body.add_component(make_page_title(
            'What subjects do you do?',
            'Your studies drive the parser, dashboard and exam timetable. '
            'Change them any time in Settings.'))

        card = make_card()
        body.add_component(card)
        card.add_component(make_section_header('Your studies'))

        # The rules sit in a micro line rather than a paragraph because the
        # student only needs them while they are actually picking.
        card.add_component(Label(
            text='One mathematics study is required, and an English-group '
                 'study is always kept (VCAA rule).',
            role='micro'))

        try:
            catalog = anvil.server.call('get_subject_catalog')
        except Exception as e:
            # friendly_error, not str(e): this is the very first screen a new
            # student sees, and the raw failure here is an Anvil transport
            # string, never a sentence written for them. Anything the server
            # DID write for them still passes straight through.
            #
            # The router re-renders this form for every hash while the user is
            # un-onboarded, so a dead-end here would trap them behind the
            # onboarding gate: keep BOTH escape hatches — retry and sign out.
            toast_error(friendly_error(
                e, "Couldn't load the subject list. Check your connection and "
                   "try again."))
            card.add_component(make_empty_state(
                "Couldn't load the subject list",
                'Check your connection and try again.',
                'Retry',
                lambda: open_form('Main')))
            card.add_component(self._sign_out_row())
            return

        # The picker is a dumb component: it renders the catalog as toggle pills
        # with live per-group counts and hands back a plain list of names. All
        # VCE rules are checked below and again server-side in set_subjects.
        self._picker = SubjectPicker(catalog)
        card.add_component(self._picker)

        confirm_btn = Button(text='Lock in my subjects', role='primary')
        confirm_btn.set_event_handler('click', self._on_confirm)
        # Sign out is a quiet ghost button beside the primary action: it must be
        # reachable (this screen is a gate) without competing with it.
        card.add_component(make_row(confirm_btn, self._sign_out_button()))

    # --- shared bits -------------------------------------------------------
    def _sign_out_button(self):
        """The escape hatch, built once so both the normal and error layouts
        offer exactly the same way out."""
        btn = Button(text='Sign out', role='ghost')
        btn.set_event_handler('click', lambda **e: _sign_out())
        return btn

    def _sign_out_row(self):
        return make_row(self._sign_out_button())

    # --- handlers ----------------------------------------------------------
    def _on_confirm(self, **event_args):
        selection = self._picker.get_selection()

        # Validate in the order the student would fix things: something chosen,
        # then maths, then the English warning (which is a confirm, not a block,
        # because the server can repair it by appending 'English'). Same three
        # checks, same order, same sentences as the Settings change-subjects
        # flow — the rule must not read differently depending on the screen.
        # There is no field to hang a message on (the picker is one component
        # covering the whole card), so these are toasts by necessity.
        if not selection:
            toast_warn(NO_SELECTION_MESSAGE)
            return
        if not any(s in MATHS_GROUP for s in selection):
            toast_error(MATHS_RULE_MESSAGE)
            return
        if not any(s in ENGLISH_GROUP for s in selection):
            if not confirm(ENGLISH_RULE_MESSAGE):
                return

        try:
            settings = anvil.server.call('set_subjects', selection)
        except Exception as e:
            # The server's subject rules raise sentences written for the
            # student, so friendly_error shows them unchanged; a dropped
            # connection gets the fallback instead of a transport string.
            toast_error(friendly_error(
                e, "Couldn't lock in those subjects. Check your connection "
                   "and try again."))
            return

        # set_subjects returns the saved settings row, so push it straight into
        # the session cache: the router reads it on the very next navigation to
        # decide that onboarding is done, with no second round-trip.
        set_session_settings(settings)
        apply_theme(settings.get('theme'))
        toast("Subjects locked in — welcome to DotPoint!")
        navigate('dashboard')
