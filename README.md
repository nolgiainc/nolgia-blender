# NOLGIA for Blender

NOLGIA's plugin for Blender. It connects Blender on your computer to
NOLGIA, so NOLGIA Agent can look at your scene, make changes in it, render
previews and move files between Blender and your NOLGIA library. Tell it what
you want right in the NOLGIA tab, so you never need to leave NOLGIA. You can
also use Claude, Cursor, Codex or another agent with NOLGIA connected
(through NOLGIA's MCP server).

```
your agent --MCP--> mcp.nolgia.ai --> api.nolgia.ai <--(the plugin connects out)-- Blender on your computer
```

The plugin always connects out to NOLGIA; nothing connects in to your
computer. It only takes commands while you are signed in and have it switched
on, and it lists every command it runs.

## Install and use

1. Get `nolgia-blender-<version>.zip` from [nolgia.ai/plugins/blender](https://nolgia.ai/plugins/blender)
   (or the [releases](https://github.com/nolgiainc/nolgia-blender/releases) here).
   Safari unpacks a downloaded zip by itself, into a folder named up to the
   first dot (`nolgia-blender-0`): Control-click that folder, choose Compress
   and use the zip that makes.
2. In Blender, open Edit > Preferences > Get Extensions, click the small arrow
   at the top right, choose Install from Disk and pick the zip. (Dragging the
   zip into Blender works too.)
3. In the 3D Viewport, press N and open the NOLGIA tab.
4. Click Sign in. Your browser opens a NOLGIA page; check that the code
   matches the one in Blender and approve it.
5. Keep Connected on and tell NOLGIA Agent what to do in the box under
   NOLGIA Agent, then click Send. Each command it runs here shows up in the
   Activity list. Click Pause (or turn Connected off) to stop at any time.

### The chat with NOLGIA Agent

The NOLGIA Agent part of the tab is a chat with your NOLGIA Agent, which
works in this Blender through the plugin:

- Type what you want in the box and click Send. Before your first message it
  suggests a few things to try (a click puts one in the box).
- One message is one turn: while NOLGIA Agent works, the box waits and Stop
  ends the turn. What it does in Blender shows in Activity.
- Each reply shows what its turn cost. The line under the box gives the rate
  of your NOLGIA Agent's model ("each turn from 70 credits on your model").
- The chat is the same chat as on [nolgia.ai/agent](https://nolgia.ai/agent)
  ("Open this chat on nolgia.ai"), named "Blender: " and your first words.
  This Blender remembers it; New chat starts another. It is pinned to this
  Blender, so NOLGIA Agent knows the work is in this window.
- The chat needs Connected and Allow NOLGIA Agent on; the tab offers the one
  click that switches on whichever is off.
- The tab shows the last three turns; the whole chat is on nolgia.ai. The
  sidebar scrolls (mouse wheel), and Settings and Activity fold away by their
  headers. Activity shows the newest six commands, or all of them.

Settings in the NOLGIA tab (also in the add-on's preferences):

- **Connected**: NOLGIA can send commands to this Blender only while this is on.
- **Allow NOLGIA Agent**: let NOLGIA Agent work here (the chat needs it), as
  well as the agent apps you run yourself.
- **Ask before running code**: show every piece of Python NOLGIA wants to run
  and wait for you to click Approve or Deny.
- **Sign out**: disconnect and forget the sign in on this computer.

Python that NOLGIA runs in Blender runs on your computer with your
permissions, like any add-on or script. Turn on Ask before running code if you
want to see it first.

## Tested on

0.2.0 (the chat) was tested on Windows 11 with Blender 4.5.8 LTS, against the
mock API and against NOLGIA itself. It has not been run on macOS yet. 0.1.1 was
tested on Windows and macOS.

## What your agent can do

| Command | Arguments | Result |
|---|---|---|
| `info` | none | open file (name, path, unsaved changes), scene, frame range, fps, render engine and size, collections with object counts, selected and active object, cameras |
| `run` | `language: "python"`, `code`, `timeout_seconds?` | `{value, stdout, stderr}`; `value` is whatever the code puts in `result` (the code sees `bpy`, `mathutils`, `C`, `D`). A failure returns the traceback. |
| `preview` | `camera?`, `frame?`, `width?` (16 to 1920), `engine?` (`current`, `eevee`, `workbench`, `cycles`) | renders a still and uploads it: `{asset_id, width, height, mime_type, camera, frame, engine}`. A PNG, or a JPEG when the PNG would be too big for your agent to see inline (about 3.6 MB). |
| `import_asset` | `asset_id`, `as?` (`plane`, `texture`, `clip`) | brings a NOLGIA asset into the scene: GLB models as objects, images as a plane (or a texture with `as: "texture"`), video as a movie clip (or a plane), audio as a sound: `{imported: [names], kind}` |
| `export` | `format` (`png`, `mp4`, `glb`, `blend`), `frames?` (`"24"` or `"1-120"`), `filename?`, `selected_only?` (glb) | renders a still or an H.264 video at the scene's settings, or exports a GLB, and uploads it: `{asset_id, format, filename}`. `blend` saves a copy on this computer instead (next to the open file, or in Documents/NOLGIA exports while it is unsaved) and never overwrites a file: `{asset_id: null, path, note}`. Blender files are not uploaded to NOLGIA. |
| `save` | `path?` | saves the file; without `path` only to the file that is already open: `{path}` |
| `open` | `path` | opens a .blend file; asks you first when the open file has unsaved changes: `{path}` |

## Blender without a window (render machines, scripts, tests)

```sh
NOLGIA_TOKEN=nol_... blender --background shot.blend --python-expr \
  "import addon_utils; addon_utils.enable('bl_ext.user_default.nolgia', default_set=True); \
   import bl_ext.user_default.nolgia as nolgia; nolgia.serve()"
```

`serve()` connects, runs commands until the plugin is switched off (or Ctrl+C),
and returns True after a normal stop. Without a window nobody can approve
code, so the plugin refuses to connect while Ask before running code is on.

| Variable | Effect |
|---|---|
| `NOLGIA_TOKEN` | use this token instead of signing in (never saved) |
| `NOLGIA_API_URL` | API to talk to (default `https://api.nolgia.ai/v1`) |
| `NOLGIA_BRIDGE_AUTOCONNECT=1` | connect as soon as Blender starts (with a window) |
| `NOLGIA_ASK_BEFORE_RUN=0/1` | set Ask before running code |
| `NOLGIA_ALLOW_AGENT=0/1` | set Allow NOLGIA Agent |
| `NOLGIA_INSTANCE_ID` | fixed session id for this Blender (render farms running many Blenders) |
| `NOLGIA_EXPORT_DIR` | where `export` `blend` puts copies of unsaved files |

## Development

Everything is standard library Python. The parts that do not need Blender
live in [`core/`](core/) and have unit tests that run with
plain Python 3.10 or newer:

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
```

The end-to-end test builds the extension, installs it into a throwaway Blender
user folder (your own settings are not touched), starts the mock API from
[`tools/mock_bridge_server.py`](tools/mock_bridge_server.py), runs Blender
without a window and drives every command through it:

```sh
python3 tests/e2e_blender.py --blender /path/to/blender
```

From WSL it works with a Windows `blender.exe` too (paths go through
`wslpath`, variables through `WSLENV`).

Build the installable zip and check it. Releases are tagged `blender-v<version>`
with the zip attached as `nolgia-blender-<version>.zip`; the website links to
that exact name.

```sh
blender --command extension build --source-dir . --output-filepath dist/nolgia-blender-0.2.0.zip
blender --command extension validate dist/nolgia-blender-0.2.0.zip
```

Run the mock API on its own:

```sh
python3 tools/mock_bridge_server.py --port 8765 --token test-token --auto-approve-after 1
```

The wire format is the NOLGIA API's `/bridge/*` endpoints (`api/openapi.yaml`,
`internal/handlers/bridge.go`); the mock follows the same rules.

## This repository

| Path | What it holds |
|---|---|
| [`__init__.py`](__init__.py), [`ui.py`](ui.py), [`ops.py`](ops.py), [`runtime.py`](runtime.py), [`blender_manifest.toml`](blender_manifest.toml) | The Blender extension (this folder is the extension: `blender --command extension build --source-dir .`) |
| [`core/`](core/) | The parts that do not need Blender (protocol client, sign in, commands, the chat); NOLGIA for DaVinci Resolve keeps a copy of it |
| [`tests/`](tests/) | Unit tests and the end-to-end test |
| [`presets/blender/`](presets/blender/) | The Film assistant workflows for Blender and the rules they share |
| [`tools/mock_bridge_server.py`](tools/mock_bridge_server.py) | An in-memory mock of the NOLGIA API that the tests run against |

The mock API is the same file in every NOLGIA plugin repository ([Blender](https://github.com/nolgiainc/nolgia-blender), [After Effects, Premiere Pro and Illustrator](https://github.com/nolgiainc/nolgia-adobe), [Photoshop](https://github.com/nolgiainc/nolgia-photoshop) and [DaVinci Resolve](https://github.com/nolgiainc/nolgia-resolve));
copy a change to it into the others.

This plugin used to live in the `blender/` folder of
[nolgiainc/nolgia-plugins](https://github.com/nolgiainc/nolgia-plugins), with
the other NOLGIA plugins. On October 16, 2026 it moved here with its history,
its branches and its releases. The releases on nolgia-plugins stay where they
are, so links to them keep working. Issues and pull requests belong here.

## License

GPL-3.0-or-later ([`LICENSE`](LICENSE)).
