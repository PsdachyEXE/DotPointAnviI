import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""LoginForm - gate the app behind Anvil Users authentication (FR20).

Two explicit paths (both use Anvil's built-in forms):
  - "Sign in"           -> anvil.users.login_with_form(...)
  - "Create an account" -> anvil.users.signup_with_form(...)

A dedicated signup button is used rather than relying on login_with_form's
show_signup_option link, which does not render in this app even though the Users
service has allow_signups: true. Both paths return a logged-in user on success.

Layout note: this theme defines no custom roles, so title styling uses direct
Label properties (font_size/bold/align) rather than role='display-1'.

See IMPLEMENTATION_SPEC.md section 3 (LoginForm) and section 5 (Authentication).
"""

import anvil
import anvil.server
import anvil.users
from anvil import ColumnPanel, Label, Button, Spacer, Notification, open_form


class LoginForm(ColumnPanel):
    def __init__(self, **properties):
        super().__init__(**properties)
        self.spacing_above = 'none'
        self.spacing_below = 'none'

        self.add_component(Spacer(height=64))
        self.add_component(Label(text='DotPoint', align='center',
                                 font_size=44, bold=True))
        self.add_component(Label(text='Assessment Tracker', align='center',
                                 font_size=18, foreground='#777777'))
        self.add_component(Spacer(height=28))

        sign_in = Button(text='Sign in', role='primary', align='center')
        sign_in.set_event_handler('click', self._on_sign_in_click)
        self.add_component(sign_in)

        self.add_component(Spacer(height=8))

        sign_up = Button(text='Create an account', role='secondary', align='center')
        sign_up.set_event_handler('click', self._on_sign_up_click)
        self.add_component(sign_up)

    def _on_sign_in_click(self, **event_args):
        user = anvil.users.login_with_form(allow_remembered=True, allow_cancel=True)
        self._after_auth(user)

    def _on_sign_up_click(self, **event_args):
        user = anvil.users.signup_with_form(allow_cancel=True)
        self._after_auth(user)

    def _after_auth(self, user):
        if user is None:
            return  # dialog cancelled

        # First call lazily creates the user_settings row via _get_or_create_settings.
        try:
            anvil.server.call('get_settings')
        except Exception as e:
            Notification("Couldn't load your settings: %s" % e, style='warning').show()

        anvil.set_url_hash('dashboard')
        open_form('Main')
