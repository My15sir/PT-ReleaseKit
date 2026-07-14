from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
INSTALL_SCRIPT = ROOT / "ptbd_core" / "assets" / "remote-install-deps.sh"


class RemoteDependencyAssetTests(unittest.TestCase):
    def run_installer(self, mode: str) -> tuple[subprocess.CompletedProcess[str], str]:
        with tempfile.TemporaryDirectory() as temporary:
            fake_bin = Path(temporary)
            log_path = fake_bin / "package-manager.log"
            python = fake_bin / "python3"
            python.write_text(
                "#!/bin/sh\n"
                "case \"$*\" in\n"
                "  *'import numpy'*|*'import PIL'*) exit 1 ;;\n"
                "esac\n"
                "exec /usr/bin/python3 \"$@\"\n",
                encoding="utf-8",
            )
            apt_get = fake_bin / "apt-get"
            apt_get.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >> \"$PTBD_TEST_PACKAGE_LOG\"\n",
                encoding="utf-8",
            )
            fake_id = fake_bin / "id"
            fake_id.write_text(
                "#!/bin/sh\n"
                "if [ \"${1:-}\" = '-u' ]; then\n"
                "  printf '0\\n'\n"
                "  exit 0\n"
                "fi\n"
                "exec /usr/bin/id \"$@\"\n",
                encoding="utf-8",
            )
            python.chmod(0o755)
            apt_get.chmod(0o755)
            fake_id.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:/usr/local/bin:/usr/bin:/bin",
                    "PTBD_AUDIO_SPECTRUM_MODE": mode,
                    "PTBD_TEST_PACKAGE_LOG": str(log_path),
                }
            )
            result = subprocess.run(
                ["sh", str(INSTALL_SCRIPT)],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            package_log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
            return result, package_log

    def test_single_mode_does_not_require_numpy_or_pillow(self) -> None:
        result, package_log = self.run_installer("single")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("status=ready", result.stdout)
        self.assertEqual(package_log, "")

    def test_combined_mode_installs_numpy_and_pillow(self) -> None:
        result, package_log = self.run_installer("combined")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("status=installed", result.stdout)
        self.assertIn("python3-numpy", package_log)
        self.assertIn("python3-pil", package_log)

    def test_invalid_mode_fails_without_installing_packages(self) -> None:
        result, package_log = self.run_installer("unexpected")

        self.assertEqual(result.returncode, 2)
        self.assertIn("status=invalid-audio-spectrum-mode", result.stderr)
        self.assertEqual(package_log, "")


if __name__ == "__main__":
    unittest.main()
