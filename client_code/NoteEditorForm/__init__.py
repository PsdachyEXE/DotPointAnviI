import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""NoteEditorForm - create / edit a single note (FR10).

Opened as an alert(..., large=True) from NotesForm, so this form is dialog
content only: no top bar and no make_page() wrapper, because the alert already
supplies the surface and the centring. Layout is one vertical stack of
make_field() groups (Title, Content, Tags) so every label/control pair lines up
on the same left edge, then the pin checkbox, then a Cancel/Save row.

Tags are edited in place rather than in a sub-dialog: a text box + Add button,
with the current tags rendered underneath as chips that each carry their own
remove button.

Content stays a plain TextArea with no markdown rendering. This header used to
call that an "SRS plain-text constraint", which is the wrong way round: FR10 in
SAT 3_SRS2026 asks for MARKDOWN notes, and it is the design document (§4.2.2)
that specifies plain text only. Plain text is what this build implements - the
decision is recorded as discrepancy 6 in docs/DISCREPANCIES.md, where the doc
governs because it also simplifies substring search and the export round trip
(FR18/FR19). Existing markdown survives as raw text either way. The hint under
the box says so plainly, so a student is never left typing **bold** and
wondering why it stays literal.

Save raises 'x-close-alert' with the note id; Cancel returns None. (The 300ms
edit-mode autosave in the spec is deferred; Save is explicit for now.)

See IMPLEMENTATION_SPEC.md section 3 (NoteEditorForm) and section 14 (Design
system) - all colour and type here comes from roles, never from Python.
"""

import anvil
import anvil.server
from anvil import ColumnPanel, TextBox, TextArea, CheckBox, Button

from ..common import (
    make_chip, make_divider, make_field, make_page_title, make_row,
    toast, toast_error, set_field_error, clear_field_errors, friendly_error,
)

# --- field bounds (mirrors of server_code/_constants.py) ---------------------
# The client cannot import server_code, so the four bounds this form can check
# before uploading are restated here. The server re-checks all of them and
# remains the authority; these exist so a 30,000-character paste is refused in
# the browser instead of after the round trip, and so the student is told the
# same limit either way. Keep in step with _constants.MAX_TITLE_LENGTH,
# MAX_NOTE_CONTENT_LENGTH, MAX_TAG_LENGTH and MAX_TAGS_PER_NOTE.
MAX_TITLE_LENGTH = 200
MAX_NOTE_CONTENT_LENGTH = 20000
MAX_TAG_LENGTH = 40
MAX_TAGS_PER_NOTE = 20


class NoteEditorForm(ColumnPanel):
    def __init__(self, mode='create', note_id=None, **properties):
        super().__init__(**properties)
        self.spacing_above = 'none'
        self.spacing_below = 'none'

        self._mode = mode
        self._note_id = note_id
        self._tags = []

        # One heading for the dialog; 'create' and 'edit' share this whole form
        # and differ only in this text and in which server call Save makes.
        # Built by the kit's make_page_title so this dialog's heading is the same
        # component every other screen uses, rather than a hand-rolled Label.
        self.add_component(make_page_title('New note' if mode == 'create'
                                           else 'Edit note'))

        # The three field wrappers are kept on self, not just added and forgotten,
        # because _validate() writes its messages into them (make_field gives each
        # one a hidden error_label). Title is the only one marked required — it is
        # the only field the server refuses to accept blank.
        self._title_tb = TextBox()
        self._title_field = make_field('Title', self._title_tb, required=True)
        self.add_component(self._title_field)

        # 260px, not a role: a note body needs room to write a paragraph in (sizing, not styling).
        self._content_ta = TextArea(height='260px')
        self._content_field = make_field(
            'Content', self._content_ta,
            hint='Plain text — markdown is not rendered.')
        self.add_component(self._content_field)

        # The tag editor is one field group. make_field lays its parts out as
        # caption -> component -> hint, so the add-row AND the chips have to go
        # in together as the component; appending the chips to the group
        # afterwards would drop them below the hint, away from the box that
        # creates them.
        self._tag_tb = TextBox(placeholder='add a tag')
        self._tag_tb.set_event_handler('pressed_enter', self._on_add_tag)
        add_tag_btn = Button(text='Add', role='secondary')
        add_tag_btn.set_event_handler('click', self._on_add_tag)
        self._tag_pills = make_row()
        tag_editor = ColumnPanel()
        tag_editor.add_component(make_row(self._tag_tb, add_tag_btn))
        tag_editor.add_component(self._tag_pills)
        self._tags_field = make_field(
            'Tags', tag_editor,
            hint='Tags are how notes are filtered on the Notes page. '
                 'Up to %d, each %d characters or fewer.'
                 % (MAX_TAGS_PER_NOTE, MAX_TAG_LENGTH))
        self.add_component(self._tags_field)

        # Left as a plain labelled checkbox: the label *is* the question, so
        # wrapping it in a make_field caption would just say it twice.
        self._pin_cb = CheckBox(text='Pin this note')
        self.add_component(self._pin_cb)

        # --- footer ---
        # The divider closes off the field stack above, so the two dialog-level
        # buttons read as the footer and not as one more field. Same pattern as
        # AssessmentEditorForm.
        self.add_component(make_divider())
        # Cancel first, Save last, so the confirming action sits on the right
        # where the eye finishes - and Save carries the only primary role.
        cancel_btn = Button(text='Cancel', role='secondary')
        cancel_btn.set_event_handler('click', self._on_cancel_click)
        save_btn = Button(text='Save', role='primary')
        save_btn.set_event_handler('click', self._on_save_click)
        self.add_component(make_row(cancel_btn, save_btn))

        # Load before the first render so an edited note's tags are drawn once,
        # with data, instead of drawn empty and then redrawn.
        if mode == 'edit' and note_id:
            self._load()
        self._render_tags()

    # --- data --------------------------------------------------------------
    def _load(self):
        try:
            notes = anvil.server.call('search_notes')
        except Exception as e:
            toast_error(friendly_error(
                e, "Couldn't load that note. Close this and try again."))
            return
        note = next((n for n in notes if n['id'] == self._note_id), None)
        if note is None:
            toast_error("Note not found.")
            return
        self._title_tb.text = note.get('title') or ''
        self._content_ta.text = note.get('content') or ''
        self._tags = list(note.get('tags') or [])
        self._pin_cb.checked = bool(note.get('is_pinned'))

    def _render_tags(self):
        """Redraw the chip list from self._tags (the single source of truth)."""
        self._tag_pills.clear()
        for t in self._tags:
            remove_tag_btn = Button(text='x', role='iconbtn')
            # tag=t is a default argument, not a closure over t: without it every
            # handler would capture the same final loop value and remove the
            # wrong tag.
            remove_tag_btn.set_event_handler(
                'click', lambda tag=t, **e: self._remove_tag(tag))
            self._tag_pills.add_component(
                make_row(make_chip('#%s' % t), remove_tag_btn))

    def _remove_tag(self, tag):
        self._tags = [t for t in self._tags if t != tag]
        # Removing a tag can be the fix for "too many tags", so clear that
        # message rather than leave it contradicting what is now on screen.
        set_field_error(self._tags_field, None)
        self._render_tags()

    # --- validation --------------------------------------------------------
    # Client-side checks are a fast, friendly FIRST pass; notes._validate_note_fields
    # runs the same rules server-side and stays the authority. The wording is copied
    # from there deliberately, so a student who trips a rule in the browser and the
    # same rule on the server is told the same thing twice, not two different things.

    def _validate(self):
        """Check the fields on SUBMIT; show any message beside its own box.

        Returns True when the note is safe to send. Deliberately not run on every
        keystroke — "Title is required." appearing while the student is still
        typing the first letter is worse than nothing.
        """
        clear_field_errors(self._title_field, self._content_field,
                           self._tags_field)

        title = (self._title_tb.text or '').strip()
        # Measured on the stripped text because that is what the server stores
        # and therefore what it measures.
        content = (self._content_ta.text or '').strip()

        first_bad_field = None

        if not title:
            set_field_error(self._title_field, 'Title is required.')
            first_bad_field = self._title_field
        elif len(title) > MAX_TITLE_LENGTH:
            set_field_error(self._title_field,
                            'Title is too long — keep it to %d characters or '
                            'fewer (currently %d).'
                            % (MAX_TITLE_LENGTH, len(title)))
            first_bad_field = self._title_field

        if len(content) > MAX_NOTE_CONTENT_LENGTH:
            set_field_error(self._content_field,
                            'Content is too long — keep it to %d characters or '
                            'fewer (currently %d).'
                            % (MAX_NOTE_CONTENT_LENGTH, len(content)))
            first_bad_field = first_bad_field or self._content_field

        # Tags are guarded as they are added, so this is the backstop for a note
        # loaded from a row that already breaks the rule.
        tag_message = self._tag_list_error(self._tags)
        if tag_message:
            set_field_error(self._tags_field, tag_message)
            first_bad_field = first_bad_field or self._tags_field

        if first_bad_field is None:
            return True
        # The dialog is tall enough to scroll, so put the cursor in the first
        # offending box: the message is no use if it is below the fold.
        try:
            first_bad_field.input_component.focus()
        except Exception:
            pass  # focus is a courtesy, never a reason to block the save
        return False

    def _tag_list_error(self, tags):
        """Return the message for an unusable set of tags, or None."""
        for tag in tags:
            if len(tag) > MAX_TAG_LENGTH:
                return ('Tag is too long — keep it to %d characters or fewer '
                        '(currently %d).' % (MAX_TAG_LENGTH, len(tag)))
        if len(tags) > MAX_TAGS_PER_NOTE:
            return ('A note can have at most %d tags (this one has %d).'
                    % (MAX_TAGS_PER_NOTE, len(tags)))
        return None

    # --- handlers ----------------------------------------------------------
    def _on_add_tag(self, **event_args):
        """Add the typed tag, or say why it was not added.

        Adding a tag IS a submit for this sub-field, so it is the right moment to
        check one. Silently dropping a too-long or duplicate tag (which is what
        this did before) looks exactly like a broken Add button.
        """
        set_field_error(self._tags_field, None)
        tag = (self._tag_tb.text or '').strip()
        if not tag:
            return  # an empty box is a mis-click, not an error worth a message

        if len(tag) > MAX_TAG_LENGTH:
            set_field_error(self._tags_field,
                            'Tag is too long — keep it to %d characters or '
                            'fewer (currently %d).'
                            % (MAX_TAG_LENGTH, len(tag)))
            return
        # Case-insensitive, matching the server's de-duplication rule.
        if tag.lower() in [t.lower() for t in self._tags]:
            set_field_error(self._tags_field,
                            'This note already has the tag "%s".' % tag)
            return
        if len(self._tags) >= MAX_TAGS_PER_NOTE:
            set_field_error(self._tags_field,
                            'A note can have at most %d tags. Remove one before '
                            'adding another.' % MAX_TAGS_PER_NOTE)
            return

        self._tags.append(tag)
        self._tag_tb.text = ''
        self._render_tags()

    def _on_save_click(self, **event_args):
        if not self._validate():
            return
        record = {
            'title': (self._title_tb.text or '').strip(),
            'content': self._content_ta.text or '',
            'tags': list(self._tags),
            'is_pinned': bool(self._pin_cb.checked),
        }
        try:
            if self._mode == 'edit':
                anvil.server.call('update_note', self._note_id, record)
                result_id = self._note_id
            else:
                result_id = anvil.server.call('create_note', record)
        except Exception as e:
            # The server's validators speak to the student; anything else (a
            # dropped connection, a platform error) is replaced by a sentence
            # that can actually be acted on. No field is named, because a failure
            # that gets this far is not about one box.
            toast_error(friendly_error(
                e, "Couldn't save that note. Please try again."))
            return
        toast("Note saved.")
        # The caller (NotesForm) decides what to do with the id; the dialog's
        # only job is to hand it back.
        self.raise_event('x-close-alert', value=result_id)

    def _on_cancel_click(self, **event_args):
        self.raise_event('x-close-alert', value=None)
