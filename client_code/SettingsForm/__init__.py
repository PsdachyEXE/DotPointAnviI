import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""SettingsForm - per-user preferences (theme, reminders, school year/terms, timezone).

Implementation pending - see IMPLEMENTATION_SPEC.md section 3 (SettingsForm).
"""

from anvil import ColumnPanel


class SettingsForm(ColumnPanel):
    def __init__(self, **properties):
        super().__init__(**properties)
        pass
