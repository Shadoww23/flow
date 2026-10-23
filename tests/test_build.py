from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import build


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.suite, self.tools = build.load_suite(REPO)

    def test_manifests_are_valid(self):
        self.assertEqual(build.validate(self.suite, self.tools, REPO), [])

    def test_missing_asset_is_reported(self):
        tools = copy.deepcopy(self.tools)
        tools[0]["assets"]["cover"] = "assets/does-not-exist.png"
        errors = build.validate(self.suite, tools, REPO)
        self.assertTrue(any("asset not found" in error for error in errors))

    def test_archive_requires_checksum(self):
        tools = copy.deepcopy(self.tools)
        del tools[0]["install"]["archive"]["sha256"]
        errors = build.validate(self.suite, tools, REPO)
        self.assertTrue(any("sha256" in error for error in errors))

    def test_default_entry_must_exist(self):
        tools = copy.deepcopy(self.tools)
        tools[0]["default_entry"] = "nope"
        errors = build.validate(self.suite, tools, REPO)
        self.assertTrue(any("default_entry" in error for error in errors))

    def test_steps_are_required(self):
        tools = copy.deepcopy(self.tools)
        tools[0]["steps"] = []
        errors = build.validate(self.suite, tools, REPO)
        self.assertTrue(any("setup steps" in error for error in errors))

    def test_availability_must_be_boolean(self):
        tools = copy.deepcopy(self.tools)
        tools[0]["install"]["aur"]["available"] = "no"
        errors = build.validate(self.suite, tools, REPO)
        self.assertTrue(any("available" in error for error in errors))

    def test_escapes_html(self):
        self.assertEqual(build.esc('<img src="x">'), "&lt;img src=&quot;x&quot;&gt;")


class BuildTests(unittest.TestCase):
    def test_build_outputs_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "site"
            result = build.build(REPO, out)
            self.assertEqual(result, out)

            index = (out / "index.html").read_text(encoding="utf-8")
            self.assertIn("Proton Picker", index)
            self.assertIn("ReleaseGuard", index)
            self.assertIn("One hub", index)
            self.assertNotIn("{{", index)

            for tool_id in ("proton-picker", "releaseguard"):
                page = out / "tools" / f"{tool_id}.html"
                self.assertTrue(page.exists())
                text = page.read_text(encoding="utf-8")
                self.assertIn("Alpha", text)
                self.assertIn("SHA-256", text)
                self.assertIn("Install in 3 steps", text)
                self.assertIn("Before you start", text)
                self.assertIn("Advanced: package managers", text)
                self.assertIn("os-download", text)
                self.assertIn("data-windows-href", text)
                self.assertIn("Questions people ask", text)
                self.assertNotIn("yay -S", text)
                self.assertNotIn("{{", text)

            sitemap = (out / "sitemap.xml").read_text(encoding="utf-8")
            self.assertIn("tools/proton-picker.html", sitemap)
            self.assertIn("tools/releaseguard.html", sitemap)

            self.assertTrue((out / "assets" / "flow-icon.svg").exists())
            self.assertTrue((out / "robots.txt").exists())
            self.assertTrue((out / ".nojekyll").exists())

            payload = json.loads((out / "flow.json").read_text(encoding="utf-8"))
            self.assertEqual(len(payload["tools"]), 2)
            self.assertEqual(payload["suite"]["name"], "Flow")

    def test_build_refuses_repo_root(self):
        with self.assertRaises(ValueError):
            build.build(REPO, REPO)


if __name__ == "__main__":
    unittest.main()
