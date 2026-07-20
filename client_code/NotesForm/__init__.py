import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""NotesForm - the notes index / panel (FR10, FR11).

Top bar + a filter row (search box, tag filter, New note) over a pinned-first
list of note cards. Each card shows title, tags, a plain-text content preview
and updated date, with pin / edit / delete actions. Editing happens in
NoteEditorForm, opened as an alert. Populated by a single search_notes() call.

See IMPLEMENTATION_SPEC.md section 3 (NotesForm).
"""

import anvil
import anvil.server
from anvil import (
    ColumnPanel, FlowPanel, Label, TextBox, Button, DropDown, Notification,
    Spacer, alert, confirm,
)

from ..common import make_top_bar

_MONTHS_ABBR = ('', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


def _fmt_iso(s):
    """'YYYY-MM-DD...'(ISO) -> 'DD Mon YYYY' or '' (manual; Skulpt-safe)."""
    if not s or not isinstance(s, str) or len(s) < 10:
        return ''
    try:
        y, m, d = int(s[0:4]), int(s[5:7]), int(s[8:10])
        return '%02d %s %d' % (d, _MONTHS_ABBR[m], y)
    except (ValueError, IndexError):
        return ''


class NotesForm(ColumnPanel):
    def __init__(self, **properties):
        super().__init__(**properties)
        self.spacing_above = 'none'
        self.spacing_below = 'none'

        self._search = ''
        self._tag = ''

        self.add_component(make_top_bar())
        body = ColumnPanel()
        self.add_component(body)

        body.add_component(Label(text='Notes', font_size=20, bold=True))

        # --- filter row ---
        row = FlowPanel()
        self._search_tb = TextBox(placeholder='Search title & content')
        self._search_tb.set_event_handler('pressed_enter', self._on_search)
        self._search_tb.set_event_handler('lost_focus', self._on_search)
        row.add_component(self._search_tb)
        self._tag_dd = DropDown(items=[('All tags', '')])
        self._tag_dd.set_event_handler('change', self._on_search)
        row.add_component(self._tag_dd)
        new_btn = Button(text='New note', role='primary')
        new_btn.set_event_handler('click', self._on_new_click)
        row.add_component(new_btn)
        body.add_component(row)

        body.add_component(Spacer(height=8))
        self._list_panel = ColumnPanel()
        body.add_component(self._list_panel)

        self._refresh()

    # --- data --------------------------------------------------------------
    def _refresh(self):
        try:
            notes = anvil.server.call('search_notes',
                                      query=self._search or None,
                                      tag=self._tag or None)
        except Exception as e:
            self._list_panel.clear()
            self._list_panel.add_component(
                Label(text="Couldn't load notes: %s" % e, foreground='#d64550'))
            return
        self._populate_tags(notes)
        self._render(notes)

    def _populate_tags(self, notes):
        all_tags = sorted({t for n in notes for t in (n.get('tags') or [])})
        current = self._tag_dd.selected_value
        items = [('All tags', '')] + [(t, t) for t in all_tags]
        self._tag_dd.items = items
        vals = [v for _, v in items]
        self._tag_dd.selected_value = current if current in vals else ''

    def _render(self, notes):
        self._list_panel.clear()
        if not notes:
            self._list_panel.add_component(
                Label(text='No notes match.', foreground='#9aa0a6', italic=True))
            return
        for n in notes:
            self._list_panel.add_component(self._make_card(n))

    def _make_card(self, n):
        card = ColumnPanel(spacing_above='small', spacing_below='small')
        head = FlowPanel()
        if n.get('is_pinned'):
            head.add_component(Label(text='PINNED', font_size=10, bold=True,
                                     foreground='#e8833a'))
        head.add_component(Label(text=n.get('title') or '(untitled)', bold=True, font_size=15))
        card.add_component(head)

        tags = n.get('tags') or []
        if tags:
            card.add_component(Label(text=' '.join('#%s' % t for t in tags),
                                     foreground='#3b7dd8', font_size=11))
        preview = (n.get('content') or '').strip().replace('\n', ' ')
        if len(preview) > 160:
            preview = preview[:160] + '…'
        if preview:
            card.add_component(Label(text=preview, foreground='#9aa0a6'))
        card.add_component(Label(text='Updated %s' % _fmt_iso(n.get('updated_at')),
                                 font_size=10, foreground='#9aa0a6'))

        actions = FlowPanel()
        pin_btn = Button(text='Unpin' if n.get('is_pinned') else 'Pin', role='secondary')
        pin_btn.set_event_handler('click', lambda nid=n['id'], **e: self._on_pin_click(nid))
        actions.add_component(pin_btn)
        edit_btn = Button(text='Edit', role='secondary')
        edit_btn.set_event_handler('click', lambda nid=n['id'], **e: self._on_edit_click(nid))
        actions.add_component(edit_btn)
        del_btn = Button(text='Delete', role='secondary')
        del_btn.set_event_handler('click', lambda nid=n['id'], **e: self._on_delete_click(nid))
        actions.add_component(del_btn)
        card.add_component(actions)
        return card

    # --- handlers ----------------------------------------------------------
    def _on_search(self, **event_args):
        self._search = (self._search_tb.text or '').strip()
        self._tag = self._tag_dd.selected_value or ''
        self._refresh()

    def _on_new_click(self, **event_args):
        from ..NoteEditorForm import NoteEditorForm
        if alert(NoteEditorForm(mode='create'), title='', large=True, buttons=[]):
            self._refresh()

    def _on_edit_click(self, note_id):
        from ..NoteEditorForm import NoteEditorForm
        if alert(NoteEditorForm(mode='edit', note_id=note_id), title='', large=True, buttons=[]):
            self._refresh()

    def _on_pin_click(self, note_id):
        try:
            anvil.server.call('toggle_pin', note_id)
        except Exception as e:
            Notification("Couldn't update: %s" % e, style='danger').show()
            return
        self._refresh()

    def _on_delete_click(self, note_id):
        if not confirm('Delete this note?'):
            return
        try:
            anvil.server.call('delete_note', note_id)
        except Exception as e:
            Notification("Couldn't delete: %s" % e, style='danger').show()
            return
        self._refresh()
