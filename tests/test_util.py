# SPDX-License-Identifier: GPL-3.0-or-later
import datetime
import json
import os
import random
import tempfile
import unittest

import support  # noqa: F401

from core import util


class ParseTime(unittest.TestCase):
    def test_go_nanoseconds_and_z(self):
        t = util.parse_time("2026-09-29T06:24:52.527745123Z")
        self.assertEqual(t, datetime.datetime(2026, 9, 29, 6, 24, 52, 527745, datetime.timezone.utc))

    def test_offsets_and_no_fraction(self):
        t = util.parse_time("2026-09-29T08:00:00+02:00")
        self.assertEqual(t.astimezone(datetime.timezone.utc).hour, 6)
        self.assertIsNotNone(util.parse_time("2026-09-29T06:00:00"))

    def test_garbage(self):
        for value in (None, "", "yesterday", 12, "2026-13-40T99:00:00Z"):
            self.assertIsNone(util.parse_time(value))


class BackoffTest(unittest.TestCase):
    def test_grows_with_jitter_and_caps(self):
        b = util.Backoff(1.0, 8.0, rng=random.Random(1))
        delays = [b.next() for _ in range(8)]
        for i, d in enumerate(delays):
            top = min(8.0, 2 ** i)
            self.assertGreaterEqual(d, top / 2)
            self.assertLessEqual(d, top)
        b.reset()
        self.assertLessEqual(b.next(), 1.0)


class Files(unittest.TestCase):
    def test_import_extension_prefers_name_then_mime_then_bytes(self):
        self.assertEqual(util.import_extension("hero.FBX", "application/octet-stream"), ".fbx")
        self.assertEqual(util.import_extension("Hero shot v2", "model/gltf-binary"), ".glb")
        self.assertEqual(util.import_extension("", "video/quicktime"), ".mov")
        self.assertEqual(util.import_extension("thing", "application/octet-stream", b"glTF\x02\x00"), ".glb")
        self.assertEqual(util.import_extension("x", "", b"\x89PNG\r\n\x1a\n...."), ".png")
        self.assertEqual(util.import_extension("x", "", b"\x00\x00\x00\x18ftypisom"), ".mp4")
        self.assertEqual(util.import_extension("notes.txt", "text/plain", b"hello"), "")

    def test_safe_filename(self):
        self.assertEqual(util.safe_filename("../../etc/passwd"), "passwd")
        self.assertEqual(util.safe_filename("C:\\x\\shot: final?.blend"), "shot_ final_.blend")
        self.assertEqual(util.safe_filename("   ", "fallback"), "fallback")

    def test_cut_text(self):
        self.assertEqual(util.cut_text("abc", 5), "abc")
        self.assertTrue(util.cut_text("a" * 10, 4).endswith("aaaa"))
        self.assertIn("6 characters cut", util.cut_text("a" * 10, 4))
        self.assertTrue(util.cut_text("abcdef", 2, keep="head").startswith("ab\n"))


if __name__ == "__main__":
    unittest.main()


class ComputerName(unittest.TestCase):
    # The owner's Mac test (2026-10-29): Photoshop said "louieknows's Mac",
    # the other plugins "Louies-MacBook-Pro.local"; the Mac is "Louie's MacBook Pro".
    def test_a_mac_names_its_computer_name(self):
        self.assertEqual(util.computer_name("darwin", scutil=lambda: "Louie\u2019s MacBook Pro", node="Louies-MacBook-Pro.local"),
                         "Louie\u2019s MacBook Pro")

    def test_without_scutil_a_mac_falls_back_to_the_network_name(self):
        self.assertEqual(util.computer_name("darwin", scutil=lambda: "", node="Louies-MacBook-Pro.local"), "Louies-MacBook-Pro")

    def test_windows_names_its_computer_name(self):
        self.assertEqual(util.computer_name("win32", scutil=lambda: "never asked", node="DESKTOP-ABC123"), "DESKTOP-ABC123")

    def test_os_names(self):
        self.assertEqual([util.os_name(p) for p in ("darwin", "win32", "linux", "freebsd13")], ["macos", "windows", "linux", ""])

    def test_the_shared_data_folder(self):
        self.assertEqual(util.nolgia_data_dir("win32", {"APPDATA": r"C:\Users\ada\AppData\Roaming"}, "C:\\Users\\ada"),
                         os.path.join(r"C:\Users\ada\AppData\Roaming", "NOLGIA"))
        self.assertEqual(util.nolgia_data_dir("darwin", {}, "/Users/ada"),
                         os.path.join("/Users/ada", "Library", "Application Support", "NOLGIA"))

    def test_the_name_is_kept_for_photoshop(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertTrue(util.remember_computer_name("Ada's MacBook Pro", "nolgia-blender 0.2.1", folder))
            with open(os.path.join(folder, "computer.json"), encoding="utf-8") as handle:
                data = json.load(handle)
            self.assertEqual(data["name"], "Ada's MacBook Pro")
            self.assertEqual(data["written_by"], "nolgia-blender 0.2.1")
            self.assertTrue(util.remember_computer_name("Ada's MacBook Pro", "nolgia-blender 0.2.1", folder), "unchanged")
            self.assertFalse(util.remember_computer_name("", "nolgia-blender 0.2.1", folder))
