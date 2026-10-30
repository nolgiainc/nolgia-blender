# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""Preferences, the NOLGIA tab in the 3D Viewport sidebar, and buttons."""

import time

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty

from . import runtime
from .core import AGENT_URL

PACKAGE = __package__
MAX_CODE_LINES = 30
ACTIVITY_SHOWN = 6
LINKS_SHOWN = 3

# What the chat suggests before the first message: things NOLGIA Agent does
# in Blender through this plugin. The button shows the short name; a click
# puts the whole ask in the box.
EXAMPLES = (
    ("Low poly mountains", "Add a low poly mountain scene with a camera and a sun."),
    ("Preview this scene", "Render a preview of this scene from the active camera and tell me what you see."),
    ("Bring in my newest image", "Bring my newest NOLGIA image into this scene as a plane."),
)

STATUS_ICONS = {
    "received": "TIME",
    "running": "PLAY",
    "approval": "QUESTION",
    "succeeded": "CHECKMARK",
    "failed": "ERROR",
    "cancelled": "CANCEL",
    "expired": "CANCEL",
}


def _ctl():
    return runtime.controller


def _on_connected(prefs, _context):
    ctl = _ctl()
    if ctl is None or ctl._syncing:
        return
    if prefs.connected:
        ctl.connect()
    else:
        ctl.disconnect()


def _on_allow_agent(_prefs, _context):
    ctl = _ctl()
    if ctl is not None and not ctl._syncing:
        ctl.update_snapshot()


class NOLGIA_Preferences(bpy.types.AddonPreferences):
    bl_idname = PACKAGE

    token: StringProperty(name="Token", subtype="PASSWORD", options={"HIDDEN"})
    account_email: StringProperty(name="Account", options={"HIDDEN"})
    token_expires_at: FloatProperty(name="Token expires", options={"HIDDEN"})
    connected: BoolProperty(
        name="Connected",
        description="Let NOLGIA send commands to this Blender. Turn off to pause",
        default=False,
        update=_on_connected,
    )
    allow_agent: BoolProperty(
        name="Allow NOLGIA Agent",
        description="Let NOLGIA Agent work here (the chat in this tab needs it), as well as agents "
        "you run yourself, like Claude, Cursor or Codex",
        default=True,
        update=_on_allow_agent,
    )
    ask_before_run: BoolProperty(
        name="Ask before running code",
        description="Show each piece of Python NOLGIA wants to run and wait for you to approve it",
        default=False,
    )

    def draw(self, context):
        draw_nolgia(self.layout, context, in_prefs=True)


def draw_nolgia(layout, context, in_prefs=False):
    """The whole NOLGIA panel in the add-on's preferences. The sidebar draws
    the same parts in sub-panels, with the chat (draw_status, draw_chat,
    draw_settings, draw_activity)."""
    ctl = _ctl()
    prefs = ctl.prefs() if ctl is not None else None
    if ctl is None or prefs is None:
        layout.label(text="NOLGIA is loading...")
        return
    draw_status(layout, context, ctl)
    if ctl.login is None and ctl.signed_in:
        draw_settings(layout, context, ctl, prefs)
    draw_activity(layout, context, ctl, prefs, box=True)
    if in_prefs:
        layout.label(text="NOLGIA works in this Blender only while Connected is on.")


def draw_status(layout, context, ctl):
    """The status line, signing in, and requests waiting for approval."""
    _wrapped(layout, ctl.status_line(), context, icon=_status_icon(ctl))

    if ctl.login is not None:
        box = layout.box()
        prompt = ctl.login.prompt
        if prompt is not None:
            box.label(text="Your code:")
            row = box.row()
            row.scale_y = 1.6
            row.label(text=prompt.user_code)
            box.label(text="Approve it in the browser page that opened.")
            row = box.row(align=True)
            row.operator("nolgia.open_sign_in_page", icon="URL")
            row.operator("nolgia.copy_code", icon="COPYDOWN")
        box.operator("nolgia.cancel_sign_in", icon="CANCEL")
    elif not ctl.signed_in:
        row = layout.row()
        row.scale_y = 1.4
        row.operator("nolgia.sign_in", icon="USER")

    for pending in list(ctl.executor.approvals):
        ctl.arming.drawn("panel", pending.id)
        box = layout.box()
        box.label(text=pending.request.title, icon="QUESTION")
        for line in pending.request.lines[:6]:
            box.label(text=_clip(line))
        if len(pending.request.lines) > 6:
            box.operator("nolgia.review_request", text="Show all", icon="TEXT").command_id = pending.id
        _answer_buttons(box, ctl, pending, "panel")


def _answer_buttons(layout, ctl, pending, place):
    """Approve and Deny for one request. They refuse (and log) a click until
    approval.ARM_SECONDS have passed since it showed in this place
    (approval.Arming.refusal). In the panel they look greyed out until then
    and the panel redraws when they turn on; an open popup is never redrawn,
    so in the window they look the same throughout."""
    row = layout.row(align=True)
    if place == "panel":
        row.active = ctl.arming.armed(place, pending.id)
    approve = row.operator("nolgia.approve", text=pending.request.approve_label, icon="CHECKMARK")
    approve.command_id = pending.id
    approve.place = place
    deny = row.operator("nolgia.deny", text="Deny", icon="CANCEL")
    deny.command_id = pending.id
    deny.place = place


def draw_settings(layout, context, ctl, prefs):
    email = ctl.account_email()
    if email:
        layout.label(text="Signed in as %s" % email)
    elif ctl.env_token:
        layout.label(text="Signed in with NOLGIA_TOKEN")
    col = layout.column()
    col.prop(prefs, "connected")
    col.prop(prefs, "allow_agent")
    col.prop(prefs, "ask_before_run")
    layout.operator("nolgia.sign_out", icon="X")


def draw_pause(layout, ctl, prefs):
    if ctl.signed_in:
        paused = not prefs.connected
        layout.operator("nolgia.pause", text="Resume" if paused else "Pause",
                        icon="PLAY" if paused else "PAUSE")


def draw_activity(layout, context, ctl, prefs, box=False):
    """The commands NOLGIA ran here, newest first: the newest few, or all
    of them (at most 20) when the person asks."""
    items = ctl.activity.items()
    if box:
        layout = layout.box()
        header = layout.row()
        header.label(text="Activity", icon="SORTTIME")
        draw_pause(header, ctl, prefs)
    if not items:
        layout.label(text="Nothing yet.")
    wm = context.window_manager
    show_all = bool(getattr(wm, "nolgia_activity_all", False))
    for item in items if show_all else items[:ACTIVITY_SHOWN]:
        row = layout.row(align=True)
        row.label(text=time.strftime("%H:%M:%S", time.localtime(item["time"])))
        row.label(text=item["kind"])
        label = runtime.ActivityLog.STATUS_LABELS.get(item["status"], item["status"])
        row.label(text=label, icon=STATUS_ICONS.get(item["status"], "DOT"))
    if len(items) > ACTIVITY_SHOWN and hasattr(wm, "nolgia_activity_all"):
        layout.prop(wm, "nolgia_activity_all", toggle=True,
                    text="Show fewer" if show_all else "Show all %d" % len(items),
                    icon="TRIA_UP" if show_all else "TRIA_DOWN")


# ------------------------------------------------------------------- the chat


def _width(context, inset=40):
    """How many pixels of text fit on one line of the sidebar, inside
    `inset` (unscaled) pixels of margins."""
    region = getattr(context, "region", None)
    width = region.width if region is not None else 300
    return max(80, width - inset * _scale(context))


def _scale(context):
    try:
        return context.preferences.system.ui_scale
    except AttributeError:
        return 1.0


def _measure(context):
    """A function giving a text's width in pixels at the panel's font size."""
    try:
        import blf

        points = context.preferences.ui_styles[0].widget.points
        blf.size(0, points * _scale(context))
        if blf.dimensions(0, "NOLGIA")[0] <= 0:
            raise ValueError("no font")
        # Labels draw with a little padding and their own kerning: keep a margin.
        return lambda text: blf.dimensions(0, text)[0] * 1.1
    except Exception:  # no window (tests, background): about 6 pixels a character
        scale = _scale(context)
        return lambda text: len(text) * 6.2 * scale


def _wrap(text, width, measure, indent=""):
    """Break text into lines no wider than `width` pixels."""
    lines = []
    for paragraph in str(text or "").split("\n"):
        line = ""
        for word in paragraph.split():
            candidate = word if not line else line + " " + word
            if line and measure(candidate) > width:
                lines.append(line)
                line = indent + word
            else:
                line = candidate
            while measure(line) > width and len(line) > len(indent) + 1:  # one very long word
                cut = len(line)
                while cut > len(indent) + 1 and measure(line[:cut]) > width:
                    cut -= 1
                lines.append(line[:cut])
                line = indent + line[cut:]
        lines.append(line)
    return lines


def _wrapped(layout, text, context, inset=40, icon=None, indent=""):
    """Text as labels, broken to the sidebar's width (Blender labels do not
    wrap). A first line with an icon keeps the rest lined up with it."""
    col = layout.column(align=True)
    width = _width(context, inset + (22 if icon else 0))
    for i, line in enumerate(_wrap(text, width, _measure(context), indent)):
        if icon:
            col.label(text=line, icon=icon if i == 0 else "BLANK1")
        else:
            col.label(text=line)
    return col


def _parts_text(parts):
    return "".join(p["text"] for p in parts)


def _faint(layout):
    row = layout.row()
    row.enabled = False
    return row


def draw_message(layout, context, row):
    box = layout.box()
    inset = 64
    if row["role"] == "user":
        _faint(box).label(text="BACKGROUND WORK FINISHED" if row["background"] else "YOU")
        _wrapped(box, row["text"], context, inset)
        return
    _faint(box).label(text="NOLGIA AGENT")
    if row["status"] == "pending":
        box.label(text="Waiting for a free turn..." if row["queued"] else "Working...", icon="TIME")
        return
    links = []
    for block in row["blocks"] or []:
        text = _parts_text(block["parts"])
        if block["type"] == "li":
            _wrapped(box, "\u2022 " + text, context, inset, indent="   ")
        else:
            _wrapped(box, text, context, inset)
        for p in block["parts"]:
            if p.get("href") and p["href"] not in [href for _, href in links]:
                links.append((p["text"], p["href"]))
    for text, href in links[:LINKS_SHOWN]:
        box.operator("wm.url_open", text=text, icon="URL").url = href
    if row["failure"]:
        sub = box.column()
        sub.alert = True
        _wrapped(sub, row["failure"], context, inset, icon="ERROR")
    question = row["question"]
    if question and question.get("options"):
        col = box.column(align=True)
        for option in question["options"]:
            label = str(option.get("label") or "")
            if label:
                col.operator("nolgia.chat_answer", text=label).text = label
    if row["credits"] is not None and row["status"] == "complete":
        _faint(box).label(text="%s credits" % _number(row["credits"]))


def _number(value):
    return str(int(value)) if float(value).is_integer() else str(value)


def draw_chat(layout, context, ctl):
    chat = ctl.chat
    view = chat.view()
    blocker = ctl.chat_blocker()
    wm = context.window_manager

    if view["loaded"] and view["empty"]:
        _wrapped(layout, "Tell NOLGIA Agent what to do in this Blender. Try one of these:", context)
        col = layout.column(align=True)
        for i, (label, _ask) in enumerate(EXAMPLES):
            col.operator("nolgia.chat_example", text=label, icon="FORWARD").index = i
        layout.operator("wm.url_open", text="Open NOLGIA Agent", icon="URL").url = AGENT_URL

    if view["hidden"]:
        _faint(layout).label(text="Earlier messages are on nolgia.ai.")
    for row in view["rows"]:
        draw_message(layout, context, row)

    if view["working"]:
        _wrapped(layout, view["working"], context, icon="TIME")
    notice = view["notice"]
    if notice:
        box = layout.box()
        sub = box.column()
        sub.alert = notice["kind"] in ("error", "credits", "network")
        _wrapped(sub, notice["text"], context, 52, icon="ERROR" if sub.alert else "INFO")
        if notice.get("action"):
            box.operator("wm.url_open", text=notice["action"]["label"], icon="URL").url = notice["action"]["url"]
    if blocker:
        box = layout.box()
        _wrapped(box, blocker["text"], context, 52, icon="INFO")
        box.operator("nolgia.chat_fix", text=blocker["label"])

    col = layout.column()
    col.enabled = not view["busy"] and blocker is None
    try:
        col.prop(wm, "nolgia_chat_input", text="", placeholder="Ask NOLGIA Agent to make or change something here")
    except TypeError:  # no placeholder before Blender 4.1
        col.prop(wm, "nolgia_chat_input", text="")
    row = layout.row(align=True)
    if view["can_stop"]:
        row.operator("nolgia.chat_stop", text="Stop", icon="CANCEL")
    send = row.row(align=True)
    send.enabled = not view["busy"] and blocker is None
    send.scale_y = 1.3
    send.operator("nolgia.chat_send", text="Send", icon="EXPORT")
    _wrapped(_faint(layout).column(), view["cost_line"], context)
    if view["session_url"]:
        layout.operator("wm.url_open", text="Open this chat on nolgia.ai", icon="URL").url = view["session_url"]


def _status_icon(ctl):
    if ctl.executor.approvals:
        return "QUESTION"
    worker = ctl.worker
    if worker is None:
        return "UNLINKED" if ctl.signed_in else "USER"
    return "LINKED" if worker.state == runtime.Status.CONNECTED else "TIME"


def _clip(line, width=90):
    line = line.replace("\t", "    ")
    return line if len(line) <= width else line[: width - 3] + "..."


class VIEW3D_PT_nolgia(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "NOLGIA"
    bl_label = "NOLGIA"

    def draw(self, context):
        ctl = _ctl()
        if ctl is None or ctl.prefs() is None:
            self.layout.label(text="NOLGIA is loading...")
            return
        draw_status(self.layout, context, ctl)


def _signed_in_here():
    ctl = _ctl()
    return ctl is not None and ctl.prefs() is not None and ctl.signed_in and ctl.login is None


class VIEW3D_PT_nolgia_agent(bpy.types.Panel):
    """The chat with NOLGIA Agent. The sidebar scrolls (mouse wheel), and
    every part below can be folded away by its header."""

    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "NOLGIA"
    bl_label = "NOLGIA Agent"
    bl_parent_id = "VIEW3D_PT_nolgia"

    @classmethod
    def poll(cls, context):
        return _signed_in_here()

    def draw_header_preset(self, context):
        if not _ctl().chat.view()["empty"]:
            self.layout.operator("nolgia.chat_new", text="New chat", icon="ADD")

    def draw(self, context):
        draw_chat(self.layout, context, _ctl())


class VIEW3D_PT_nolgia_settings(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "NOLGIA"
    bl_label = "Settings"
    bl_parent_id = "VIEW3D_PT_nolgia"

    @classmethod
    def poll(cls, context):
        return _signed_in_here()

    def draw(self, context):
        ctl = _ctl()
        draw_settings(self.layout, context, ctl, ctl.prefs())


class VIEW3D_PT_nolgia_activity(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "NOLGIA"
    bl_label = "Activity"
    bl_parent_id = "VIEW3D_PT_nolgia"

    @classmethod
    def poll(cls, context):
        ctl = _ctl()
        return ctl is not None and ctl.prefs() is not None

    def draw_header_preset(self, context):
        ctl = _ctl()
        draw_pause(self.layout, ctl, ctl.prefs())

    def draw(self, context):
        ctl = _ctl()
        draw_activity(self.layout, context, ctl, ctl.prefs())


class NOLGIA_OT_chat_send(bpy.types.Operator):
    bl_idname = "nolgia.chat_send"
    bl_label = "Send"
    bl_description = "Send this to NOLGIA Agent, which works in this Blender"

    def execute(self, context):
        ctl = _ctl()
        wm = context.window_manager
        if ctl is None or ctl.chat_blocker() is not None:
            return {"CANCELLED"}
        if ctl.chat.send(wm.nolgia_chat_input):
            set_chat_input(wm, "")
            return {"FINISHED"}
        return {"CANCELLED"}


class NOLGIA_OT_chat_answer(bpy.types.Operator):
    bl_idname = "nolgia.chat_answer"
    bl_label = "Answer"
    bl_description = "Send this answer to NOLGIA Agent"
    bl_options = {"INTERNAL"}

    text: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        ctl = _ctl()
        if ctl is None or ctl.chat_blocker() is not None or not ctl.chat.send(self.text):
            return {"CANCELLED"}
        return {"FINISHED"}


class NOLGIA_OT_chat_example(bpy.types.Operator):
    bl_idname = "nolgia.chat_example"
    bl_label = "Try this"
    bl_options = {"INTERNAL"}

    index: IntProperty(options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def description(cls, context, properties):
        return "Put this in the box: " + EXAMPLES[properties.index % len(EXAMPLES)][1]

    def execute(self, context):
        set_chat_input(context.window_manager, EXAMPLES[self.index % len(EXAMPLES)][1])
        return {"FINISHED"}


class NOLGIA_OT_chat_stop(bpy.types.Operator):
    bl_idname = "nolgia.chat_stop"
    bl_label = "Stop"
    bl_description = "Stop what NOLGIA Agent is doing (generations it already started keep running)"

    def execute(self, context):
        _ctl().chat.stop()
        return {"FINISHED"}


class NOLGIA_OT_chat_new(bpy.types.Operator):
    bl_idname = "nolgia.chat_new"
    bl_label = "New chat"
    bl_description = "Start a new chat with NOLGIA Agent (this one stays on nolgia.ai)"

    def execute(self, context):
        _ctl().chat.new_chat()
        return {"FINISHED"}


class NOLGIA_OT_chat_fix(bpy.types.Operator):
    bl_idname = "nolgia.chat_fix"
    bl_label = "Fix"
    bl_description = "Switch on what NOLGIA Agent needs to work in this Blender"

    def execute(self, context):
        _ctl().fix_chat_blocker()
        return {"FINISHED"}


class NOLGIA_OT_sign_in(bpy.types.Operator):
    bl_idname = "nolgia.sign_in"
    bl_label = "Sign in"
    bl_description = "Sign in to NOLGIA in your browser"

    def execute(self, context):
        _ctl().sign_in()
        return {"FINISHED"}


class NOLGIA_OT_cancel_sign_in(bpy.types.Operator):
    bl_idname = "nolgia.cancel_sign_in"
    bl_label = "Cancel"
    bl_description = "Stop signing in"

    def execute(self, context):
        _ctl().cancel_sign_in()
        return {"FINISHED"}


class NOLGIA_OT_open_sign_in_page(bpy.types.Operator):
    bl_idname = "nolgia.open_sign_in_page"
    bl_label = "Open page again"
    bl_description = "Open the NOLGIA sign in page in your browser"

    def execute(self, context):
        ctl = _ctl()
        if ctl.login is not None and ctl.login.prompt is not None:
            bpy.ops.wm.url_open(url=ctl.login.prompt.open_url)
        return {"FINISHED"}


class NOLGIA_OT_copy_code(bpy.types.Operator):
    bl_idname = "nolgia.copy_code"
    bl_label = "Copy code"
    bl_description = "Copy the sign in code"

    def execute(self, context):
        ctl = _ctl()
        if ctl.login is not None and ctl.login.prompt is not None:
            context.window_manager.clipboard = ctl.login.prompt.user_code
        return {"FINISHED"}


class NOLGIA_OT_sign_out(bpy.types.Operator):
    bl_idname = "nolgia.sign_out"
    bl_label = "Sign out"
    bl_description = "Disconnect and forget the NOLGIA sign in on this computer"

    def execute(self, context):
        _ctl().sign_out()
        return {"FINISHED"}


class NOLGIA_OT_pause(bpy.types.Operator):
    bl_idname = "nolgia.pause"
    bl_label = "Pause"
    bl_description = "Stop or resume taking commands from NOLGIA"

    def execute(self, context):
        prefs = _ctl().prefs()
        prefs.connected = not prefs.connected
        return {"FINISHED"}


def _answer(op, event, approve):
    """Approve or Deny clicked: answer only for a deliberate click (approval.py)."""
    ctl = _ctl()
    if ctl is None:
        return {"CANCELLED"}
    pending = next((p for p in ctl.executor.approvals if p.id == op.command_id), None)
    label = (pending.request.approve_label if pending is not None else "Approve") if approve else "Deny"
    why = ctl.arming.refusal(op.place, op.command_id, event.type, event.value, event.mouse_x, event.mouse_y)
    if why is not None:
        runtime.log("Ignored %s for request %s: %s." % (label, op.command_id, why))
        return {"CANCELLED"}
    answered = ctl.executor.approve(op.command_id) if approve else ctl.executor.deny(op.command_id)
    runtime.log("The person clicked %s for request %s%s." % (
        label, op.command_id, "" if answered else ", which was no longer waiting"))
    ctl.arming.forget(op.command_id)
    ctl.bump()
    return {"FINISHED"}


def _not_a_click(op, approve):
    runtime.log("Ignored %s for request %s: it did not come from a click on its button." % (
        "Approve" if approve else "Deny", op.command_id))
    return {"CANCELLED"}


class NOLGIA_OT_approve(bpy.types.Operator):
    bl_idname = "nolgia.approve"
    bl_label = "Approve"
    bl_description = "Let NOLGIA do this"
    bl_options = {"INTERNAL"}

    command_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    place: StringProperty(default="panel", options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        return _answer(self, event, True)

    def execute(self, context):
        return _not_a_click(self, True)


class NOLGIA_OT_deny(bpy.types.Operator):
    bl_idname = "nolgia.deny"
    bl_label = "Deny"
    bl_description = "Do not let NOLGIA do this"
    bl_options = {"INTERNAL"}

    command_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    place: StringProperty(default="panel", options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        return _answer(self, event, False)

    def execute(self, context):
        return _not_a_click(self, False)


class NOLGIA_OT_watch_clicks(bpy.types.Operator):
    """Notes each left mouse press in its window while a NOLGIA request
    waits, and passes every event on (approval.py)."""

    bl_idname = "nolgia.watch_clicks"
    bl_label = "NOLGIA click watch"
    bl_options = {"INTERNAL"}

    def invoke(self, context, event):
        ctl = _ctl()
        if ctl is None or context.window is None:
            return {"CANCELLED"}
        self._window = context.window
        self._generation = ctl.watcher_generation(context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        ctl = _ctl()
        if ctl is None:
            return {"FINISHED", "PASS_THROUGH"}
        if not ctl.executor.approvals or ctl.watcher_generation(self._window) != self._generation:
            ctl.watcher_done(self._window, self._generation)
            return {"FINISHED", "PASS_THROUGH"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            ctl.arming.pressed(event.mouse_x, event.mouse_y)
        return {"PASS_THROUGH"}


class NOLGIA_OT_review_request(bpy.types.Operator):
    """Shows a waiting request with its own Approve and Deny (approval.py:
    off for a second, a click only). Close, Return and Escape leave it
    waiting in the NOLGIA panel; the window never approves by itself."""

    bl_idname = "nolgia.review_request"
    bl_label = "NOLGIA request"
    bl_description = "Review what NOLGIA wants to do"
    bl_options = {"INTERNAL"}

    command_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def _pending(self):
        ctl = _ctl()
        if ctl is None:
            return None
        return next((p for p in ctl.executor.approvals if p.id == self.command_id), None)

    def invoke(self, context, event):
        pending = self._pending()
        if pending is None:
            return {"CANCELLED"}
        ctl = _ctl()
        ctl.arming.show("window", pending.id)
        kwargs = {"width": 640}
        try:
            shown = context.window_manager.invoke_props_dialog(
                self, title=pending.request.title, confirm_text="Close", cancel_default=True, **kwargs
            )
        except TypeError:  # Blender before 4.1 has no title, confirm_text or cancel_default
            shown = context.window_manager.invoke_props_dialog(self, **kwargs)
        # A press watcher in front of this window's handlers, or it would not
        # see the presses inside it.
        win = context.window
        bpy.app.timers.register(lambda: (ctl.start_watchers(restart=win), None)[1], first_interval=0.05)
        return shown

    def draw(self, context):
        pending = self._pending()
        layout = self.layout
        if pending is None:
            layout.label(text="This request is no longer waiting.")
            return
        layout.label(text=pending.request.title, icon="QUESTION")
        box = layout.box()
        col = box.column(align=True)
        for line in pending.request.lines[:MAX_CODE_LINES]:
            col.label(text=_clip(line, 110) or " ")
        if len(pending.request.lines) > MAX_CODE_LINES:
            col.label(text="... %d more lines" % (len(pending.request.lines) - MAX_CODE_LINES))
        layout.label(text="Code runs on this computer with your permissions.", icon="INFO")
        _answer_buttons(layout, _ctl(), pending, "window")
        layout.separator()
        try:
            # Only Close at the bottom, the default for Return.
            layout.template_popup_confirm("", text="", cancel_text="Close", cancel_default=True)
        except (AttributeError, TypeError):
            pass  # Blender without it: its OK button is Close (execute below)

    def execute(self, context):
        """The window's own confirm button: it closes and leaves the request waiting."""
        return {"FINISHED"}


CLASSES = (
    NOLGIA_Preferences,
    VIEW3D_PT_nolgia,
    VIEW3D_PT_nolgia_agent,
    VIEW3D_PT_nolgia_settings,
    VIEW3D_PT_nolgia_activity,
    NOLGIA_OT_chat_send,
    NOLGIA_OT_chat_answer,
    NOLGIA_OT_chat_example,
    NOLGIA_OT_chat_stop,
    NOLGIA_OT_chat_new,
    NOLGIA_OT_chat_fix,
    NOLGIA_OT_sign_in,
    NOLGIA_OT_cancel_sign_in,
    NOLGIA_OT_open_sign_in_page,
    NOLGIA_OT_copy_code,
    NOLGIA_OT_sign_out,
    NOLGIA_OT_pause,
    NOLGIA_OT_approve,
    NOLGIA_OT_deny,
    NOLGIA_OT_review_request,
    NOLGIA_OT_watch_clicks,
)


# Text the plugin itself puts in the chat box (an example, a message NOLGIA
# refused, the box emptied after a send) is never sent by the update below.
_setting_chat_input = False


def set_chat_input(wm, text):
    """Fill or empty the chat box without sending it."""
    global _setting_chat_input
    _setting_chat_input = True
    try:
        wm.nolgia_chat_input = text
    finally:
        _setting_chat_input = False


def _on_chat_input(wm, _context):
    """Enter in the chat box sends it, like Send (the owner's Mac test,
    2026-10-29). Blender confirms a text field on Enter (and when you click
    away from it) and only then runs this update."""
    ctl = _ctl()
    if ctl is None:
        return
    ctl.chat.wake()
    if _setting_chat_input or not wm.nolgia_chat_input.strip() or ctl.chat_blocker() is not None:
        return
    if ctl.chat.send(wm.nolgia_chat_input):
        set_chat_input(wm, "")


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.nolgia_chat_input = StringProperty(
        name="Message",
        description="Ask NOLGIA Agent to make or change something in this Blender. Enter sends it",
        options={"SKIP_SAVE"},
        update=_on_chat_input,
    )
    bpy.types.WindowManager.nolgia_activity_all = BoolProperty(
        name="Show all activity", description="Show every command NOLGIA ran here (the last 20)", default=False,
    )


def unregister():
    for name in ("nolgia_chat_input", "nolgia_activity_all"):
        if hasattr(bpy.types.WindowManager, name):
            delattr(bpy.types.WindowManager, name)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
