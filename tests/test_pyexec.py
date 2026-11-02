# SPDX-License-Identifier: GPL-3.0-or-later
"""The `run` executor: result value, output capture, tracebacks, timeout."""

import contextlib
import json
import math
import sys
import threading
import time
import unittest

import support  # noqa: F401

from core.pyexec import run_python, to_jsonable


class Vec:
    """Stands in for mathutils.Vector: a sequence of floats."""

    def __init__(self, *xs):
        self.xs = xs

    def __len__(self):
        return len(self.xs)

    def __iter__(self):
        return iter(self.xs)


class FakeID:
    """Stands in for a bpy data block."""

    bl_rna = object()

    def __init__(self, name):
        self.name = name


class RunPython(unittest.TestCase):
    def test_result_and_stdout(self):
        out = run_python("print('hi')\nresult = {'a': 1, 'b': [1, 2]}", {})
        self.assertTrue(out.ok)
        self.assertEqual(out.value, {"a": 1, "b": [1, 2]})
        self.assertEqual(out.stdout, "hi\n")
        self.assertEqual(out.result(), {"value": {"a": 1, "b": [1, 2]}, "stdout": "hi\n", "stderr": ""})

    def test_namespace_is_available(self):
        out = run_python("result = bpy_marker + 1", {"bpy_marker": 41})
        self.assertEqual(out.value, 42)

    def test_no_result_is_null(self):
        self.assertIsNone(run_python("x = 1", {}).value)

    def test_stderr_captured(self):
        out = run_python("import sys\nsys.stderr.write('warn\\n')", {})
        self.assertEqual(out.stderr, "warn\n")

    def test_traceback_names_the_line(self):
        out = run_python("a = 1\nb = 0\nc = a / b\n", {})
        self.assertFalse(out.ok)
        self.assertIn("Traceback (most recent call last)", out.error)
        self.assertIn("line 3", out.error)
        self.assertIn("c = a / b", out.error)
        self.assertIn("ZeroDivisionError", out.error)
        self.assertNotIn("pyexec.py", out.error)
        self.assertIsNone(out.value)

    def test_syntax_error(self):
        out = run_python("def broken(:\n", {})
        self.assertFalse(out.ok)
        self.assertIn("SyntaxError", out.error)

    def test_output_before_failure_is_kept(self):
        out = run_python("print('step 1')\nraise RuntimeError('nope')", {})
        self.assertEqual(out.stdout, "step 1\n")
        self.assertIn("RuntimeError: nope", out.error)

    def test_exit_is_reported_not_obeyed(self):
        out = run_python("import sys\nsys.exit(2)", {})
        self.assertFalse(out.ok)
        self.assertIn("exit(2)", out.error)

    def test_other_threads_output_is_not_captured(self):
        seen = []
        real = sys.stdout

        class Tee:
            def write(self, text):
                seen.append(text)
                return len(text)

            def flush(self):
                pass

        sys.stdout = Tee()
        try:
            code = "import threading\nt = threading.Thread(target=lambda: print('from thread'))\n" \
                   "t.start(); t.join()\nprint('from code')"
            out = run_python(code, {})
        finally:
            sys.stdout = real
        self.assertEqual(out.stdout, "from code\n")
        self.assertEqual("".join(seen), "from thread\n")

    def test_output_is_capped(self):
        out = run_python("print('x' * 400000)", {})
        self.assertLess(len(out.stdout), 300000)
        self.assertIn("more characters cut", out.stdout)

    def test_timeout_stops_a_loop(self):
        t0 = time.time()
        out = run_python("while True:\n    pass\n", {}, timeout=0.5)
        self.assertFalse(out.ok)
        self.assertTrue(out.timed_out)
        self.assertIn("longer than 0.5 seconds", out.error)
        self.assertLess(time.time() - t0, 5)

    def test_timeout_is_not_swallowed_by_except_exception(self):
        # The work inside `try` matters on Python 3.10: it skips the check for
        # a pending timeout when a loop jumps straight back to a `try`.
        code = ("while True:\n    try:\n        for _ in range(1000):\n            pass\n"
                "    except Exception:\n        pass\n")
        out = run_python(code, {}, timeout=0.3)
        self.assertTrue(out.timed_out)

    def test_timeout_does_not_fire_after_finishing(self):
        out = run_python("result = 1", {}, timeout=0.2)
        self.assertTrue(out.ok)
        time.sleep(0.4)  # a stray timeout would land here and fail the test
        x = 0
        for _ in range(100000):
            x += 1
        self.assertEqual(x, 100000)

    def test_the_context_opens_after_the_timeout_thread_waits_and_closes_before_it_stops(self):
        # Blender: the code runs in the window and 3D view override, where no
        # other thread may run Python (mainthread.Quiet).
        seen = {}

        def timers():
            return [t for t in threading.enumerate() if isinstance(t, threading.Timer)]

        @contextlib.contextmanager
        def context():
            timer = timers()[-1]
            frame = sys._current_frames()[timer.ident]
            seen["open_in"] = frame.f_code.co_name
            yield
            seen["stopped_at_close"] = timer.finished.is_set()
            seen["alive_at_close"] = timer.is_alive()

        out = run_python("result = 7", {}, timeout=30, context=context)
        self.assertTrue(out.ok, out.error)
        self.assertEqual(out.value, 7)
        self.assertEqual(seen["open_in"], "wait", "the timeout's thread was not waiting yet")
        self.assertFalse(seen["stopped_at_close"])
        self.assertTrue(seen["alive_at_close"])

    def test_a_failure_inside_the_context_keeps_its_traceback(self):
        @contextlib.contextmanager
        def context():
            yield

        out = run_python("x = 1\nraise ValueError('nope')", {}, timeout=30, context=context)
        self.assertFalse(out.ok)
        self.assertIn("ValueError: nope", out.error)
        self.assertIn("line 2", out.error)

    def test_runs_on_the_calling_thread(self):
        out = run_python("import threading\nresult = threading.get_ident()", {})
        self.assertEqual(out.value, threading.get_ident())


class ToJsonable(unittest.TestCase):
    def test_blender_like_values(self):
        value = {
            "vec": Vec(1.0, 2.0, 3.0),
            "matrix": [Vec(1, 0), Vec(0, 1)],
            "obj": FakeID("Cube"),
            "tuple": (1, 2),
            "set": {3},
            "nan": float("nan"),
            "inf": math.inf,
            "bytes": b"abc",
            1: "int key",
        }
        out = to_jsonable(value)
        json.dumps(out)
        self.assertEqual(out["vec"], [1.0, 2.0, 3.0])
        self.assertEqual(out["matrix"], [[1, 0], [0, 1]])
        self.assertEqual(out["obj"], "Cube")
        self.assertEqual(out["tuple"], [1, 2])
        self.assertEqual(out["set"], [3])
        self.assertEqual((out["nan"], out["inf"]), ("nan", "inf"))
        self.assertEqual(out["bytes"], "abc")
        self.assertEqual(out["1"], "int key")

    def test_unknown_objects_become_repr(self):
        class Thing:
            def __repr__(self):
                return "<Thing>"
        self.assertEqual(to_jsonable(Thing()), "<Thing>")

    def test_deep_nesting_is_cut(self):
        deep = []
        cur = deep
        for _ in range(100):
            cur.append([])
            cur = cur[0]
        json.dumps(to_jsonable(deep))


if __name__ == "__main__":
    unittest.main()
