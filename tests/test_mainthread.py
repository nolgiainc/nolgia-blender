# SPDX-License-Identifier: GPL-3.0-or-later
"""Handing commands to the main thread, and asking the person first."""

import sys
import threading
import time
import unittest

import support  # noqa: F401

from core.commands import Command
from core.mainthread import ApprovalRequest, MainThreadExecutor, NoQuiet, Quiet


def command(kind="run", cid="c1", timeout=60):
    return Command({"id": cid, "kind": kind, "args": {"code": "x = 1"}, "timeout_seconds": timeout},
                   received_at=0.0)


class Harness:
    def __init__(self, ask=True, can_ask=True):
        self.now = 0.0
        self.ran = []
        self.statuses = []
        self.ask = ask
        self.executor = MainThreadExecutor(
            run=self.run,
            needs_approval=self.needs_approval,
            can_ask=lambda: can_ask,
            on_status=lambda cid, status, detail="": self.statuses.append((cid, status)),
            clock=lambda: self.now,
        )

    def run(self, ticket):
        self.ran.append((ticket.command.id, threading.get_ident()))
        return True, {"ran": ticket.command.id}, None

    def needs_approval(self, ticket):
        if self.ask and ticket.command.kind == "run":
            return ApprovalRequest("Your agent wants to run Python in Blender", ["x = 1"], "nobody to ask")
        return None


class Executor(unittest.TestCase):
    def test_runs_on_the_pumping_thread(self):
        h = Harness(ask=False)
        tickets = []
        submitter = threading.Thread(target=lambda: tickets.append(h.executor.submit(command("info"))))
        submitter.start()
        submitter.join()
        self.assertEqual(h.ran, [])  # nothing runs until the main thread pumps
        h.executor.pump()
        self.assertEqual(h.ran, [("c1", threading.get_ident())])
        self.assertTrue(tickets[0].wait(0))
        self.assertEqual((tickets[0].ok, tickets[0].result), (True, {"ran": "c1"}))
        self.assertEqual(h.statuses, [("c1", "running")])

    def test_approve(self):
        h = Harness()
        ticket = h.executor.submit(command())
        h.executor.pump()
        self.assertFalse(ticket.done)
        self.assertEqual([p.id for p in h.executor.approvals], ["c1"])
        self.assertEqual(h.statuses, [("c1", "approval")])
        self.assertTrue(h.executor.approve("c1"))
        h.executor.pump()
        self.assertTrue(ticket.ok)
        self.assertEqual(h.executor.approvals, [])

    def test_deny(self):
        h = Harness()
        ticket = h.executor.submit(command())
        h.executor.pump()
        h.executor.deny("c1")
        h.executor.pump()
        self.assertFalse(ticket.ok)
        self.assertIn("clicked Deny", ticket.error)
        self.assertEqual(h.ran, [])

    def test_nobody_answers_in_time(self):
        h = Harness()
        ticket = h.executor.submit(command(timeout=30))
        h.executor.pump()
        h.now = 26.0
        h.executor.pump()
        self.assertFalse(ticket.done)
        h.now = 27.0  # 3 s before expiry, so the refusal still reaches the API
        h.executor.pump()
        self.assertFalse(ticket.ok)
        self.assertIn("in time", ticket.error)

    def test_headless_cannot_ask(self):
        h = Harness(can_ask=False)
        ticket = h.executor.submit(command())
        h.executor.pump()
        self.assertEqual((ticket.ok, ticket.error), (False, "nobody to ask"))

    def test_switch_off_fails_queued_and_waiting(self):
        h = Harness()
        waiting = h.executor.submit(command(cid="w"))
        h.executor.pump()
        queued = h.executor.submit(command("info", cid="q"))
        h.executor.close("switched off")
        h.executor.pump()
        self.assertEqual((waiting.ok, waiting.error), (False, "switched off"))
        self.assertEqual((queued.ok, queued.error), (False, "switched off"))
        h.executor.reopen()
        again = h.executor.submit(command("info", cid="a"))
        h.executor.pump()
        self.assertTrue(again.ok)

    def test_a_crashing_runner_still_answers(self):
        h = Harness(ask=False)
        h.executor._run = lambda ticket: 1 / 0
        ticket = h.executor.submit(command("info"))
        h.executor.pump()
        self.assertFalse(ticket.ok)
        self.assertIn("division by zero", ticket.error)



class Traced(threading.Thread):
    """A thread that counts every Python event it runs (calls, lines,
    returns), to show it runs no Python while it is parked."""

    def __init__(self, body):
        super().__init__(daemon=True)
        self.body = body
        self.events = 0

    def run(self):
        def tracer(frame, event, arg):
            self.events += 1
            return tracer

        sys.settrace(tracer)
        try:
            self.body()
        finally:
            sys.settrace(None)


class QuietTest(unittest.TestCase):
    """mainthread.Quiet: while the main thread holds it (Blender runs an
    operator inside a context override), the plugin's own threads run no
    Python, which is what crashed Blender 4.5 after a save (2026-11-01)."""

    def test_work_waits_while_held_and_runs_once_released(self):
        quiet = Quiet()
        ran = []
        self.assertTrue(quiet.hold())
        worker = threading.Thread(target=lambda: quiet.work().__enter__() or ran.append(1), daemon=True)
        worker.start()
        time.sleep(0.3)
        self.assertEqual(ran, [], "work started during the hold")
        quiet.release()
        worker.join(5)
        self.assertEqual(ran, [1])

    def test_a_parked_thread_runs_no_python_at_all(self):
        quiet = Quiet()
        quiet.hold()
        done = []

        def body():
            with quiet.work():
                done.append(1)

        thread = Traced(body)
        thread.start()
        time.sleep(0.2)  # long enough to reach the wait
        parked = thread.events
        time.sleep(0.6)
        self.assertEqual(thread.events, parked, "the parked thread ran Python during the hold")
        self.assertEqual(done, [])
        quiet.release()
        thread.join(5)
        self.assertEqual(done, [1])
        self.assertGreater(thread.events, parked)

    def test_hold_waits_for_work_already_under_way(self):
        quiet = Quiet()
        started = threading.Event()

        def body():
            with quiet.work():
                started.set()
                time.sleep(0.4)

        worker = threading.Thread(target=body, daemon=True)
        worker.start()
        started.wait(5)
        t0 = time.monotonic()
        self.assertTrue(quiet.hold(settle=5.0))
        self.assertGreaterEqual(time.monotonic() - t0, 0.3)
        quiet.release()
        worker.join(5)

    def test_hold_stops_waiting_after_settle(self):
        quiet = Quiet()
        started, finish = threading.Event(), threading.Event()

        def body():
            with quiet.work():
                started.set()
                finish.wait(5)

        worker = threading.Thread(target=body, daemon=True)
        worker.start()
        started.wait(5)
        t0 = time.monotonic()
        self.assertFalse(quiet.hold(settle=0.2))
        self.assertLess(time.monotonic() - t0, 1.5)
        self.assertTrue(quiet.held)
        quiet.release()
        finish.set()
        worker.join(5)

    def test_work_with_a_limit_goes_ahead_after_it(self):
        quiet = Quiet()
        quiet.hold()
        ran = []

        def body():
            with quiet.work(limit=0.3):
                ran.append(time.monotonic())

        t0 = time.monotonic()
        worker = threading.Thread(target=body, daemon=True)
        worker.start()
        worker.join(5)
        self.assertEqual(len(ran), 1)
        self.assertGreaterEqual(ran[0] - t0, 0.25)
        quiet.release()

    def test_holds_nest(self):
        quiet = Quiet()
        quiet.hold()
        with quiet.held_quiet():
            self.assertTrue(quiet.held)
        self.assertTrue(quiet.held, "the outer hold is still on")
        quiet.release()
        self.assertFalse(quiet.held)

    def test_close_lets_every_waiter_go(self):
        quiet = Quiet()
        quiet.hold()
        ran = []
        worker = threading.Thread(target=lambda: quiet.work().__enter__() or ran.append(1), daemon=True)
        worker.start()
        time.sleep(0.1)
        quiet.close()
        worker.join(5)
        self.assertEqual(ran, [1])

    def test_no_quiet_never_waits(self):
        quiet = NoQuiet()
        quiet.hold()
        with quiet.work():
            pass
        with quiet.held_quiet():
            pass
        self.assertFalse(quiet.held)


if __name__ == "__main__":
    unittest.main()
