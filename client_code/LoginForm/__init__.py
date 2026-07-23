import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""LoginForm - gate the app behind authentication (FR20).

WORKAROUND: Anvil's client-initiated login_with_form / signup_with_form raise
"PermissionDenied: Cannot access this table from server code" on the users table
(a Users-service<->table binding issue surfaced after a schema resync). So this
form uses a custom email/password dialog that calls trusted server-module
callables (notes.sign_in_with_email / notes.create_account), which run with this
app's full users-table access and sidestep that path. Revert to
login_with_form/signup_with_form once the binding is fixed (Anvil support / table
permission re-apply).

Layout note: this theme defines no custom roles, so styling uses direct Label
properties rather than role='display-1'.

See IMPLEMENTATION_SPEC.md section 3 (LoginForm) and section 5 (Authentication).
"""

import anvil
import anvil.server
from anvil import (
    ColumnPanel, Label, Button, Spacer, TextBox, Notification, alert, open_form,
)


class LoginForm(ColumnPanel):
    def __init__(self, **properties):
        super().__init__(**properties)
        self.spacing_above = 'none'
        self.spacing_below = 'none'

        self.add_component(Spacer(height=72))

        card = ColumnPanel(role='authcard')
        card.add_component(Label(text='DotPoint', align='center',
                                 font_size=40, bold=True))
        card.add_component(Label(text='Assessment Tracker', align='center',
                                 font_size=16, foreground='#6b7280'))
        card.add_component(Label(
            text='Type "Methods SAC2 due Friday week 5 worth 25%" and it\'s tracked.',
            align='center', font_size=12, italic=True, foreground='#9aa0a6'))
        card.add_component(Spacer(height=20))

        sign_in = Button(text='Sign in', role='primary', align='center')
        sign_in.set_event_handler('click', self._on_sign_in_click)
        card.add_component(sign_in)

        card.add_component(Spacer(height=6))

        sign_up = Button(text='Create an account', role='secondary', align='center')
        sign_up.set_event_handler('click', self._on_sign_up_click)
        card.add_component(sign_up)

        self.add_component(card)

    # --- credential prompt -------------------------------------------------
    def _prompt_credentials(self, title, action_label):
        """Show an email + password dialog. Returns (email, password) or None."""
        panel = ColumnPanel()
        panel.add_component(Label(text='Email'))
        email_box = TextBox(placeholder='email@example.com')
        panel.add_component(email_box)
        panel.add_component(Label(text='Password'))
        password_box = TextBox(placeholder='password', hide_text=True)
        panel.add_component(password_box)

        confirmed = alert(panel, title=title,
                          buttons=[(action_label, True), ('Cancel', False)])
        if not confirmed:
            return None
        return (email_box.text or '').strip(), (password_box.text or '')

    # --- handlers ----------------------------------------------------------
    def _on_sign_in_click(self, **event_args):
        self._authenticate('sign_in_with_email', 'Sign in', 'Sign in')

    def _on_sign_up_click(self, **event_args):
        self._authenticate('create_account', 'Create an account', 'Create account')

    def _authenticate(self, server_fn, title, action_label):
        creds = self._prompt_credentials(title, action_label)
        if creds is None:
            return  # cancelled
        email, password = creds
        if not email or not password:
            Notification("Email and password are required.", style='danger').show()
            return
        try:
            anvil.server.call(server_fn, email, password)
        except Exception as e:
            Notification(str(e), style='danger').show()
            return
        anvil.set_url_hash('dashboard')
        open_form('Main')
