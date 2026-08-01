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
remove button. Content stays a plain TextArea (no markdown render - SRS
plain-text constraint).

Save raises 'x-close-alert' with the note id; Cancel returns None. (The 300ms
edit-mode autosave in the spec is deferred; Save is explicit for now.)

See IMPLEMENTATION_SPEC.md section 3 (NoteEditorForm) and section 14 (Design
system) - all colour and type here comes from roles, never from Python.
"""

import anvil
import anvil.server
from anvil import ColumnPanel, Label, TextBox, TextArea, CheckBox, Button

from ..common import make_field, make_row, make_chip, toast, toast_error


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
        self.add_component(Label(text='New note' if mode == 'create' else 'Edit note',
                                 role='pagetitle'))

        self._title_tb = TextBox()
        self.add_component(make_field('Title', self._title_tb))

        # A fixed height is set here (not by a role) because the box has to be
        # tall enough to write a paragraph in - it is sizing, not styling.
        self._content_ta = TextArea(height='260px')
        self.add_component(make_field('Content', self._content_ta,
                                      hint='Plain text - markdown is not rendered.'))

        # The tag editor is one field group: the add-control row is the field's
        # component, and the chips panel is appended to the same group so the
        # tags stay visually attached to the box that creates them.
        self._tag_tb = TextBox(placeholder='add a tag')
        self._tag_tb.set_event_handler('pressed_enter', self._on_add_tag)
        add_tag_btn = Button(text='Add', role='secondary')
        add_tag_btn.set_event_handler('click', self._on_add_tag)
        tags_field = make_field('Tags', make_row(self._tag_tb, add_tag_btn),
                                hint='Tags are how notes are filtered on the Notes page.')
        self._tag_pills = make_row()
        tags_field.add_component(self._tag_pills)
        self.add_component(tags_field)

        # Left as a plain labelled checkbox: the label *is* the question, so
        # wrapping it in a make_field caption would just say it twice.
        self._pin_cb = CheckBox(text='Pin this note')
        self.add_component(self._pin_cb)

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
            toast_error("Couldn't load note: %s" % e)
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
            # tag=t is a default argument, not a closure over t: without it every
            # handler would capture the same final loop value and remove the
            # wrong tag.
            x = Button(text='x', role='iconbtn')
            x.set_event_handler('click', lambda tag=t, **e: self._remove_tag(tag))
            self._tag_pills.add_component(make_row(make_chip(t), x))

    def _remove_tag(self, tag):
        self._tags = [t for t in self._tags if t != tag]
        self._render_tags()

    # --- handlers ----------------------------------------------------------
    def _on_add_tag(self, **event_args):
        tag = (self._tag_tb.text or '').strip()
        if tag and tag.lower() not in [t.lower() for t in self._tags]:
            self._tags.append(tag)
        self._tag_tb.text = ''
        self._render_tags()

    def _on_save_click(self, **event_args):
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
            toast_error(str(e))
            return
        toast("Note saved.")
        # The caller (NotesForm) decides what to do with the id; the dialog's
        # only job is to hand it back.
        self.raise_event('x-close-alert', value=result_id)

    def _on_cancel_click(self, **event_args):
        self.raise_event('x-close-alert', value=None)
