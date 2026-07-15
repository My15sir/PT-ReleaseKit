from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (PROJECT_ROOT / path).read_text(encoding="utf-8")


class BrandingContractTests(unittest.TestCase):
    def test_public_surfaces_use_releasekit_brand(self) -> None:
        readme = source("README.md")
        english_readme = source("docs/README.en.md")
        gui = source("ptbd-gui.py")
        web = source("ptbd-web.py")

        self.assertTrue(readme.startswith("# PT ReleaseKit\n"))
        self.assertTrue(english_readme.startswith("# PT ReleaseKit\n"))
        self.assertIn('PRODUCT_NAME = "PT ReleaseKit"', gui)
        self.assertIn('APP_NAME = "PT-ReleaseKit"', gui)
        self.assertIn('PORTABLE_CONFIG_FILENAME = "PT-ReleaseKit-config.json"', gui)
        self.assertIn('LEGACY_PORTABLE_CONFIG_FILENAME = "PT-BDtool-config.json"', gui)
        self.assertIn('APP_NAME = "PT ReleaseKit Web"', web)
        self.assertIn("PT ReleaseKit 材料工作台", web)
        self.assertNotIn("github.com/My15sir/PT-BDtool", readme)

    def test_repository_links_use_new_slug(self) -> None:
        files = (
            source("README.md"),
            source("docs/README.en.md"),
            source("install.sh"),
            source("ptbd_core/bundle_archive.py"),
        )
        for content in files:
            with self.subTest(content=content[:40]):
                self.assertIn("My15sir/PT-ReleaseKit", content)
                self.assertNotIn("My15sir/PT-BDtool", content)

    def test_legacy_runtime_paths_remain_compatible(self) -> None:
        gui = source("ptbd-gui.py")
        installer = source("install.sh")
        portable_workflow = source(".github/workflows/controller-build.yml")
        bundle_workflow = source(".github/workflows/bundle-release.yml")

        self.assertIn("/opt/PT-BDtool", installer)
        self.assertIn("image: pt-bdtool:local", source("compose.yaml"))
        self.assertIn("PT-ReleaseKit-windows-portable.zip", portable_workflow)
        self.assertIn("PT-ReleaseKit-macos-portable.zip", portable_workflow)
        self.assertIn("PT-ReleaseKit-linux-portable.tar.gz", portable_workflow)
        self.assertIn("PT-ReleaseKit-linux-amd64.tar.gz", bundle_workflow)
        self.assertNotIn("PT-BDtool-windows-portable.zip", portable_workflow)
        self.assertNotIn("PT-BDtool-linux-amd64.tar.gz", bundle_workflow)
        self.assertIn("name: PT ReleaseKit Portable Downloads", portable_workflow)
        self.assertIn("name: PT ReleaseKit Linux Bundle Asset", bundle_workflow)
        self.assertIn("Wait for verified Linux bundle dependency", portable_workflow)
        self.assertIn('[[ "$bundle_sha" == "$GITHUB_SHA" ]]', portable_workflow)
        self.assertIn("sha256sum --check PT-ReleaseKit-linux-amd64.tar.gz.sha256", portable_workflow)
        self.assertIn("ditto -c -k --sequesterRsrc --keepParent", portable_workflow)
        self.assertIn("codesign --verify --deep --strict", portable_workflow)

    def test_compose_passes_documented_image_host_http_opt_in(self) -> None:
        compose = source("compose.yaml")

        self.assertIn(
            'PTBD_ALLOW_INSECURE_IMAGE_HOST: "${PTBD_ALLOW_INSECURE_IMAGE_HOST:-0}"',
            compose,
        )


if __name__ == "__main__":
    unittest.main()
