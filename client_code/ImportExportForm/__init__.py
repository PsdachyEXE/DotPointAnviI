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

CHOSEN FILES ARE CHECKED BEFORE THEY ARE UPLOADED (SAT criterion 7.3). This is
the app's widest input surface: the file arrives from the student's own disk, so
its name, its size and even its character encoding are all outside the app's
control. `file_types='.json'` on the loader below only sets the DEFAULT filter in
the operating system's picker — the student can switch it to "All files" or drag
anything in — so it is a convenience, never a check. _file_error() below is the
check, and it runs before a single byte is sent.

See IMPLEMENTATION_SPEC.md section 3 (ImportExportForm) and section 14.
"""

import anvil
import anvil.server
import anvil.media
from anvil import ColumnPanel, Label, Button, FileLoader, confirm

from ..common import (
    make_top_bar, make_page, make_page_title, make_card, make_section_header,
    make_row, toast, toast_error, get_session_settings, apply_theme,
    friendly_error,
)

# --- what this screen will accept -------------------------------------------
# An export of a full VCE year is a few hundred kilobytes of text, so this cap is
# generous by two orders of magnitude and still refuses the case it exists for: a
# 50 MB video picked by mistake, which would otherwise be uploaded in full before
# the server got to look at the first byte and reject it.
MAX_IMPORT_BYTES = 5 * 1024 * 1024
MAX_IMPORT_SIZE_TEXT = '5 MB'
IMPORT_EXTENSION = '.json'


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
        self._loader = FileLoader(file_types=IMPORT_EXTENSION)
        self._loader.set_event_handler('change', self._on_file_change)
        self._status = Label(text='', role='caption', visible=False)
        body.add_component(make_card(
            make_section_header('Import'),
            Label(text='Choose a %s file downloaded from Export, up to %s. '
                       'Nothing is saved unless the whole file is valid; '
                       'assessments with a title you already use are renamed, '
                       'not overwritten.'
                       % (IMPORT_EXTENSION, MAX_IMPORT_SIZE_TEXT),
                  role='caption'),
            make_row(self._loader, self._status),
        ))

    # --- helpers -----------------------------------------------------------
    def _set_status(self, text, is_error=False):
        """Show a status message, or hide the line entirely when there is none.

        Hiding rather than blanking keeps the card from rendering an empty row
        where a message used to be.

        `is_error` repaints the line in the app's error colour ('fielderror' is
        the same role make_field() uses for a per-field message). Without it a
        refusal would appear in the same grey as "Importing…", and this line sits
        beside the file picker — which makes it the "message beside the offending
        field" this screen has instead of a labelled input.
        """
        self._status.text = text or ''
        self._status.visible = bool(text)
        self._status.role = 'fielderror' if is_error else 'caption'

    # --- pre-upload checks -------------------------------------------------
    def _file_error(self, file):
        """Return a message for a file that should not be uploaded, or None.

        Three checks, cheapest first, each with its own sentence — "that file is
        not right" tells a student nothing they can act on:

          NAME   the wrong file entirely (a .pdf, a photo). Named first because
                 it is the likeliest mistake and the only one that can be
                 reported without touching the file at all.
          SIZE   a video or a disk image picked by mistake. Checked before the
                 bytes are read, because reading them is the expensive part.
          TEXT   an export file is UTF-8 JSON; a binary file that happens to be
                 named .json fails here.

        The server remains the authority on the JSON SHAPE — whether the file has
        the right keys, and whether every row inside it is valid. All this does is
        catch the obvious cases while they are still cheap to catch.
        """
        name = getattr(file, 'name', '') or ''
        if not name.lower().endswith(IMPORT_EXTENSION):
            # Naming the file back to the student matters when several were
            # picked from the same folder; the fallback covers a file the
            # browser gave no name for.
            return ('Import needs the %s file you downloaded from Export, and '
                    '%s is a different kind of file.'
                    % (IMPORT_EXTENSION,
                       ('"%s"' % name) if name else 'this one'))

        # Anvil's Media exposes its byte length; treat an unreported length as
        # "cannot tell" and let the next check (and the server) decide.
        size = getattr(file, 'length', None)
        if isinstance(size, (int, float)):
            if size <= 0:
                return 'That file is empty. Choose the file downloaded from Export.'
            if size > MAX_IMPORT_BYTES:
                # The exact size is deliberately not quoted: a file a few bytes
                # over the cap would round to "5.0 MB", which reads as a
                # contradiction of a 5 MB limit.
                return ('That file is too large to import — the limit is %s. An '
                        'export of your data is only a few hundred kilobytes, so '
                        'check you picked the right file.' % MAX_IMPORT_SIZE_TEXT)

        # Encoding. Reading the bytes in the browser costs nothing next to
        # uploading them, and a file that is not text cannot possibly be an
        # export. Anything at all going wrong here is treated as "not readable
        # text", because that is what it means to the student either way.
        try:
            text = file.get_bytes().decode('utf-8')
        except Exception:
            return ('That file is not readable text, so it cannot be an export '
                    'file. Choose the .json file downloaded from Export.')
        if not text.strip():
            return 'That file is empty. Choose the file downloaded from Export.'
        return None

    def _import_summary(self, result):
        """Describe honestly what the import actually did.

        Written carefully because this line is the ONLY report the student gets:

        * The counts are the numbers of rows written, and import_user_data is
          all-or-nothing (one Transaction, every row validated first), so a
          partial import cannot happen — but a VALID file that simply held
          nothing still returns zeros, and "Imported 0 assessment(s)" reads like
          a failure. That case gets its own sentence.
        * 'renamed' holds ASSESSMENT titles only — notes are never renamed — so
          the old wording ("%d had duplicate titles") was ambiguous about which
          records it meant.
        * Counts are read defensively: this line must not raise and blank the
          status after a successful import.
        """
        assessments = self._count(result.get('assessments_inserted'))
        notes = self._count(result.get('notes_inserted'))
        renamed = result.get('renamed') or []

        if not assessments and not notes:
            return ('That file was valid but held no assessments or notes, so '
                    'nothing was added.')

        message = 'Imported %s and %s.' % (self._plural(assessments, 'assessment'),
                                           self._plural(notes, 'note'))
        if renamed:
            if len(renamed) == 1:
                message += (' One assessment already had that title, so it was '
                            'saved with the import date on the end.')
            else:
                message += (' %d assessments already had those titles, so they '
                            'were saved with the import date on the end.'
                            % len(renamed))
        return message

    def _count(self, value):
        """A server-reported count, or 0 when the value is not one."""
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return 0
        return value

    def _plural(self, count, noun):
        """'1 note' / '3 notes' — every noun this screen counts is regular."""
        return '%d %s' % (count, noun if count == 1 else noun + 's')

    # --- handlers ----------------------------------------------------------
    def _on_export_click(self, **event_args):
        try:
            media = anvil.server.call('export_user_data')
        except Exception as e:
            toast_error(friendly_error(
                e, "Couldn't export your data. Please try again."))
            return
        anvil.media.download(media)

    def _on_file_change(self, file, **event_args):
        if file is None:
            return
        # Checked BEFORE the confirm dialog: there is no point asking a student to
        # confirm importing a file that was never going to be imported.
        problem = self._file_error(file)
        if problem:
            # The message goes beside the picker rather than into a toast: the
            # student is looking at the control they just used, and a toast in
            # the corner would be the weaker of the two places to put it.
            self._set_status(problem, is_error=True)
            self._loader.clear()
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
            # "nothing was saved" is a promise the server keeps: every row is
            # validated before the transaction opens, so a raise here means the
            # account is untouched. Said in both places — the line beside the
            # picker so it stays readable, and a toast because a failed server
            # call is the one case that may be a dropped connection rather than
            # anything about the file.
            message = 'Import failed — nothing was saved. %s' % friendly_error(
                e, 'That file could not be imported. Choose the .json file '
                   'downloaded from Export.')
            self._set_status(message, is_error=True)
            toast_error(message)
            self._loader.clear()
            return
        msg = self._import_summary(result or {})
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
