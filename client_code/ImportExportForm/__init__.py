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
    """The #import-export screen: back everything up, or restore a backup.

    Two cards on one page. Export is a single button that downloads the whole
    account as a JSON file. Import is a file picker with one status line beside
    it, which is the only report the student gets about what happened.

    WHICH REQUIREMENTS IT IMPLEMENTS
      FR18  Export. assessments + notes + user_settings, named
            dotpoint-export-YYYY-MM-DD.json; reminder_logs is left out as
            transient. This form only triggers it and hands the blob to the
            browser - the file is built server-side.
      FR19  Import. The file is validated against the expected schema BEFORE
            anything is written, nothing existing is overwritten, and a title
            that collides with one the student already has is saved under a
            changed name with the student told about it. (FR19 words that
            change as a numeric suffix; the server actually appends the import
            timestamp - '(imported 2026-09-02 14:05)' - which meets the same
            requirement: unique, and visible in the report.)
      NFR03  neither callable takes a user. Export reads the caller's own rows
            and import writes to the caller's own account, so a file exported
            from one account cannot be made to write into another.

    HOW IT IS CONSTRUCTED
      ImportExportForm() - no arguments of its own, no modes; Main._make_form()
      passes none. Both cards are built once in __init__ and never rebuilt;
      only the status line changes after that.

    HOW THE IMPORT DATA ACTUALLY FLOWS
      FileLoader hands over an Anvil Media object -> _file_error() reads its
      bytes IN THE BROWSER and decodes them as UTF-8, purely as a check ->
      confirm() -> the Media object itself (not the decoded text) is passed to
      import_user_data, which decodes and json.loads it server-side -> a
      summary dict comes back -> _import_summary() turns it into one sentence,
      shown beside the picker and toasted.
      The SERVER is the authority on the JSON shape; the checks on this side
      exist only to refuse the obvious cases before a file is uploaded.

    SERVER CALLABLES IT DEPENDS ON
      export_user_data  returns a BlobMedia for anvil.media.download
      import_user_data  returns {'assessments_inserted': int,
                                 'notes_inserted': int, 'renamed': [title,...]}
                        or raises ValueError with a sentence
      get_settings      indirectly, via common.get_session_settings(refresh=
                        True) after an import, because an import can change
                        the stored settings

    WHAT IT HANDS BACK
      Nothing: it is a page, not a dialog. Its lasting effects are the rows the
      server wrote and the refreshed session settings cache.
    """

    def __init__(self, **properties):
        """Build the two cards. No server call is made until the user acts.

        Stashes two controls on self: _loader (the FileLoader, cleared after
        every attempt so the same file can be picked again) and _status (the
        one message line, hidden until there is something to say).
        """
        super().__init__(**properties)
        # The form is the page shell: its own spacing is removed so make_page
        # below owns all the padding, and the top bar can span the window.
        self.spacing_above = 'none'
        self.spacing_below = 'none'

        self.add_component(make_top_bar(active='import-export'))
        body = make_page()
        self.add_component(body)
        body.add_component(make_page_title(
            'Import & export',
            'Back up everything as a JSON file, or restore from one.'))

        # --- Export -------------------------------------------------------
        # Export needs no input and can refuse nothing, so it is one button
        # with no confirmation: the worst it can do is put a file the student
        # did not want in their Downloads folder.
        #
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
        # 'change' is the only event: choosing a file IS the action, so there is
        # no separate Import button to press. That is why the confirmation in
        # _on_file_change matters - it is the student's one chance to back out.
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

        `file` is the Anvil Media object the FileLoader produced: .name is the
        name as it was on disk, .length is its size in bytes, .get_bytes()
        reads it in the browser. Returns a sentence to show the student, or
        None when the file is worth uploading. Never raises.
        """
        # 1. NAME. getattr with a default, not file.name: the loader can hand
        #    over a file the browser gave no name for, and '' then falls into
        #    the "different kind of file" branch with wording that fits.
        name = getattr(file, 'name', '') or ''
        if not name.lower().endswith(IMPORT_EXTENSION):
            # Naming the file back to the student matters when several were
            # picked from the same folder; the fallback covers a file the
            # browser gave no name for.
            return ('Import needs the %s file you downloaded from Export, and '
                    '%s is a different kind of file.'
                    % (IMPORT_EXTENSION,
                       ('"%s"' % name) if name else 'this one'))

        # 2. SIZE. Anvil's Media exposes its byte length; treat an unreported
        #    length as "cannot tell" and let the next check (and the server)
        #    decide. isinstance over a truth test, because 0 is a real length
        #    and `if size:` would skip the empty-file message below.
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

        # 3. ENCODING. Reading the bytes in the browser costs nothing next to
        #    uploading them, and a file that is not text cannot possibly be an
        #    export. Anything at all going wrong here is treated as "not
        #    readable text", because that is what it means to the student
        #    either way. `text` is used for nothing but this check — the upload
        #    below sends the Media object itself, not this decoded string.
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

        `result` is what import_user_data returned, and it holds exactly three
        keys:
          assessments_inserted  int, the number of assessment rows added
          notes_inserted        int, the number of note rows added
          renamed               list of the FINAL titles of the assessments
                                that had to be renamed; its length is the only
                                thing used, the titles themselves are not shown
        Returns one sentence of plain text. Raises nothing.

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
        * The one thing this sentence does NOT report is the settings block.
          import_user_data applies it best-effort, inside its own try/except
          after the transaction has committed, so a file can add every row and
          still leave the stored theme, terms and reminder defaults untouched
          without saying so. The rows are the part that is all-or-nothing.
        """
        # 1. Read the three keys through their own guards. `or []` on renamed
        #    covers both a missing key and an explicit None.
        assessments = self._count(result.get('assessments_inserted'))
        notes = self._count(result.get('notes_inserted'))
        renamed = result.get('renamed') or []

        # 2. Zero of both is a real, successful outcome — an export taken
        #    before anything was added — so it gets its own sentence rather
        #    than "Imported 0 assessments and 0 notes.", which reads as a
        #    failure and would send the student looking for a problem.
        if not assessments and not notes:
            return ('That file was valid but held no assessments or notes, so '
                    'nothing was added.')

        message = 'Imported %s and %s.' % (self._plural(assessments, 'assessment'),
                                           self._plural(notes, 'note'))
        # 3. The renaming is only mentioned when it happened, and the two
        #    wordings exist because "1 assessments" is the kind of detail that
        #    makes a student trust the rest of the sentence less.
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
        """A server-reported count, or 0 when the value is not one.

        bool is tested FIRST and rejected, because bool subclasses int: a
        stray True would otherwise pass the int check and be reported to the
        student as "1 assessment" that was never imported.
        """
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return 0
        return value

    def _plural(self, count, noun):
        """'1 note' / '3 notes' — every noun this screen counts is regular."""
        return '%d %s' % (count, noun if count == 1 else noun + 's')

    # --- handlers ----------------------------------------------------------
    def _on_export_click(self, **event_args):
        """Fetch the export blob and hand it to the browser (FR18).

        The file is built entirely server-side — this only moves it. `media` is
        an Anvil BlobMedia of JSON, already named
        dotpoint-export-YYYY-MM-DD.json using the student's own timezone, so
        the download name is not re-derived here and cannot disagree with it.
        """
        try:
            media = anvil.server.call('export_user_data')
        except Exception as e:
            # A toast, not the status line: the status line belongs to the
            # import card, and a message about the export appearing over there
            # would point at the wrong half of the screen.
            toast_error(friendly_error(
                e, "Couldn't export your data. Please try again."))
            return
        # Only reached with a blob in hand, so the browser is never asked to
        # download nothing.
        anvil.media.download(media)

    def _on_file_change(self, file, **event_args):
        """The whole import, from a chosen file to a reported outcome (FR19).

        `file` is the Anvil Media object the FileLoader produced, or None when
        the loader was cleared (including by this method itself, which is why
        the None case returns rather than reporting anything).

        The order below is deliberate and each step earns its place: check
        locally, ask, upload, report. Returns nothing; every outcome is left in
        the status line, and the loader is cleared on every path so the same
        file can be chosen again after a fix.
        """
        if file is None:
            return
        # 1. Checked BEFORE the confirm dialog: there is no point asking a
        # student to confirm importing a file that was never going to be
        # imported.
        problem = self._file_error(file)
        if problem:
            # The message goes beside the picker rather than into a toast: the
            # student is looking at the control they just used, and a toast in
            # the corner would be the weaker of the two places to put it.
            self._set_status(problem, is_error=True)
            self._loader.clear()
            return
        # 2. Import adds records to a live account, so it is confirmed before a
        # single byte is uploaded; declining clears the loader so the same file
        # can be picked again later. The file's own name is quoted back, because
        # picking the wrong one out of a folder of backups is the mistake this
        # dialog is really guarding against.
        if not confirm('Import "%s"? New records will be added to your account.'
                       % getattr(file, 'name', 'this file')):
            self._loader.clear()
            return
        # 3. Say something before the wait starts. The upload plus the
        #    server-side validation is the longest thing this screen does, and
        #    a file picker that appears to do nothing for two seconds invites a
        #    second click.
        self._set_status('Importing…')
        # 4. The Media object is passed whole; the bytes decoded during the
        #    checks above are thrown away, so the server reads the file itself
        #    rather than trusting anything this side did to it.
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
        # 5. Report. `result or {}` because a server that returned None would
        #    otherwise blow up the summary AFTER the rows were safely written —
        #    the student would see a crash for an import that worked. The same
        #    sentence goes in both places: the status line keeps it readable
        #    next to the picker, the toast makes sure it is noticed.
        msg = self._import_summary(result or {})
        self._set_status(msg)
        toast(msg)
        self._loader.clear()
        # 6. The import may have changed settings server-side (theme, terms,
        # reminder defaults) — refresh the session cache so the router and
        # editor don't keep serving the pre-import copy. Swallowed on failure:
        # the import itself has already succeeded and been reported, and a
        # stale cache is fixed by the next page load, so an error here would
        # contradict a message that is true.
        try:
            settings = get_session_settings(refresh=True)
            apply_theme(settings.get('theme'))
        except Exception:
            pass
