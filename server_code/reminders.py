import anvil.secrets
import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""Background email reminder dispatcher.

Exposes: run_reminder_check (@anvil.server.background_task; scheduled every 30 min),
trigger_reminder_check_now (@anvil.server.callable; dev only).

Implementation pending - see IMPLEMENTATION_SPEC.md section 2 (server_code/reminders.py)
and section 6 (Email).
"""

pass
