from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from ptbd_core.runtime_assets import PROFILE_FILES, copy_profile, validate_profile


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RuntimeAssetManifestTests(unittest.TestCase):
    def test_every_profile_is_complete_in_repository(self) -> None:
        for profile in PROFILE_FILES:
            with self.subTest(profile=profile):
                self.assertTrue(validate_profile(PROJECT_ROOT, profile))

    def test_remote_profile_contains_python_core_and_legacy_fallbacks(self) -> None:
        paths = {entry.relative_path for entry in validate_profile(PROJECT_ROOT, "remote")}
        self.assertIn("bdtool", paths)
        self.assertIn("bdtool-legacy.sh", paths)
        self.assertIn("bdtool.sh", paths)
        self.assertIn("scripts/audio-spectrum.py", paths)
        self.assertIn("ptbd_core/cli.py", paths)
        self.assertIn("ptbd_core/assets/remote-probe.sh", paths)
        self.assertIn("ptbd_core/assets/remote-install-deps.sh", paths)

    def test_controller_builder_uses_controller_manifest(self) -> None:
        script_path = PROJECT_ROOT / "scripts" / "build-controller-app.py"
        spec = importlib.util.spec_from_file_location("build_controller_app", script_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader if spec else None)
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        actual = {source.relative_to(PROJECT_ROOT).as_posix() for source, _ in module.iter_data_entries()}
        expected = {entry.relative_path for entry in validate_profile(PROJECT_ROOT, "controller")}
        self.assertEqual(expected, actual)

    def test_remote_profile_copy_preserves_relative_layout(self) -> None:
        expected = {entry.relative_path for entry in validate_profile(PROJECT_ROOT, "remote")}
        with tempfile.TemporaryDirectory() as temp_dir:
            copied = copy_profile(PROJECT_ROOT, temp_dir, "remote")
            self.assertEqual(expected, {entry.relative_path for entry in copied})
            for relative_path in expected:
                self.assertTrue((Path(temp_dir) / relative_path).is_file(), relative_path)

    def test_delivery_adapters_reference_the_manifest(self) -> None:
        adapters = (
            "install.sh",
            "scripts/build-bundle.sh",
            "scripts/build-controller-app.py",
            "scripts/prepare-remote-runtime.sh",
            "ptbd_remote_backend.py",
            "Dockerfile",
        )
        for relative_path in adapters:
            with self.subTest(adapter=relative_path):
                source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn("runtime_assets", source)

    def test_release_workflows_watch_manifest_and_runtime_inputs(self) -> None:
        for relative_path in (
            ".github/workflows/controller-build.yml",
            ".github/workflows/bundle-release.yml",
        ):
            source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
            with self.subTest(workflow=relative_path):
                self.assertIn("bdtool-legacy.sh", source)
                self.assertIn("ptbd_core/**", source)
                self.assertIn("scripts/audio-spectrum.py", source)

    def test_release_workflows_move_mutable_tags_after_asset_publish(self) -> None:
        workflows = {
            ".github/workflows/bundle-release.yml": "bundle-latest",
            ".github/workflows/controller-build.yml": "portable-latest",
        }
        for relative_path, tag in workflows.items():
            source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
            release_step = "uses: ncipollo/release-action@v1"
            tag_update = f"git/refs/tags/{tag}"
            with self.subTest(workflow=relative_path):
                self.assertIn(tag_update, source)
                self.assertIn("GH_TOKEN: ${{ github.token }}", source)
                self.assertIn("--method PATCH", source)
                self.assertIn('-f sha="$GITHUB_SHA"', source)
                self.assertIn("-F force=true", source)
                self.assertLess(source.index(release_step), source.index(tag_update))


if __name__ == "__main__":
    unittest.main()
