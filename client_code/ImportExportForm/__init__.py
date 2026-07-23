import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""ImportExportForm - JSON export and import (FR18, FR19).

Export downloads a dotpoint-export-YYYY-MM-DD.json blob of all the user's
assessments, notes and settings. Import accepts such a file, validates it
server-side (nothing is written unless the whole file is valid), remaps linked
notes, suffixes title collisions, and reports a summary.

See IMPLEMENTATION_SPEC.md section 3 (ImportExportForm).
"""

import anvil
import anvil.server
import anvil.media
from anvil import (
    ColumnPanel, Label, Button, FileLoader, Notification, Spacer, confirm,
)

from ..common import make_top_bar, get_session_settings, apply_theme


class ImportExportForm(ColumnPanel):
    def __init__(self, **properties):
        super().__init__(**properties)
        self.spacing_above = 'none'
        self.spacing_below = 'none'

        self.add_component(make_top_bar())
        body = ColumnPanel()
        self.add_component(body)

        # --- Export ---
        body.add_component(Label(text='Export', font_size=18, bold=True))
        body.add_component(Label(
            text='Download all your assessments, notes and settings as a JSON file.',
            foreground='#9aa0a6'))
        export_btn = Button(text='Export my data (JSON)', role='primary')
        export_btn.set_event_handler('click', self._on_export_click)
        body.add_component(export_btn)

        body.add_component(Spacer(height=16))

        # --- Import ---
        body.add_component(Label(text='Import', font_size=18, bold=True))
        body.add_component(Label(
            text='Load a DotPoint export file. Nothing is saved unless the whole '
                 'file is valid; duplicate titles are renamed, not overwritten.',
            foreground='#9aa0a6'))
        self._loader = FileLoader(file_types='.json')
        self._loader.set_event_handler('change', self._on_file_change)
        body.add_component(self._loader)
        self._status = Label(text='', foreground='#9aa0a6')
        body.add_component(self._status)

    # --- handlers ----------------------------------------------------------
    def _on_export_click(self, **event_args):
        try:
            media = anvil.server.call('export_user_data')
        except Exception as e:
            Notification("Couldn't export: %s" % e, style='danger', timeout=4).show()
            return
        anvil.media.download(media)

    def _on_file_change(self, file, **event_args):
        if file is None:
            return
        if not confirm('Import "%s"? New records will be added to your account.'
                       % getattr(file, 'name', 'this file')):
            self._loader.clear()
            return
        self._status.text = 'Importing…'
        try:
            result = anvil.server.call('import_user_data', file)
        except Exception as e:
            self._status.text = ''
            Notification("Import failed — nothing was saved. %s" % e, style='danger', timeout=4).show()
            self._loader.clear()
            return
        renamed = result.get('renamed') or []
        msg = 'Imported %d assessment(s) and %d note(s).' % (
            result.get('assessments_inserted', 0), result.get('notes_inserted', 0))
        if renamed:
            msg += ' %d had duplicate titles and were renamed.' % len(renamed)
        self._status.text = msg
        Notification(msg, style='success', timeout=4).show()
        self._loader.clear()
        # The import may have changed settings server-side (theme, terms,
        # reminder defaults) — refresh the session cache so the router and
        # editor don't keep serving the pre-import copy.
        try:
            settings = get_session_settings(refresh=True)
            apply_theme(settings.get('theme'))
        except Exception:
            pass
