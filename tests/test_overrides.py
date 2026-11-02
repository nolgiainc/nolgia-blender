# SPDX-License-Identifier: GPL-3.0-or-later
"""Context overrides in the add-on, read from its source.

NOLGIA for Blender 0.2.3 crashed Blender 4.5 (2026-11-01, and in the same
place on 2026-10-06): the undo push after a `run` ran inside a
`bpy.context.temp_override`, where Blender looks up context through Python
while it has let go of Python's lock, so a network thread running Python at
that moment broke Python's memory. tools/repro_save_crash.py shows it in a
real Blender. These checks keep the two rules that fixed it:

1. every override the add-on opens holds QUIET in the same `with`, so the
   plugin's own threads wait (core/mainthread.py, Quiet);
2. the undo push opens no override at all.
"""

import ast
import os
import unittest

import support

ADDON_FILES = ("__init__.py", "approval.py", "ops.py", "runtime.py", "ui.py")


def parse(name):
    with open(os.path.join(support.EXT_DIR, name), encoding="utf-8") as handle:
        return ast.parse(handle.read(), name)


def call_name(node):
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute):
            return func.attr
        if isinstance(func, ast.Name):
            return func.id
    return None


def functions(tree):
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


class Overrides(unittest.TestCase):
    def test_every_override_holds_quiet_in_the_same_with(self):
        found = 0
        for name in ADDON_FILES:
            tree = parse(name)
            for fn in functions(tree):
                bound = {}  # variable name -> line of its temp_override(...)
                for node in ast.walk(fn):
                    if isinstance(node, ast.Assign) and call_name(node.value) == "temp_override":
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                bound[target.id] = node.lineno
                used = set()
                for node in ast.walk(fn):
                    if not isinstance(node, (ast.With, ast.AsyncWith)):
                        continue
                    exprs = [item.context_expr for item in node.items]
                    opens = [e for e in exprs if call_name(e) == "temp_override"
                             or (isinstance(e, ast.Name) and e.id in bound)]
                    if not opens:
                        continue
                    found += len(opens)
                    used.update(e.id for e in opens if isinstance(e, ast.Name))
                    self.assertIn("held_quiet", [call_name(e) for e in exprs],
                                  "%s:%d opens a context override without QUIET.held_quiet() in the same with"
                                  % (name, node.lineno))
                for var, line in bound.items():
                    self.assertIn(var, used, "%s:%d makes an override that no checked with opens" % (name, line))
                for node in ast.walk(fn):
                    if call_name(node) != "temp_override":
                        continue
                    inside = any(
                        isinstance(w, (ast.With, ast.AsyncWith)) and any(i.context_expr is node for i in w.items)
                        for w in ast.walk(fn)
                    ) or any(isinstance(a, ast.Assign) and a.value is node for a in ast.walk(fn))
                    self.assertTrue(inside, "%s:%d calls temp_override outside a with" % (name, node.lineno))
        self.assertGreaterEqual(found, 2, "the check found no overrides at all")

    def test_the_undo_push_opens_no_override(self):
        fn = next(f for f in functions(parse("ops.py")) if f.name == "push_undo")
        names = {call_name(n) for n in ast.walk(fn)}
        for opener in ("temp_override", "ui_context", "window_context", "held_quiet"):
            self.assertNotIn(opener, names, "push_undo must not run inside a context override")
        self.assertIn("undo_push", names)

    def test_run_pushes_its_undo_step_after_the_override_closed(self):
        fn = next(f for f in functions(parse("ops.py")) if f.name == "do_run")
        for node in ast.walk(fn):
            if isinstance(node, (ast.With, ast.AsyncWith)):
                inner = {call_name(n) for n in ast.walk(node)}
                self.assertNotIn("push_undo", inner, "do_run pushes its undo step inside a with")
        calls = [n for n in ast.walk(fn) if call_name(n) == "run_python"]
        self.assertEqual(len(calls), 1)
        context = [k.value for k in calls[0].keywords if k.arg == "context"]
        self.assertTrue(context and isinstance(context[0], ast.Name) and context[0].id == "ui_context",
                        "run_python gets the override as its context, opened after the timeout thread waits")


if __name__ == "__main__":
    unittest.main()
