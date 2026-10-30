# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""When a click on Approve or Deny counts.

An answer to a NOLGIA request comes only from a deliberate click (the
owner's Mac test 3, 2026-10-29: a request in the Resolve plugin was approved
about 3 s after its window opened, with the pointer resting where its button
appears and nobody clicking). In the NOLGIA panel and in the NOLGIA request
window:

- Each time a request shows there, its Approve and Deny take no answer for
  ARM_SECONDS: in the panel from the first time it is drawn (they look
  greyed out until then), in the window every time it opens (Blender never
  redraws an open popup's buttons, so they look the same throughout).
- A click counts only when Blender ran the button on a left mouse release
  and the press that started it came after ARM_SECONDS, at the same place
  (within PRESS_SLOP_PX): a press watcher (NOLGIA_OT_watch_clicks) notes
  every left mouse press in a window while a request waits. Return, keypad
  Enter and every other key never answer, and neither does a release whose
  press came earlier or elsewhere, such as one already held down when the
  window opened under the pointer (Blender's popups take a bare release).
- The answer goes to the request id the button carries, and only while
  that request still waits.
- Every answer and every dropped click is logged with its request id.

No bpy here, so the unit tests run it with plain Python.
"""

import time

ARM_SECONDS = 1.0
PRESS_SLOP_PX = 12
PLACES = ("panel", "window")


class Arming:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._shown = {}  # (place, command id) -> when it showed there
        self._armed = set()
        self.last_press = None  # (when, x, y) of the last left mouse press seen

    def show(self, place, command_id):
        """The request shows in place (again): its buttons go off for ARM_SECONDS."""
        self._shown[(place, command_id)] = self._clock()
        self._armed.discard((place, command_id))

    def drawn(self, place, command_id):
        """The request is drawn in place: it showed now, unless it already had."""
        if (place, command_id) not in self._shown:
            self.show(place, command_id)

    def armed(self, place, command_id):
        at = self._shown.get((place, command_id))
        return at is not None and self._clock() - at >= ARM_SECONDS

    def newly_armed(self):
        """True once for each request whose buttons just turned on (redraw then)."""
        now = self._clock()
        fresh = [key for key, at in self._shown.items() if key not in self._armed and now - at >= ARM_SECONDS]
        self._armed.update(fresh)
        return bool(fresh)

    def pressed(self, x, y):
        self.last_press = (self._clock(), x, y)

    def refusal(self, place, command_id, event_type, event_value, x, y):
        """None when this button run, ending with a release at (x, y), is a
        click that answers; else why not."""
        at = self._shown.get((place, command_id))
        if event_type != "LEFTMOUSE" or event_value != "RELEASE":
            return "it was not a mouse click (%s %s)" % (event_type, event_value)
        if at is None or self._clock() - at < ARM_SECONDS:
            return "it came before the request's buttons were on"
        press = self.last_press
        if press is None or press[0] < at + ARM_SECONDS:
            return "the mouse button was pressed before the request's buttons were on"
        if abs(press[1] - x) > PRESS_SLOP_PX or abs(press[2] - y) > PRESS_SLOP_PX:
            return "the mouse button was pressed somewhere else"
        return None

    def forget(self, command_id):
        for place in PLACES:
            self._shown.pop((place, command_id), None)
            self._armed.discard((place, command_id))

    def keep_only(self, command_ids):
        """Drop what is known about requests that no longer wait."""
        for key in [k for k in self._shown if k[1] not in command_ids]:
            del self._shown[key]
            self._armed.discard(key)
