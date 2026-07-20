import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""Main - hash router and app startup form (spec §4).

A thin custom hash router. On instantiation it reads the URL hash, checks the
auth state, and renders the matching top-level form *inside itself* via
add_component. Logged-out users are forced to LoginForm; logged-in users hitting
'#login' are bounced to the dashboard.

Child forms navigate by calling anvil.set_url_hash(...) then open_form('Main'),
which re-enters this router (see client_code/common.make_top_bar).

See IMPLEMENTATION_SPEC.md section 4 (Routing) and section 5 (Authentication).
"""

import anvil
import anvil.users
from anvil import ColumnPanel

# hash -> form name (spec §4). Forms not yet built fall through to the default
# (DashboardForm); their hashes are added to this map as their slices land.
_ROUTES = {
    '': 'DashboardForm',
    'dashboard': 'DashboardForm',
    'login': 'LoginForm',
    'settings': 'SettingsForm',
    'import-export': 'ImportExportForm',
    'notes': 'NotesForm',
}


class Main(ColumnPanel):
    def __init__(self, **properties):
        super().__init__(**properties)
        self.spacing_above = 'none'
        self.spacing_below = 'none'
        self._route_to_current()

    def _route_to_current(self):
        hash_value = anvil.get_url_hash()
        # get_url_hash() returns a dict for query-style hashes; this app uses
        # only plain string routes, so coerce anything else to ''.
        if not isinstance(hash_value, str):
            hash_value = ''

        user = anvil.users.get_user(allow_remembered=True)

        if user is None:
            if hash_value != 'login':
                anvil.set_url_hash('login')
            self._render('LoginForm')
            return

        target = _ROUTES.get(hash_value, 'DashboardForm')
        if target == 'LoginForm':
            # Already authenticated; don't show the login screen.
            anvil.set_url_hash('dashboard')
            target = 'DashboardForm'
        self._render(target)

    def _render(self, form_name):
        """Replace the router's content with a fresh instance of `form_name`.

        Forms are imported lazily so that routes whose forms don't exist yet
        (built in later slices) never break the router at import time.
        """
        self.clear()
        self.add_component(self._make_form(form_name))

    def _make_form(self, form_name):
        if form_name == 'LoginForm':
            from ..LoginForm import LoginForm
            return LoginForm()
        if form_name == 'SettingsForm':
            from ..SettingsForm import SettingsForm
            return SettingsForm()
        if form_name == 'ImportExportForm':
            from ..ImportExportForm import ImportExportForm
            return ImportExportForm()
        if form_name == 'NotesForm':
            from ..NotesForm import NotesForm
            return NotesForm()
        # default / 'dashboard'
        from ..DashboardForm import DashboardForm
        return DashboardForm()
