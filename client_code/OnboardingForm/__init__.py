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

Structure (spec §14): a single centred card — make_page() > make_card() —
holding title, one-line caption, the two program rules as a micro line, the
pill picker and the two actions. There is deliberately NO top bar: the router
re-renders this form for every hash until subjects exist, so nav links would
promise pages the student cannot reach yet. The only ways out are 'Lock in my
subjects' and 'Sign out'.
"""

import anvil
import anvil.server
from anvil import ColumnPanel, Label, Button, Spacer, confirm, open_form

from ..common import (
    SubjectPicker, set_session_settings, apply_theme, _sign_out,
    navigate, toast, toast_error,
    make_page, make_card, make_page_title, make_row, make_empty_state,
)

# Mirrors _constants.ENGLISH_GROUP / MATHS_GROUP (client can't import server
# modules; keep in sync).
ENGLISH_GROUP = ('English', 'English as an Additional Language',
                 'English Language', 'Literature')
MATHS_GROUP = ('Foundation Mathematics', 'General Mathematics',
               'Mathematical Methods', 'Specialist Mathematics')


class OnboardingForm(ColumnPanel):
    def __init__(self, **properties):
        super().__init__(**properties)
        self.spacing_above = 'none'
        self.spacing_below = 'none'

        body = make_page()
        self.add_component(body)

        card = make_card()
        body.add_component(card)

        # One hierarchy: title -> what it's for -> the rules -> the choice.
        # The rules sit in a micro line rather than a paragraph because the
        # student only needs them while they are actually picking.
        card.add_component(make_page_title(
            'What subjects do you do?',
            'Your studies drive the parser, dashboard and exam timetable. '
            'Change them any time in Settings.'))
        card.add_component(Label(
            text='One mathematics study is required, and an English-group '
                 'study is always kept (VCAA rule).',
            role='micro'))
        # The card role neutralises Anvil's per-component margins so that cards
        # control their own rhythm, so the gaps between the three blocks of this
        # card are set explicitly rather than inherited.
        card.add_component(Spacer(height=16))

        try:
            catalog = anvil.server.call('get_subject_catalog')
        except Exception as e:
            # The router re-renders this form for every hash while the user is
            # un-onboarded, so a dead-end here would trap them: always offer a
            # retry and a way out.
            card.add_component(make_empty_state(
                "Couldn't load the subject list",
                str(e),
                'Try again',
                lambda: open_form('Main')))
            card.add_component(self._sign_out_row())
            return

        # The picker is a dumb component: it renders the catalog as toggle pills
        # with live per-group counts and hands back a plain list of names. All
        # VCE rules are checked below and again server-side in set_subjects.
        self._picker = SubjectPicker(catalog)
        card.add_component(self._picker)

        card.add_component(Spacer(height=16))

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
        # because the server can repair it by appending 'English').
        if not selection:
            toast_error("Pick your subjects first.")
            return
        if not any(s in MATHS_GROUP for s in selection):
            toast_error("Select at least one mathematics study — DotPoint "
                        "needs one in every program.")
            return
        if not any(s in ENGLISH_GROUP for s in selection):
            proceed = confirm(
                "You haven't picked an English-group study. Every VCE program "
                "includes one, so 'English' will be added automatically. Continue?")
            if not proceed:
                return

        try:
            settings = anvil.server.call('set_subjects', selection)
        except Exception as e:
            toast_error(str(e))
            return

        # set_subjects returns the saved settings row, so push it straight into
        # the session cache: the router reads it on the very next navigation to
        # decide that onboarding is done, with no second round-trip.
        set_session_settings(settings)
        apply_theme(settings.get('theme'))
        toast("Subjects locked in — welcome to DotPoint!")
        navigate('dashboard')
