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
"""

import anvil
import anvil.server
from anvil import (
    ColumnPanel, Label, Button, Link, Spacer, Notification, confirm, open_form,
)

from ..common import (
    SubjectPicker, set_session_settings, apply_theme, _sign_out,
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

        card = ColumnPanel(role='card')
        card.add_component(Label(text='What subjects do you do?',
                                 font_size=26, bold=True))
        card.add_component(Label(
            text='Pick your VCE studies — DotPoint uses them to tailor the '
                 'parser, dashboard and exam timetable. You can change them '
                 'later in Settings.',
            foreground='#6b7280'))
        card.add_component(Label(
            text='Every VCE program includes an English-group study (VCAA '
                 'rule), and DotPoint also asks for one mathematics study.',
            font_size=12, italic=True, foreground='#9aa0a6'))
        card.add_component(Spacer(height=8))

        try:
            catalog = anvil.server.call('get_subject_catalog')
        except Exception as e:
            # The router re-renders this form for every hash while the user is
            # un-onboarded, so a dead-end here would trap them: always offer a
            # retry and a way out.
            card.add_component(Label(
                text="Couldn't load the subject list: %s" % e,
                foreground='#d64550'))
            retry = Button(text='Try again', role='primary')
            retry.set_event_handler('click', lambda **ev: open_form('Main'))
            card.add_component(retry)
            sign_out = Link(text='Sign out', foreground='#6b7280')
            sign_out.set_event_handler('click', lambda **ev: _sign_out())
            card.add_component(sign_out)
            self.add_component(card)
            return

        self._picker = SubjectPicker(catalog)
        card.add_component(self._picker)

        card.add_component(Spacer(height=12))
        confirm_btn = Button(text='Lock in my subjects', role='primary')
        confirm_btn.set_event_handler('click', self._on_confirm)
        card.add_component(confirm_btn)

        sign_out = Link(text='Sign out', foreground='#6b7280')
        sign_out.set_event_handler('click', lambda **e: _sign_out())
        card.add_component(sign_out)

        self.add_component(card)

    def _on_confirm(self, **event_args):
        selection = self._picker.get_selection()

        if not selection:
            Notification("Pick your subjects first.", style='danger', timeout=4).show()
            return
        if not any(s in MATHS_GROUP for s in selection):
            Notification("Select at least one mathematics study — DotPoint "
                         "needs one in every program.", style='danger', timeout=6).show()
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
            Notification(str(e), style='danger', timeout=6).show()
            return

        set_session_settings(settings)
        apply_theme(settings.get('theme'))
        Notification("Subjects locked in — welcome to DotPoint!",
                     style='success', timeout=4).show()
        anvil.set_url_hash('dashboard')
        open_form('Main')
