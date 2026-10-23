from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import flow


def make_tar(path: Path, name: str = "hello.txt") -> Path:
    with tarfile.open(path, "w:gz") as bundle:
        info = tarfile.TarInfo(name)
        data = b"hello flow\n"
        info.size = len(data)
        bundle.addfile(info, io.BytesIO(data))
    return path


class VersionTests(unittest.TestCase):
    def test_prerelease_ordering(self):
        self.assertLess(flow.version_key("0.1.0-alpha"), flow.version_key("0.1.0-beta"))
        self.assertLess(flow.version_key("0.1.0-beta"), flow.version_key("0.1.0-rc1"))
        self.assertLess(flow.version_key("0.1.0-rc1"), flow.version_key("0.1.0"))
        self.assertLess(flow.version_key("0.1.0"), flow.version_key("0.2.0"))

    def test_tag_prefix_is_ignored(self):
        self.assertEqual(flow.version_key("v0.1.0-alpha"), flow.version_key("0.1.0-alpha"))


class ExtractTests(unittest.TestCase):
    def test_tar_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = make_tar(root / "evil.tar.gz", "../evil.txt")
            with self.assertRaises(flow.FlowError):
                flow.safe_extract(archive, root / "out", "tar.gz")
            self.assertFalse((root / "evil.txt").exists())

    def test_zip_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "evil.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../evil.txt", "bad")
            with self.assertRaises(flow.FlowError):
                flow.safe_extract(archive, root / "out", "zip")
            self.assertFalse((root / "evil.txt").exists())

    def test_valid_tar_extracts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = make_tar(root / "good.tar.gz")
            flow.safe_extract(archive, root / "out", "tar.gz")
            self.assertEqual((root / "out" / "hello.txt").read_text(), "hello flow\n")


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data_root = self.root / "data"
        self.archive = make_tar(self.root / "demo.tar.gz")
        self.digest = flow.sha256_file(self.archive)
        self.tool = {
            "id": "demo",
            "name": "Demo",
            "version": "0.1.0-alpha",
            "install": {
                "archive": {
                    "label": "Archive",
                    "url": str(self.archive),
                    "sha256": self.digest,
                    "format": "tar.gz",
                }
            },
            "entries": {"run": "hello.txt"},
        }

    def tearDown(self):
        self.temporary.cleanup()

    def test_install_verifies_and_records_state(self):
        with contextlib.redirect_stdout(io.StringIO()):
            flow.install_tool(self.tool, data_root=self.data_root)
        destination = self.data_root / "tools" / "demo" / "0.1.0-alpha"
        self.assertTrue((destination / "hello.txt").exists())
        state = flow.load_state(self.data_root)
        self.assertEqual(state["tools"]["demo"]["version"], "0.1.0-alpha")
        self.assertEqual(state["tools"]["demo"]["sha256"], self.digest)

    def test_reinstall_requires_force(self):
        with contextlib.redirect_stdout(io.StringIO()):
            flow.install_tool(self.tool, data_root=self.data_root)
            with self.assertRaises(flow.FlowError):
                flow.install_tool(self.tool, data_root=self.data_root)
            flow.install_tool(self.tool, data_root=self.data_root, force=True)

    def test_checksum_mismatch_leaves_nothing_installed(self):
        self.tool["install"]["archive"]["sha256"] = "0" * 64
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(flow.FlowError):
                flow.install_tool(self.tool, data_root=self.data_root)
        self.assertFalse((self.data_root / "tools" / "demo").exists())

    def test_dry_run_changes_nothing(self):
        with contextlib.redirect_stdout(io.StringIO()):
            flow.install_tool(self.tool, dry_run=True, data_root=self.data_root)
        self.assertFalse((self.data_root / "tools").exists())

    def test_open_uses_entry(self):
        with contextlib.redirect_stdout(io.StringIO()):
            flow.install_tool(self.tool, data_root=self.data_root)
            code = flow.open_tool(self.tool, "run", dry_run=True, data_root=self.data_root)
        self.assertEqual(code, 0)

    def test_open_requires_install(self):
        with self.assertRaises(flow.FlowError):
            flow.open_tool(self.tool, dry_run=True, data_root=self.data_root)

    def test_unknown_channel_is_rejected(self):
        with self.assertRaises(flow.FlowError):
            flow.install_tool(self.tool, "aur", data_root=self.data_root)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.previous = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = str(self.root)

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self.previous
        self.temporary.cleanup()

    def run_cli(self, *argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = flow.main(list(argv))
        return code, buffer.getvalue()

    def test_list_json(self):
        code, output = self.run_cli("list", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(output)
        ids = {tool["id"] for tool in payload["tools"]}
        self.assertEqual(ids, {"proton-picker", "releaseguard"})
        self.assertEqual(payload["installed"], {})

    def test_channels_include_official_recipes(self):
        code, output = self.run_cli("channels")
        self.assertEqual(code, 0)
        self.assertIn("yay -S proton-picker", output)
        self.assertIn("yay -S releaseguard", output)
        self.assertIn("proton-picker", output)
        self.assertIn("planned", output)
        self.assertIn("available", output)

    def test_planned_channel_is_refused(self):
        _, tools = flow.load_tools()
        tool = flow.find_tool(tools, "proton-picker")
        with self.assertRaises(flow.FlowError):
            flow.install_tool(tool, "aur", dry_run=True)

    def test_info(self):
        code, output = self.run_cli("info", "proton-picker")
        self.assertEqual(code, 0)
        self.assertIn("Proton Picker", output)
        self.assertIn("0.1.0-alpha", output)
        self.assertIn("installed  no", output)

    def test_unknown_tool_exits_nonzero(self):
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            code = flow.main(["info", "nope"])
        self.assertEqual(code, 1)
        self.assertIn("unknown tool", buffer.getvalue())

    def test_update_reports_not_installed(self):
        code, output = self.run_cli("update", "--all", "--offline")
        self.assertEqual(code, 0)
        self.assertIn("not installed", output)

    def test_doctor_offline(self):
        code, output = self.run_cli("doctor", "--offline")
        self.assertIn("manifests", output)
        self.assertIn("OK", output)


if __name__ == "__main__":
    unittest.main()
