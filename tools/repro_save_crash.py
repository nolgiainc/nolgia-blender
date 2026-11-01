#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""Reproduce the crash after a save from code, in a real Blender with a window.

On 2026-11-01 NOLGIA Agent's `run` saved the .blend from its own code
(`bpy.ops.wm.save_mainfile()`) and Blender 4.5.15 crashed straight after, in
the plugin's undo push (ed_undo_push > image_undosys_poll >
BPY_context_member_get > PyUnicode_FromString, EXCEPTION_ACCESS_VIOLATION in
python311.dll), the same place as on 2026-10-06. Why is in
core/mainthread.py (Quiet).

This builds the extension from a source folder (this repository by default,
or an older checkout to see the crash), installs it into a throwaway Blender
user folder, opens a .blend in Blender WITH a window (an undo push needs
one; nothing happens headless), connects it to the mock API and sends `run`
commands that save from code, the way the Agent did. Blender gets
--enable-event-simulate and --no-window-focus, so the computer's own mouse
and keyboard never reach it and it never takes focus.

    python3 tools/repro_save_crash.py --blender /path/to/blender[.exe] [--runs 80] [--source DIR]

Exit 0: every run answered and Blender closed normally. Exit 1: Blender
crashed (the crash report is printed) or stopped answering.
Works from WSL with a Windows blender.exe, like tests/e2e_blender.py.
"""

import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "tests"))

from e2e_blender import Caller, Host  # noqa: E402
from mock_bridge_server import MockBridgeServer  # noqa: E402

TOKEN = "repro-token"
MODULE = "bl_ext.user_default.nolgia"
BOOT = ("import addon_utils, os; addon_utils.enable('%s', default_set=True, handle_error=None); "
        "open(os.environ['REPRO_PID_FILE'], 'w').write(str(os.getpid()))" % MODULE)

# The Agent's step from 2026-11-01 (bl-cmds cc41234d), plus two operators,
# all inside the plugin's window and 3D view override.
SAVE_STEP = """
import bpy
sc = bpy.context.scene
cam = sc.camera
if cam is not None:
    cam.data.lens = 50 + (%(i)d %% 40)
sc.frame_set(1 + (%(i)d %% 24))
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.select_all(action='DESELECT')
bpy.ops.wm.save_mainfile()
result = {"saved": bpy.data.filepath, "frame": sc.frame_current}
"""

QUIT = "import bpy\nbpy.app.timers.register(lambda: bpy.ops.wm.quit_blender(), first_interval=0.5)\nresult = 'bye'\n"


def crash_report(folder):
    reports = sorted(glob.glob(os.path.join(folder, "*.crash.txt")), key=os.path.getmtime)
    if not reports:
        return None
    with open(reports[-1], encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    top = text.split("Threads:")[0]
    return "%s\n%s" % (reports[-1], top[:3000])


def enqueue(caller, kind, args, timeout):
    """Queue a command; wait out the API's limit of 120 commands a minute."""
    for _ in range(30):
        status, data = caller.http("POST", "/v1/bridge/commands",
                                   {"app": "blender", "kind": kind, "args": args, "timeout_seconds": timeout})
        if status == 201:
            return data["id"]
        assert status == 429, "enqueue %s: %s %s" % (kind, status, data)
        time.sleep(3)
    raise SystemExit("the API kept refusing new commands (429)")


def end_blender(proc, pid_file, wsl):
    """A crashed Blender on Windows waits on its error window: end it by its
    own process id (never by name: other Blenders may be open)."""
    if proc.poll() is None and wsl and os.path.isfile(pid_file):
        with open(pid_file) as handle:
            pid = handle.read().strip()
        if pid.isdigit():
            subprocess.run(["taskkill.exe", "/PID", pid, "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if proc.poll() is None:
        proc.kill()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender", default=os.environ.get("BLENDER", "blender"))
    parser.add_argument("--source", default=REPO, help="the extension's source folder (default: this repository)")
    parser.add_argument("--runs", type=int, default=80)
    parser.add_argument("--workdir", default=None, help="scratch folder Blender can reach (default: a temp dir)")
    opts = parser.parse_args()

    host = Host(opts.blender)
    work = os.path.abspath(opts.workdir or tempfile.mkdtemp(prefix="nolgia-repro-"))
    if os.path.exists(work):
        shutil.rmtree(work)
    user_dir, dist, temp = (os.path.join(work, n) for n in ("blender-user", "dist", "temp"))
    for folder in (user_dir, dist, temp):
        os.makedirs(folder)
    blend = os.path.join(work, "shot.blend")
    base = {"BLENDER_USER_RESOURCES": host.to_blender(user_dir)}
    print("scratch folder: %s" % work, flush=True)

    out = host.run(["--command", "extension", "build", "--source-dir", host.to_blender(os.path.abspath(opts.source)),
                    "--output-dir", host.to_blender(dist)], base)
    assert out.returncode == 0, out.stdout[-2000:]
    zips = [f for f in os.listdir(dist) if f.endswith(".zip")]
    assert len(zips) == 1, zips
    out = host.run(["--command", "extension", "install-file", "-r", "user_default", "-e",
                    host.to_blender(os.path.join(dist, zips[0]))], base)
    assert out.returncode == 0, out.stdout[-2000:]
    out = host.run(["--background", "--factory-startup", "--python-expr",
                    "import bpy; bpy.ops.wm.save_as_mainfile(filepath=%r)" % host.to_blender(blend)], base)
    assert os.path.isfile(blend), out.stdout[-2000:]
    print("extension: %s" % zips[0], flush=True)

    server = MockBridgeServer(tokens=(TOKEN,)).start()
    caller = Caller(server.root_url, TOKEN)
    # TEMP points Blender's crash report at the scratch folder.
    pid_file = os.path.join(work, "blender.pid")
    env = dict(base, NOLGIA_TOKEN=TOKEN, NOLGIA_API_URL=server.base_url, NOLGIA_BRIDGE_AUTOCONNECT="1",
               NOLGIA_ASK_BEFORE_RUN="0", TEMP=host.to_blender(temp), TMP=host.to_blender(temp),
               REPRO_PID_FILE=host.to_blender(pid_file))
    log_path = os.path.join(work, "blender.log")
    proc = host.popen(["--factory-startup", "--no-window-focus", "--enable-event-simulate",
                       "--window-geometry", "60", "60", "800", "500", host.to_blender(blend),
                       "--python-expr", BOOT], env, log_path)
    started = time.time()
    code = 1
    try:
        while time.time() - started < 120:
            status, data = caller.http("GET", "/v1/bridge/sessions")
            if status == 200 and data.get("sessions"):
                break
            if proc.poll() is not None:
                raise SystemExit("Blender exited before it connected (code %s); see %s" % (proc.returncode, log_path))
            time.sleep(0.5)
        else:
            raise SystemExit("Blender did not connect in 120 s; see %s" % log_path)
        print("connected after %.1f s" % (time.time() - started), flush=True)
        done = 0
        for i in range(opts.runs):
            cid = enqueue(caller, "run", {"language": "python", "code": SAVE_STEP % {"i": i}}, 60)
            end = time.time() + 60
            cmd = None
            while time.time() < end:
                status, cmd = caller.http("GET", "/v1/bridge/commands/%s?wait=2" % cid)
                if cmd["status"] not in ("queued", "running") or proc.poll() is not None or crash_report(temp):
                    break
            if proc.poll() is not None or crash_report(temp):
                time.sleep(2)  # let the crash report be written
                print("CRASH: Blender exited (code %s) during run %d after %d answered runs"
                      % (proc.returncode, i + 1, done), flush=True)
                print(crash_report(temp) or "(no crash report in %s)" % temp, flush=True)
                return 1
            if cmd["status"] != "succeeded":
                print("run %d did not succeed: %s %s" % (i + 1, cmd["status"], cmd.get("error")), flush=True)
                report = crash_report(temp)
                if report:
                    print("CRASH (Blender is still open with its error window):\n" + report, flush=True)
                return 1
            done += 1
            if done % 10 == 0:
                print("%d runs saved from code, no crash" % done, flush=True)
        enqueue(caller, "run", {"language": "python", "code": QUIT}, 30)
        try:
            proc.wait(60)
        except Exception:
            print("Blender did not close after the runs", flush=True)
            return 1
        report = crash_report(temp)
        if report:
            print("CRASH while closing:\n" + report, flush=True)
            return 1
        print("PASS: %d runs saved from code in a Blender with a window; Blender closed normally (code %s)"
              % (done, proc.returncode), flush=True)
        code = 0
        return code
    finally:
        end_blender(proc, pid_file, host.wsl)
        server.stop()


if __name__ == "__main__":
    sys.exit(main())
