import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""ImportExportForm - JSON export and import (FR18, FR19).

Export downloads a dotpoint-export-YYYY-MM-DD.json blob of all the user's
assessments, notes and settings. Import accepts such a file, validates it
server-side (nothing is written unless the whole file is valid), remaps linked
notes, suffixes title collisions, and reports a summary.

Layout: page title, then one card per direction (Export, then Import), because
the two flows are independent and a student should never have to read the
import rules to find the export button. Each card is opened by a section header
and carries a single caption line — the long paragraphs the old screen used
were re-reading the same warning the confirm() dialog already gives. The import
status line sits beside the file picker and stays hidden until there is
something to say, so the card does not reserve a blank row.

Every colour and size comes from the roles in the shared stylesheet, so this
screen follows the light/dark theme.

See IMPLEMENTATION_SPEC.md section 3 (ImportExportForm) and section 14.
"""

import anvil
import anvil.server
import anvil.media
from anvil import ColumnPanel, Label, Button, FileLoader, confirm

from ..common import (
    make_top_bar, make_page, make_page_title, make_card, make_section_header,
    make_row, toast, toast_error, get_session_settings, apply_theme,
)


class ImportExportForm(ColumnPanel):
    def __init__(self, **properties):
        super().__init__(**properties)
        self.spacing_above = 'none'
        self.spacing_below = 'none'

        self.add_component(make_top_bar(active='import-export'))
        body = make_page()
        self.add_component(body)
        body.add_component(make_page_title(
            'Import & export',
            'Back up everything as a JSON file, or restore from one.'))

        # --- Export -------------------------------------------------------
        # The button lives in a row rather than straight in the card so it
        # keeps its natural width instead of stretching the whole card.
        export_btn = Button(text='Export my data (JSON)', role='primary')
        export_btn.set_event_handler('click', self._on_export_click)
        body.add_component(make_card(
            make_section_header('Export'),
            Label(text='Downloads every assessment, note and setting as one '
                       'JSON file.', role='caption'),
            make_row(export_btn),
        ))

        # --- Import -------------------------------------------------------
        # The loader and the status line share a row so the message appears
        # next to the control that produced it, and the picker stays put
        # instead of jumping when the status text appears or clears.
        self._loader = FileLoader(file_types='.json')
        self._loader.set_event_handler('change', self._on_file_change)
        self._status = Label(text='', role='caption', visible=False)
        body.add_component(make_card(
            make_section_header('Import'),
            Label(text='Nothing is saved unless the whole file is valid; '
                       'duplicate titles are renamed, not overwritten.',
                  role='caption'),
            make_row(self._loader, self._status),
        ))

    # --- helpers -----------------------------------------------------------
    def _set_status(self, text):
        """Show a status message, or hide the line entirely when there is none.

        Hiding rather than blanking keeps the card from rendering an empty row
        where a message used to be.
        """
        self._status.text = text or ''
        self._status.visible = bool(text)

    # --- handlers ----------------------------------------------------------
    def _on_export_click(self, **event_args):
        try:
            media = anvil.server.call('export_user_data')
        except Exception as e:
            toast_error("Couldn't export: %s" % e)
            return
        anvil.media.download(media)

    def _on_file_change(self, file, **event_args):
        if file is None:
            return
        # Import adds records to a live account, so it is confirmed before a
        # single byte is uploaded; declining clears the loader so the same file
        # can be picked again later.
        if not confirm('Import "%s"? New records will be added to your account.'
                       % getattr(file, 'name', 'this file')):
            self._loader.clear()
            return
        self._set_status('Importing…')
        try:
            result = anvil.server.call('import_user_data', file)
        except Exception as e:
            self._set_status('')
            toast_error("Import failed — nothing was saved. %s" % e)
            self._loader.clear()
            return
        renamed = result.get('renamed') or []
        msg = 'Imported %d assessment(s) and %d note(s).' % (
            result.get('assessments_inserted', 0), result.get('notes_inserted', 0))
        if renamed:
            msg += ' %d had duplicate titles and were renamed.' % len(renamed)
        self._set_status(msg)
        toast(msg)
        self._loader.clear()
        # The import may have changed settings server-side (theme, terms,
        # reminder defaults) — refresh the session cache so the router and
        # editor don't keep serving the pre-import copy.
        try:
            settings = get_session_settings(refresh=True)
            apply_theme(settings.get('theme'))
        except Exception:
            pass
