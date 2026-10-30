# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""When a click on Approve or Deny answers a request (approval.py)."""

import unittest

import support  # noqa: F401  (puts the extension on sys.path)

import approval


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class ArmingTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.arming = approval.Arming(clock=self.clock)

    def later(self, seconds):
        self.clock.now += seconds

    def click(self, place="panel", command_id="cmd-1", at=(50, 60), release=None):
        """A press at `at` now, then the button's run on the release."""
        self.arming.pressed(*at)
        x, y = release or at
        return self.arming.refusal(place, command_id, "LEFTMOUSE", "RELEASE", x, y)

    def test_a_click_after_the_buttons_turn_on_answers(self):
        self.arming.drawn("panel", "cmd-1")
        self.later(approval.ARM_SECONDS)
        self.assertIsNone(self.click())

    def test_a_click_as_the_request_shows_never_answers(self):
        # The owner's Mac test 3: approved about 3 s after the window opened, no click.
        self.arming.drawn("panel", "cmd-1")
        self.later(0.3)
        self.assertEqual(self.click(), "it came before the request's buttons were on")
        self.later(approval.ARM_SECONDS / 2)
        self.assertIsNotNone(self.click())

    def test_a_press_before_the_buttons_turn_on_never_counts(self):
        self.arming.show("window", "cmd-1")
        self.later(0.3)
        self.arming.pressed(50, 60)
        self.later(1.2)  # released on the button after they turned on
        self.assertEqual(self.arming.refusal("window", "cmd-1", "LEFTMOUSE", "RELEASE", 50, 60),
                         "the mouse button was pressed before the request's buttons were on")

    def test_a_press_held_since_before_the_window_opened_never_counts(self):
        self.arming.pressed(50, 60)
        self.later(0.2)
        self.arming.show("window", "cmd-1")
        self.later(2.0)
        self.assertIsNotNone(self.arming.refusal("window", "cmd-1", "LEFTMOUSE", "RELEASE", 50, 60))

    def test_a_press_somewhere_else_never_counts(self):
        # Blender's popups run a button on a bare release: a press elsewhere
        # dragged onto Approve is not a click on it.
        self.arming.show("window", "cmd-1")
        self.later(approval.ARM_SECONDS)
        self.assertEqual(self.click("window", release=(50 + approval.PRESS_SLOP_PX + 1, 60)),
                         "the mouse button was pressed somewhere else")
        self.assertIsNone(self.click("window", release=(52, 61)))

    def test_no_key_ever_answers(self):
        self.arming.drawn("panel", "cmd-1")
        self.later(5)
        self.arming.pressed(50, 60)
        for event_type, value in (("RET", "PRESS"), ("RET", "RELEASE"), ("NUMPAD_ENTER", "PRESS"),
                                  ("SPACE", "PRESS"), ("LEFTMOUSE", "PRESS"), ("TIMER", "NOTHING")):
            why = self.arming.refusal("panel", "cmd-1", event_type, value, 50, 60)
            self.assertTrue(why.startswith("it was not a mouse click"), (event_type, why))

    def test_each_place_is_armed_on_its_own_and_the_window_every_time_it_opens(self):
        self.arming.drawn("panel", "cmd-1")
        self.later(approval.ARM_SECONDS)
        self.arming.show("window", "cmd-1")
        self.assertIsNone(self.click("panel"))
        self.assertIsNotNone(self.click("window"))
        self.later(approval.ARM_SECONDS)
        self.assertIsNone(self.click("window"))
        self.arming.show("window", "cmd-1")  # opened again
        self.assertIsNotNone(self.click("window"))
        self.arming.drawn("panel", "cmd-1")  # drawn again: still armed
        self.assertIsNone(self.click("panel"))

    def test_an_answer_counts_only_for_the_request_shown(self):
        self.arming.drawn("panel", "cmd-1")
        self.later(approval.ARM_SECONDS)
        self.assertIsNotNone(self.click("panel", "cmd-2"), "never shown, never armed")
        self.arming.drawn("panel", "cmd-2")
        self.assertIsNotNone(self.click("panel", "cmd-2"))
        self.arming.keep_only({"cmd-2"})
        self.assertIsNotNone(self.click("panel", "cmd-1"), "no longer waiting")

    def test_the_panel_redraws_once_when_buttons_turn_on(self):
        self.arming.drawn("panel", "cmd-1")
        self.assertFalse(self.arming.newly_armed())
        self.later(approval.ARM_SECONDS)
        self.assertTrue(self.arming.newly_armed())
        self.assertFalse(self.arming.newly_armed())
        self.assertTrue(self.arming.armed("panel", "cmd-1"))


if __name__ == "__main__":
    unittest.main()
