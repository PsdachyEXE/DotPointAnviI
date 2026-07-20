import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""NoteEditorForm - create / edit a single note (FR10).

Opened as an alert(..., large=True) from NotesForm. Title, plain-text content
(no markdown render — SRS plain-text constraint), a simple tag manager (type +
Add, pills with x), and a pin checkbox. Save raises 'x-close-alert' with the note
id; Cancel returns None. (The 300ms edit-mode autosave in the spec is deferred; Save
is explicit for now.)

See IMPLEMENTATION_SPEC.md section 3 (NoteEditorForm).
"""

import anvil
import anvil.server
from anvil import (
    ColumnPanel, FlowPanel, Label, TextBox, TextArea, CheckBox, Button, Notification,
)


class NoteEditorForm(ColumnPanel):
    def __init__(self, mode='create', note_id=None, **properties):
        super().__init__(**properties)
        self.spacing_above = 'none'
        self.spacing_below = 'none'

        self._mode = mode
        self._note_id = note_id
        self._tags = []

        self.add_component(Label(text='New note' if mode == 'create' else 'Edit note',
                                 font_size=20, bold=True))

        self.add_component(Label(text='Title'))
        self._title_tb = TextBox()
        self.add_component(self._title_tb)

        self.add_component(Label(text='Content'))
        self._content_ta = TextArea(height='260px')
        self.add_component(self._content_ta)

        self.add_component(Label(text='Tags'))
        tag_row = FlowPanel()
        self._tag_tb = TextBox(placeholder='add a tag')
        self._tag_tb.set_event_handler('pressed_enter', self._on_add_tag)
        tag_row.add_component(self._tag_tb)
        add_tag_btn = Button(text='Add', role='secondary')
        add_tag_btn.set_event_handler('click', self._on_add_tag)
        tag_row.add_component(add_tag_btn)
        self.add_component(tag_row)
        self._tag_pills = FlowPanel()
        self.add_component(self._tag_pills)

        self._pin_cb = CheckBox(text='Pin this note')
        self.add_component(self._pin_cb)

        footer = FlowPanel()
        cancel_btn = Button(text='Cancel', role='secondary')
        cancel_btn.set_event_handler('click', self._on_cancel_click)
        footer.add_component(cancel_btn)
        save_btn = Button(text='Save', role='primary')
        save_btn.set_event_handler('click', self._on_save_click)
        footer.add_component(save_btn)
        self.add_component(footer)

        if mode == 'edit' and note_id:
            self._load()
        self._render_tags()

    # --- data --------------------------------------------------------------
    def _load(self):
        try:
            notes = anvil.server.call('search_notes')
        except Exception as e:
            Notification("Couldn't load note: %s" % e, style='danger').show()
            return
        note = next((n for n in notes if n['id'] == self._note_id), None)
        if note is None:
            Notification("Note not found.", style='danger').show()
            return
        self._title_tb.text = note.get('title') or ''
        self._content_ta.text = note.get('content') or ''
        self._tags = list(note.get('tags') or [])
        self._pin_cb.checked = bool(note.get('is_pinned'))

    def _render_tags(self):
        self._tag_pills.clear()
        for t in self._tags:
            pill = FlowPanel()
            pill.add_component(Label(text='#%s' % t, foreground='#3b7dd8'))
            x = Button(text='x', role='secondary')
            x.set_event_handler('click', lambda tag=t, **e: self._remove_tag(tag))
            pill.add_component(x)
            self._tag_pills.add_component(pill)

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
            Notification(str(e), style='danger').show()
            return
        Notification("Note saved.", style='success').show()
        self.raise_event('x-close-alert', value=result_id)

    def _on_cancel_click(self, **event_args):
        self.raise_event('x-close-alert', value=None)
