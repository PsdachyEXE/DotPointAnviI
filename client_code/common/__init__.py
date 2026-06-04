import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""Client-side shared helpers.

Slice 1 (§10 step 1) provides the shared top navigation bar used by every
top-level form. Display helpers (format_date_au, urgency_colour, ...) are added
in later slices as their consumers land.

See IMPLEMENTATION_SPEC.md section 0 (Common helpers) and section 3 (Client Forms).
"""

import anvil
import anvil.users
from anvil import FlowPanel, Link, Button, open_form


def _navigate(hash_value):
    """Set the URL hash and re-enter the Main router (spec §4 navigation)."""
    anvil.set_url_hash(hash_value)
    open_form('Main')


def _sign_out():
    anvil.users.logout()
    anvil.set_url_hash('login')
    open_form('Main')


def make_top_bar():
    """Build the shared top navigation bar (spec §3 DashboardForm top bar).

    'DotPoint' is the home link (-> dashboard); Notes / Settings / Import-Export
    route via the Main hash router; Sign out logs out and returns to login.
    """
    bar = FlowPanel()

    title = Link(text='DotPoint', role='heading')
    title.set_event_handler('click', lambda **e: _navigate('dashboard'))
    bar.add_component(title)

    for label, hash_value in (('Notes', 'notes'),
                              ('Settings', 'settings'),
                              ('Import/Export', 'import-export')):
        link = Link(text=label)
        link.set_event_handler('click', lambda h=hash_value, **e: _navigate(h))
        bar.add_component(link)

    sign_out = Button(text='Sign out', role='secondary')
    sign_out.set_event_handler('click', lambda **e: _sign_out())
    bar.add_component(sign_out)

    return bar
