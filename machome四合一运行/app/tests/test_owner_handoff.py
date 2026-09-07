#!/usr/bin/env python3

import hashlib
import json
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "owner_handoff.py"


class OwnerHandoffToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="machome-owner-handoff-")
        self.root = Path(self.temporary.name)
        os.chmod(self.root, 0o700)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_tool(self, *arguments: str, expected: int = 0) -> subprocess.CompletedProcess:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            expected,
            msg=f"stdout={result.stdout}\nstderr={result.stderr}",
        )
        return result

    def write_private_json(self, path: Path, value: dict) -> None:
        path.write_text(json.dumps(value), encoding="utf-8")
        os.chmod(path, 0o600)

    def write_deployment_manifest(self, path: Path, files: list[dict]) -> None:
        self.write_private_json(
            path,
            {
                "schema_version": 1,
                "kind": "machome_owner_deployment_manifest",
                "path_base": "manifest_directory",
                "files": files,
                "metadata": {"build": "test"},
            },
        )

    def test_inspect_plist_freezes_exact_identity_and_refuses_overwrite(self) -> None:
        program = self.root / "worker"
        program.write_bytes(b"#!/bin/sh\nexit 0\n")
        os.chmod(program, 0o700)
        plist = self.root / "com.example.worker.plist"
        with plist.open("wb") as stream:
            plistlib.dump(
                {
                    "Label": "com.example.worker",
                    "ProgramArguments": [str(program), "--config", "/tmp/a b.json"],
                },
                stream,
            )
        os.chmod(plist, 0o600)
        output = self.root / "identity.json"

        result = self.run_tool(
            "inspect-plist",
            "--plist",
            str(plist),
            "--unit-id",
            "worker",
            "--expected-label",
            "com.example.worker",
            "--output",
            str(output),
        )
        payload = json.loads(result.stdout)
        unit = payload["launchd_unit"]
        self.assertEqual(unit["expected_program"], str(program))
        self.assertEqual(unit["expected_arguments"], ["--config", "/tmp/a b.json"])
        self.assertEqual(
            unit["artifact_sha256"], hashlib.sha256(program.read_bytes()).hexdigest()
        )
        self.assertEqual(
            unit["expected_artifacts"],
            [{"path": str(program), "sha256": unit["artifact_sha256"]}],
        )
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

        self.run_tool(
            "inspect-plist",
            "--plist",
            str(plist),
            "--unit-id",
            "worker",
            "--output",
            str(output),
            expected=2,
        )

    def test_python_identity_requires_entry_and_deployment_manifest(self) -> None:
        runtime = self.root / "python3"
        entry = self.root / "service.py"
        package = self.root / "package"
        package.mkdir()
        helper = package / "helper.py"
        manifest = self.root / "deployment-manifest.json"
        runtime.write_bytes(b"python-runtime-test")
        entry.write_bytes(b"print('test')\n")
        helper.write_bytes(b"VALUE = 1\n")
        self.write_deployment_manifest(
            manifest,
            [
                {
                    "path": "service.py",
                    "sha256": hashlib.sha256(entry.read_bytes()).hexdigest(),
                },
                {
                    "path": "package/helper.py",
                    "sha256": hashlib.sha256(helper.read_bytes()).hexdigest(),
                },
            ],
        )
        os.chmod(runtime, 0o700)
        os.chmod(entry, 0o600)
        os.chmod(helper, 0o600)
        plist = self.root / "com.example.python.plist"
        with plist.open("wb") as stream:
            plistlib.dump(
                {
                    "Label": "com.example.python",
                    "ProgramArguments": [str(runtime), str(entry), "--serve"],
                },
                stream,
            )
        os.chmod(plist, 0o600)

        self.run_tool(
            "inspect-plist",
            "--plist",
            str(plist),
            "--unit-id",
            "python-service",
            expected=2,
        )
        result = self.run_tool(
            "inspect-plist",
            "--plist",
            str(plist),
            "--unit-id",
            "python-service",
            "--python-unit",
            "--python-entry",
            str(entry),
            "--deployment-manifest",
            str(manifest),
        )
        artifacts = json.loads(result.stdout)["launchd_unit"]["expected_artifacts"]
        self.assertEqual(
            [item["path"] for item in artifacts],
            [str(runtime), str(entry), str(manifest), str(helper)],
        )

    def test_python_entry_must_match_real_plist_entry_after_normalization(self) -> None:
        runtime = self.root / "python3"
        entry = self.root / "service.py"
        other = self.root / "other.py"
        manifest = self.root / "deployment-manifest.json"
        runtime.write_bytes(b"runtime")
        entry.write_bytes(b"print('entry')\n")
        other.write_bytes(b"print('other')\n")
        for path in (runtime, entry, other):
            os.chmod(path, 0o700 if path == runtime else 0o600)
        self.write_deployment_manifest(
            manifest,
            [
                {
                    "path": "other.py",
                    "sha256": hashlib.sha256(other.read_bytes()).hexdigest(),
                }
            ],
        )
        plist = self.root / "com.example.python-entry.plist"
        with plist.open("wb") as stream:
            plistlib.dump(
                {
                    "Label": "com.example.python-entry",
                    "ProgramArguments": [
                        str(runtime),
                        str(self.root / "subdir" / ".." / "service.py"),
                        "--plugin",
                        str(other),
                    ],
                },
                stream,
            )
        os.chmod(plist, 0o600)

        result = self.run_tool(
            "inspect-plist",
            "--plist",
            str(plist),
            "--unit-id",
            "python-entry",
            "--python-unit",
            "--python-entry",
            str(other),
            "--deployment-manifest",
            str(manifest),
            expected=2,
        )
        self.assertIn("真实入口", result.stderr)

    def test_deployment_manifest_rejects_invalid_paths_and_hashes(self) -> None:
        runtime = self.root / "python3"
        entry = self.root / "service.py"
        outside = self.root.parent / f"{self.root.name}-outside.py"
        manifest = self.root / "deployment-manifest.json"
        plist = self.root / "com.example.manifest.plist"
        runtime.write_bytes(b"runtime")
        entry.write_bytes(b"print('entry')\n")
        outside.write_bytes(b"outside\n")
        escape_link = self.root / "escape.py"
        escape_link.symlink_to(outside)
        os.chmod(runtime, 0o700)
        os.chmod(entry, 0o600)
        os.chmod(outside, 0o600)
        with plist.open("wb") as stream:
            plistlib.dump(
                {
                    "Label": "com.example.manifest",
                    "ProgramArguments": [str(runtime), str(entry)],
                },
                stream,
            )
        os.chmod(plist, 0o600)

        base_arguments = (
            "inspect-plist",
            "--plist",
            str(plist),
            "--unit-id",
            "manifest",
            "--python-unit",
            "--python-entry",
            str(entry),
            "--deployment-manifest",
            str(manifest),
        )
        entry_hash = hashlib.sha256(entry.read_bytes()).hexdigest()
        cases = [
            (
                "directory traversal",
                [
                    {"path": "service.py", "sha256": entry_hash},
                    {
                        "path": f"../{outside.name}",
                        "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
                    },
                ],
                "目录逃逸",
            ),
            (
                "duplicate",
                [
                    {"path": "service.py", "sha256": entry_hash},
                    {"path": "service.py", "sha256": entry_hash},
                ],
                "路径重复",
            ),
            (
                "symlink escape",
                [
                    {"path": "service.py", "sha256": entry_hash},
                    {
                        "path": "escape.py",
                        "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
                    },
                ],
                "路径逃逸",
            ),
            (
                "missing",
                [
                    {"path": "service.py", "sha256": entry_hash},
                    {"path": "missing.py", "sha256": "0" * 64},
                ],
                "文件不存在",
            ),
            (
                "hash mismatch",
                [{"path": "service.py", "sha256": "0" * 64}],
                "hash 不符",
            ),
        ]
        try:
            for name, files, expected_error in cases:
                with self.subTest(name=name):
                    self.write_deployment_manifest(manifest, files)
                    result = self.run_tool(*base_arguments, expected=2)
                    self.assertIn(expected_error, result.stderr)
        finally:
            outside.unlink(missing_ok=True)

    def test_deployment_manifest_requires_explicit_schema_and_entry(self) -> None:
        runtime = self.root / "python3"
        entry = self.root / "service.py"
        helper = self.root / "helper.py"
        manifest = self.root / "deployment-manifest.json"
        plist = self.root / "com.example.manifest-schema.plist"
        runtime.write_bytes(b"runtime")
        entry.write_bytes(b"entry")
        helper.write_bytes(b"helper")
        os.chmod(runtime, 0o700)
        os.chmod(entry, 0o600)
        os.chmod(helper, 0o600)
        with plist.open("wb") as stream:
            plistlib.dump(
                {
                    "Label": "com.example.manifest-schema",
                    "ProgramArguments": [str(runtime), str(entry)],
                },
                stream,
            )
        os.chmod(plist, 0o600)
        arguments = (
            "inspect-plist",
            "--plist",
            str(plist),
            "--unit-id",
            "manifest-schema",
            "--python-unit",
            "--python-entry",
            str(entry),
            "--deployment-manifest",
            str(manifest),
        )

        self.write_private_json(manifest, {"build": "legacy"})
        result = self.run_tool(*arguments, expected=2)
        self.assertIn("缺少字段", result.stderr)

        self.write_deployment_manifest(
            manifest,
            [
                {
                    "path": "helper.py",
                    "sha256": hashlib.sha256(helper.read_bytes()).hexdigest(),
                }
            ],
        )
        result = self.run_tool(*arguments, expected=2)
        self.assertIn("必须包含 --python-entry", result.stderr)

    def test_shell_launcher_freezes_real_wrapper_and_rejects_hidden_relative_payload(self) -> None:
        shell = self.root / "zsh"
        wrapper = self.root / "start.sh"
        shell.write_bytes(b"test-shell")
        wrapper.write_bytes(b"#!/bin/zsh\nexit 0\n")
        os.chmod(shell, 0o700)
        os.chmod(wrapper, 0o700)

        absolute_plist = self.root / "com.example.wrapper.plist"
        with absolute_plist.open("wb") as stream:
            plistlib.dump(
                {
                    "Label": "com.example.wrapper",
                    "ProgramArguments": [str(shell), str(wrapper), "--serve"],
                },
                stream,
            )
        os.chmod(absolute_plist, 0o600)
        result = self.run_tool(
            "inspect-plist",
            "--plist",
            str(absolute_plist),
            "--unit-id",
            "wrapper",
        )
        artifacts = json.loads(result.stdout)["launchd_unit"]["expected_artifacts"]
        self.assertEqual([item["path"] for item in artifacts], [str(shell), str(wrapper)])
        self.assertEqual(
            artifacts[1]["sha256"], hashlib.sha256(wrapper.read_bytes()).hexdigest()
        )

        relative_plist = self.root / "com.example.relative-wrapper.plist"
        with relative_plist.open("wb") as stream:
            plistlib.dump(
                {
                    "Label": "com.example.relative-wrapper",
                    "ProgramArguments": [str(shell), "start.sh"],
                },
                stream,
            )
        os.chmod(relative_plist, 0o600)
        result = self.run_tool(
            "inspect-plist",
            "--plist",
            str(relative_plist),
            "--unit-id",
            "relative-wrapper",
            expected=2,
        )
        self.assertIn("绝对 WorkingDirectory", result.stderr)

    def test_prepare_marker_is_private_and_stdout_is_redacted(self) -> None:
        marker = self.root / "webull.handoff.json"
        lock = self.root / "webull.owner.lock"
        receipt = self.root / "receipt.json"
        result = self.run_tool(
            "prepare-marker",
            "--module-id",
            "webull",
            "--owner-id",
            "change-test-webull",
            "--generation",
            "1",
            "--lock-path",
            str(lock),
            "--marker",
            str(marker),
            "--ttl-seconds",
            "60",
            "--previous-label",
            "com.example.old-webull",
            "--previous-owner-stopped",
            "--receipt",
            str(receipt),
        )
        public = json.loads(result.stdout)
        private = json.loads(marker.read_text(encoding="utf-8"))
        self.assertNotIn(private["handoff_token"], result.stdout)
        self.assertNotIn('"handoff_token":', result.stdout)
        self.assertEqual(
            hashlib.sha256(private["handoff_token"].encode("utf-8")).hexdigest(),
            public["owner_lease"]["handoff_token_sha256"],
        )
        self.assertTrue(private["previous_owner_stopped"])
        self.assertGreaterEqual(len(private["nonce"]), 16)
        self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(receipt.stat().st_mode), 0o600)

        self.run_tool(
            "prepare-marker",
            "--module-id",
            "webull",
            "--owner-id",
            "change-test-webull",
            "--generation",
            "1",
            "--lock-path",
            str(lock),
            "--marker",
            str(marker),
            "--previous-label",
            "com.example.old-webull",
            "--previous-owner-stopped",
            expected=2,
        )

    def test_prepare_requires_explicit_stopped_assertion(self) -> None:
        result = self.run_tool(
            "prepare-marker",
            "--module-id",
            "upload",
            "--owner-id",
            "change-test-upload",
            "--generation",
            "1",
            "--lock-path",
            str(self.root / "upload.owner.lock"),
            "--marker",
            str(self.root / "upload.handoff.json"),
            "--previous-label",
            "com.example.old-upload",
            expected=2,
        )
        self.assertIn("--previous-owner-stopped", result.stderr)
        self.assertFalse((self.root / "upload.handoff.json").exists())

    def test_invalidate_state_requires_exact_identity_and_monotonic_next_generation(self) -> None:
        state_path = self.root / "premium.owner.lock.state.json"
        nonce_hash = "a" * 64
        self.write_private_json(
            state_path,
            {
                "schema_version": 1,
                "module_id": "premium",
                "owner_id": "change-test-premium",
                "generation": 4,
                "marker_nonce_sha256": nonce_hash,
                "active": True,
                "activated_at": "2026-09-04T00:00:00.000Z",
            },
        )

        self.run_tool(
            "invalidate-state",
            "--state",
            str(state_path),
            "--module-id",
            "premium",
            "--owner-id",
            "change-test-premium",
            "--generation",
            "4",
            "--reason",
            "rollback test",
            expected=2,
        )
        self.assertTrue(json.loads(state_path.read_text(encoding="utf-8"))["active"])

        self.run_tool(
            "invalidate-state",
            "--state",
            str(state_path),
            "--module-id",
            "premium",
            "--owner-id",
            "change-test-premium",
            "--generation",
            "4",
            "--marker-nonce-sha256",
            nonce_hash,
            "--reason",
            "rollback test",
            "--confirm-owner-stopped",
        )
        invalidated = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertFalse(invalidated["active"])
        self.assertEqual(stat.S_IMODE(state_path.stat().st_mode), 0o600)

        # Reusing or decreasing a consumed generation is rejected before a
        # marker can be written.
        self.run_tool(
            "prepare-marker",
            "--module-id",
            "premium",
            "--owner-id",
            "change-test-premium",
            "--generation",
            "4",
            "--lock-path",
            str(self.root / "premium.owner.lock"),
            "--marker",
            str(self.root / "premium.handoff.json"),
            "--previous-process",
            "/Applications/OldPremium.app/Contents/MacOS/old-premium",
            "--previous-owner-stopped",
            expected=2,
        )
        self.assertFalse((self.root / "premium.handoff.json").exists())

        self.run_tool(
            "prepare-marker",
            "--module-id",
            "premium",
            "--owner-id",
            "change-test-premium",
            "--generation",
            "5",
            "--lock-path",
            str(self.root / "premium.owner.lock"),
            "--marker",
            str(self.root / "premium.handoff.json"),
            "--previous-process",
            "/Applications/OldPremium.app/Contents/MacOS/old-premium",
            "--previous-owner-stopped",
        )
        self.assertTrue((self.root / "premium.handoff.json").exists())


if __name__ == "__main__":
    unittest.main()
