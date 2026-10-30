# SPDX-License-Identifier: GPL-3.0-or-later
"""The network worker against the mock API, with a thread standing in for
Blender's main thread."""

import socket
import threading
import time
import unittest
from unittest import mock

import support

from core import PLUGIN_VERSION
from core.api import ApiClient
from core.commands import CAPABILITIES, ActivityLog
from core.mainthread import MainThreadExecutor
from core import util
from core import worker as worker_module
from core.worker import BridgeWorker, Status


def until(predicate, timeout=10.0, step=0.02):
    end = time.time() + timeout
    while time.time() < end:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    raise AssertionError("condition not met within %.1fs" % timeout)


class Harness:
    def __init__(self, server_url, token=support.TOKEN, run=None, heartbeat=0.2, prepare=None, finish=None):
        self.activity = ActivityLog()
        self.main_ident = None
        self.executor = MainThreadExecutor(run=run or self.default_run, on_status=self.activity.update)
        self.snapshot = {"document": {"name": ""}, "allow_agent": True, "app_version": "4.5.8"}
        self.auth_failed = []
        self.log = []
        self.worker = BridgeWorker(
            ApiClient(server_url, token), self.executor, self.activity, "inst-test", lambda: self.snapshot,
            prepare=prepare, finish=finish, on_auth_failed=self.auth_failed.append, log=self.log.append,
            heartbeat_interval=heartbeat, machine_name="test-box",
        )
        self._stop = threading.Event()
        self._pump = threading.Thread(target=self._loop, daemon=True)

    def default_run(self, ticket):
        return True, {"kind": ticket.command.kind, "args": ticket.command.args}, None

    def _loop(self):
        self.main_ident = threading.get_ident()
        while not self._stop.is_set():
            self.executor.pump()
            time.sleep(0.005)

    def start(self):
        self._pump.start()
        self.worker.start()
        return self

    def close(self):
        self.worker.stop()
        self.worker.finished.wait(10)
        self._stop.set()


class WorkerTest(unittest.TestCase):
    def setUp(self):
        self.server = support.start_mock(poll_wait_seconds=2)
        self.harness = None

    def tearDown(self):
        if self.harness is not None:
            self.harness.worker.kill()
            self.harness.close()
        self.server.stop()

    def start(self, **kwargs):
        self.harness = Harness(self.server.base_url, **kwargs).start()
        until(lambda: self.harness.worker.state == Status.CONNECTED)
        return self.harness

    def live_sessions(self):
        return support.call(self.server, "GET", "/v1/bridge/sessions")[1]["sessions"]

    def test_registers_with_the_spec_body(self):
        self.start()
        body = self.server.state.heartbeats[-1]["body"]
        expected = {
            "instance_id": "inst-test", "app": "blender", "app_version": "4.5.8",
            "plugin_version": PLUGIN_VERSION, "machine_name": "test-box",
            "document": {"name": ""}, "capabilities": list(CAPABILITIES), "allow_agent": True,
        }
        if util.os_name():
            expected["os"] = util.os_name()
        self.assertEqual(body, expected)
        self.assertEqual(self.live_sessions()[0].get("os"), util.os_name() or None)

    def test_an_api_without_the_os_field_still_connects(self):
        # An API from before sessions carried the OS refuses the field as
        # unknown; the plugin then stops sending it instead of failing.
        self.server.state.bridge_os = False
        h = self.start()
        body = self.server.state.heartbeats[-1]["body"]
        self.assertNotIn("os", body)
        self.assertEqual(h.worker.state, Status.CONNECTED)

    def test_a_name_looked_up_is_kept_for_photoshop(self):
        with mock.patch.object(worker_module, "computer_name", return_value="Ada's MacBook Pro"), \
                mock.patch.object(worker_module, "remember_computer_name") as remember:
            w = BridgeWorker(ApiClient(self.server.base_url, support.TOKEN), None, ActivityLog(), "inst-x", lambda: {})
            self.assertEqual(w.machine_name, "Ada's MacBook Pro")
            w._set_status = lambda state, text: None
            with mock.patch.object(threading, "Thread"):
                w.start()
        remember.assert_called_once()
        self.assertEqual(remember.call_args[0][0], "Ada's MacBook Pro")

    def test_fields_are_kept_within_the_api_limits(self):
        h = Harness(self.server.base_url)
        h.worker.machine_name = "box\n" + "m" * 300
        h.snapshot = {"document": {"name": "n" * 600, "path": "p" * 5000}, "app_version": "v" * 100}
        body = h.worker.payload()
        self.assertEqual(body["machine_name"], "box" + "m" * 125)
        self.assertEqual(len(body["app_version"]), 64)
        self.assertEqual(body["document"], {"name": "n" * 512})
        self.assertEqual(h.worker.api.register_session(body)["document"], {"name": "n" * 512})
        self.assertEqual(len(self.live_sessions()), 1)

    def test_heartbeats_keep_coming(self):
        self.start(heartbeat=0.1)
        until(lambda: len(self.server.state.heartbeats) >= 4)

    def test_heartbeat_at_once_when_asked(self):
        h = self.start(heartbeat=30)
        count = len(self.server.state.heartbeats)
        h.snapshot = dict(h.snapshot, document={"name": "shot.blend", "path": "/p/shot.blend"}, allow_agent=False)
        h.worker.request_heartbeat()
        until(lambda: len(self.server.state.heartbeats) > count, timeout=3)
        body = self.server.state.heartbeats[-1]["body"]
        self.assertEqual(body["document"], {"name": "shot.blend", "path": "/p/shot.blend"})
        self.assertFalse(body["allow_agent"])

    def test_command_round_trip(self):
        h = self.start()
        cid = support.enqueue(self.server, "save", {"path": "/tmp/a.blend"}, caller="agent")
        done = support.wait_command(self.server, cid)
        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(done["result"], {"kind": "save", "args": {"path": "/tmp/a.blend"}})
        until(lambda: h.activity.items() and h.activity.items()[0]["status"] == "succeeded")
        item = h.activity.items()[0]
        self.assertEqual((item["kind"], item["caller"]), ("save", "agent"))

    def test_bad_args_fail_before_reaching_blender(self):
        ran = []
        h = self.start(run=lambda t: ran.append(t) or (True, {}, None))
        cid = support.enqueue(self.server, "preview", {"width": 99999})
        done = support.wait_command(self.server, cid)
        self.assertEqual(done["status"], "failed")
        self.assertIn("at most 1920", done["error"])
        self.assertEqual(ran, [])

    def test_failure_reaches_the_caller(self):
        self.start(run=lambda t: (False, {"value": None, "stdout": "a", "stderr": ""}, "Traceback ...\nValueError: x"))
        cid = support.enqueue(self.server, "run", {"language": "python", "code": "raise ValueError('x')"})
        done = support.wait_command(self.server, cid)
        self.assertEqual((done["status"], done["error"]), ("failed", "Traceback ...\nValueError: x"))
        self.assertEqual(done["result"]["stdout"], "a")

    def test_network_work_runs_off_the_main_thread(self):
        seen = {}

        def prepare(command):
            seen["prepare"] = threading.get_ident()
            return {"downloaded": True}

        def run(ticket):
            seen["run"] = threading.get_ident()
            return True, {"_upload": "file", "prepared": ticket.prepared}, None

        def finish(command, result):
            seen["finish"] = threading.get_ident()
            return {"asset_id": "a-1", "prepared": result["prepared"]}

        h = self.start(run=run, prepare=prepare, finish=finish)
        cid = support.enqueue(self.server, "import_asset", {"asset_id": "x"})
        done = support.wait_command(self.server, cid)
        self.assertEqual(done["result"], {"asset_id": "a-1", "prepared": {"downloaded": True}})
        self.assertEqual(seen["run"], h.main_ident)
        self.assertNotEqual(seen["prepare"], h.main_ident)
        self.assertEqual(seen["prepare"], seen["finish"])

    def test_stop_closes_the_session(self):
        h = self.start()
        sid = h.worker.session_id
        h.worker.stop()
        self.assertTrue(h.worker.finished.wait(5))
        self.assertEqual([d["session_id"] for d in self.server.state.deleted_sessions], [sid])
        self.assertEqual(self.live_sessions(), [])
        self.assertEqual(h.worker.state, Status.OFF)

    def test_stop_during_a_command_reports_it_first(self):
        release = threading.Event()

        def slow(ticket):
            release.wait(10)
            return True, {"finished": True}, None

        h = self.start(run=slow)
        cid = support.enqueue(self.server, "info")
        until(lambda: h.worker.busy_with == cid)
        h.worker.stop()
        time.sleep(0.3)
        self.assertEqual(self.server.state.deleted_sessions, [], "closed while a command was running")
        release.set()
        self.assertTrue(h.worker.finished.wait(5))
        done = support.wait_command(self.server, cid)
        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(len(self.server.state.deleted_sessions), 1)
        result_at = next(r for r in self.server.state.requests if r["path"].endswith("/result"))
        delete_at = next(r for r in self.server.state.requests if r["method"] == "DELETE")
        reqs = self.server.state.requests
        self.assertLess(reqs.index(result_at), reqs.index(delete_at))

    def test_cancelled_while_running(self):
        release = threading.Event()
        h = self.start(run=lambda t: (release.wait(10), (True, {}, None))[1])
        cid = support.enqueue(self.server, "info")
        until(lambda: h.worker.busy_with == cid)
        support.call(self.server, "POST", "/v1/bridge/commands/%s/cancel" % cid)
        release.set()
        until(lambda: h.activity.items()[0]["status"] == "cancelled")
        # and it keeps working
        cid2 = support.enqueue(self.server, "info")
        self.assertEqual(support.wait_command(self.server, cid2)["status"], "succeeded")

    def test_refused_token_stops_without_retrying(self):
        self.harness = Harness(self.server.base_url, token="wrong").start()
        self.assertTrue(self.harness.worker.finished.wait(5))
        self.assertEqual(self.harness.worker.state, Status.SIGNED_OUT)
        self.assertEqual(len(self.harness.auth_failed), 1)
        self.assertIn("Sign in again", self.harness.auth_failed[0])
        self.assertEqual(self.server.state.deleted_sessions, [])

    def test_registers_again_after_a_disconnect_on_the_server(self):
        h = self.start()
        sid = h.worker.session_id
        count = len(self.server.state.heartbeats)
        support.call(self.server, "DELETE", "/v1/bridge/sessions/%s" % sid)  # e.g. another client closed it
        until(lambda: len(self.server.state.heartbeats) > count, timeout=5)
        until(lambda: self.live_sessions(), timeout=5)
        self.assertEqual(h.worker.session_id, sid)  # same session, reconnected
        cid = support.enqueue(self.server, "info")
        self.assertEqual(support.wait_command(self.server, cid)["status"], "succeeded")

    def test_413_is_not_retried(self):
        h = self.start()
        huge = {"status": "succeeded", "result": {"value": "x" * (1 << 20)}}
        with mock.patch.object(worker_module, "result_body", lambda ok, result, error: huge):
            cid = support.enqueue(self.server, "info")
            done = support.wait_command(self.server, cid)
        self.assertEqual(done["status"], "failed")
        until(lambda: h.activity.items()[0]["status"] == "failed")
        posts = [r for r in self.server.state.requests if r["path"].endswith("/%s/result" % cid)]
        self.assertEqual([r["status"] for r in posts], [413])

    def test_a_late_result_is_dropped(self):
        release = threading.Event()
        h = self.start(run=lambda t: (release.wait(10), (True, {}, None))[1])
        cid = support.enqueue(self.server, "info")
        until(lambda: h.worker.busy_with == cid)
        self.server.state.commands[cid]["expires_at"] = time.time() - 0.01
        release.set()
        until(lambda: h.activity.items()[0]["status"] == "expired")
        posts = [r for r in self.server.state.requests if r["path"].endswith("/%s/result" % cid)]
        self.assertEqual([r["status"] for r in posts], [409])

    def test_agent_commands_follow_allow_agent(self):
        h = self.start(heartbeat=30)
        cid = support.enqueue(self.server, "info", caller="agent")
        self.assertEqual(support.wait_command(self.server, cid)["caller"], "agent")
        h.snapshot = dict(h.snapshot, allow_agent=False)
        h.worker.request_heartbeat()
        until(lambda: self.server.state.heartbeats[-1]["body"]["allow_agent"] is False, timeout=3)
        status, data = support.call(self.server, "POST", "/v1/bridge/commands",
                                    {"app": "blender", "kind": "info"}, headers={"X-Nolgia-Surface": "hermes"})
        self.assertEqual((status, data["code"]), (403, "agent_not_allowed"))

    def test_registers_again_when_the_session_disappears(self):
        h = self.start()
        old = h.worker.session_id
        with self.server.state.cond:
            self.server.state.sessions.clear()
            self.server.state.cond.notify_all()
        until(lambda: h.worker.session_id not in (None, old), timeout=10)
        cid = support.enqueue(self.server, "info")
        self.assertEqual(support.wait_command(self.server, cid)["status"], "succeeded")

    def test_comes_back_when_the_api_comes_back(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.harness = Harness("http://127.0.0.1:%d/v1" % port).start()
        until(lambda: self.harness.worker.state == Status.RETRYING, timeout=5)
        self.assertIn("Cannot reach NOLGIA", self.harness.worker.status_text)
        self.assertIn("Trying again in", self.harness.worker.status_text)
        late = support.start_mock(port=port)
        try:
            until(lambda: self.harness.worker.state == Status.CONNECTED, timeout=15)
        finally:
            self.harness.worker.kill()
            self.harness.close()
            self.harness = None
            late.stop()



class Relay:
    """A TCP relay in front of the mock that can break every open connection
    (the plugin sees ECONNRESET) or refuse new ones (an outage). One thread
    and a selector, so a break reaches every socket at once."""

    def __init__(self, target_url):
        import selectors
        from urllib.parse import urlparse
        u = urlparse(target_url)
        self.target = (u.hostname, u.port)
        self.refusing = False
        self.pairs = {}  # socket -> its other end
        self.sel = selectors.DefaultSelector()
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(64)
        self.srv.setblocking(False)
        self.base_url = "http://127.0.0.1:%d/v1" % self.srv.getsockname()[1]
        self.wake_r, self.wake_w = socket.socketpair()
        self.wake_r.setblocking(False)
        self.sel.register(self.srv, selectors.EVENT_READ, "accept")
        self.sel.register(self.wake_r, selectors.EVENT_READ, "wake")
        self.orders = []
        self.closed = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _reset(self, sock):
        try:
            self.sel.unregister(sock)
        except (KeyError, ValueError):
            pass
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
        except OSError:
            pass
        sock.close()

    def _drop(self, sock):
        other = self.pairs.pop(sock, None)
        self._reset(sock)
        if other is not None:
            self.pairs.pop(other, None)
            self._reset(other)

    def _run(self):
        import selectors
        while not self.closed:
            for key, _ in self.sel.select(0.2):
                if key.data == "wake":
                    try:
                        self.wake_r.recv(64)
                    except OSError:
                        pass
                elif key.data == "accept":
                    try:
                        client, _ = self.srv.accept()
                    except OSError:
                        continue
                    if self.refusing:
                        self._reset(client)
                        continue
                    try:
                        upstream = socket.create_connection(self.target, timeout=5)
                    except OSError:
                        self._reset(client)
                        continue
                    for a, b in ((client, upstream), (upstream, client)):
                        a.setblocking(False)
                        self.pairs[a] = b
                        self.sel.register(a, selectors.EVENT_READ, "pipe")
                else:
                    sock = key.fileobj
                    try:
                        data = sock.recv(65536)
                    except (BlockingIOError, InterruptedError):
                        continue
                    except OSError:
                        data = b""
                    other = self.pairs.get(sock)
                    if not data or other is None:
                        self._drop(sock)
                        continue
                    try:
                        other.setblocking(True)
                        other.sendall(data)
                        other.setblocking(False)
                    except OSError:
                        self._drop(sock)
            while self.orders:
                order = self.orders.pop(0)
                if isinstance(order, threading.Event):
                    order.set()
                elif order == "break":
                    for sock in list(self.pairs):
                        if sock in self.pairs:
                            self._drop(sock)
        for sock in list(self.pairs):
            if sock in self.pairs:
                self._drop(sock)
        self.srv.close()

    def _order(self, order):
        done = threading.Event()
        self.orders.append(order)
        self.orders.append(done)
        self.wake_w.send(b"x")
        # Wait until the relay thread has carried it out.
        end = time.time() + 5
        while done in self.orders and time.time() < end:
            time.sleep(0.01)

    def break_all(self):
        self._order("break")

    def outage(self, on):
        self.refusing = on
        if on:
            self.break_all()

    def close(self):
        self.closed = True
        self.wake_w.send(b"x")
        self.thread.join(5)


class ReconnectTest(unittest.TestCase):
    """The owner's Mac test 4: a broken socket, then 5 minutes silent."""

    def setUp(self):
        self.server = support.start_mock(poll_wait_seconds=2)
        self.relay = Relay(self.server.base_url)
        self.harness = None

    def tearDown(self):
        if self.harness is not None:
            self.harness.worker.kill()
            self.harness.close()
        self.relay.close()
        self.server.stop()

    def harness_on(self, url, **kwargs):
        self.harness = Harness(url, **kwargs)
        for key, value in kwargs.pop("worker", {}).items():
            setattr(self.harness.worker, key, value)
        return self.harness

    def test_a_connection_that_breaks_mid_request_is_tried_again_at_once(self):
        h = self.harness_on(self.relay.base_url, heartbeat=20).start()
        until(lambda: h.worker.state == Status.CONNECTED)
        sid = h.worker.session_id
        self.relay.break_all()
        t0 = time.monotonic()
        # The mock still holds the broken poll until its wait ends (2 s) and could hand it the
        # command; the API's poll generation drops such a poll at once.
        time.sleep(2.5)
        cid = support.enqueue(self.server, "info")
        self.assertEqual(support.wait_command(self.server, cid)["status"], "succeeded")
        self.assertLess(time.monotonic() - t0, 4.5)
        self.assertEqual(h.worker.session_id, sid)
        self.assertNotIn(Status.RETRYING, [state for state in getattr(h, "states", [])])

    def test_back_within_the_retry_limit_once_an_outage_ends_however_long(self):
        h = self.harness_on(self.relay.base_url, heartbeat=20)
        h.worker.network_retry_max = 2.0
        h.start()
        until(lambda: h.worker.state == Status.CONNECTED)
        self.relay.outage(True)
        until(lambda: h.worker.state == Status.RETRYING)
        time.sleep(14)  # long enough for the backoff to reach its limit (and well past 2 s without one)
        self.relay.outage(False)
        t0 = time.monotonic()
        until(lambda: h.worker.state == Status.CONNECTED, timeout=10)
        back = time.monotonic() - t0
        self.assertLessEqual(back, 3.5, "back after %.1f s" % back)
        cid = support.enqueue(self.server, "info")
        self.assertEqual(support.wait_command(self.server, cid)["status"], "succeeded")
        self.assertTrue(any(("Trying again in 1 s." in line or "Trying again in 2 s." in line) for line in h.log), h.log)

    def test_network_waits_stop_at_the_limit_but_slow_down_answers_do_not(self):
        w = BridgeWorker(ApiClient(self.server.base_url, support.TOKEN), None, ActivityLog(), "x", lambda: {},
                         machine_name="m")
        backoff = util.Backoff(1.0, 60.0)
        streak = {"first": True}
        from core.api import ApiError, NetworkError
        self.assertIsNone(w._retry_delay(NetworkError("broken"), backoff, streak), "the first one at once")
        delays = [w._retry_delay(NetworkError("down"), backoff, streak) for _ in range(12)]
        self.assertTrue(all(d <= worker_module.NETWORK_RETRY_MAX for d in delays), delays)
        slow = ApiError(503, title="busy")
        later = [w._retry_delay(slow, backoff, streak) for _ in range(3)]
        self.assertTrue(any(d > worker_module.NETWORK_RETRY_MAX for d in later), later)

    def test_a_due_heartbeat_goes_out_when_a_poll_comes_back(self):
        h = self.harness_on(self.server.base_url, heartbeat=1000).start()  # a timer that, as under App Nap, does not come in time
        until(lambda: h.worker.state == Status.CONNECTED)
        first = h.worker.last_heartbeat_at
        h.worker.heartbeat_interval = 2.0  # due after 2 s; only a poll's answer can wake it now
        until(lambda: h.worker.last_heartbeat_at > first, timeout=8)


if __name__ == "__main__":
    unittest.main()
