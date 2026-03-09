#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import venv
from pathlib import Path


APP_NAME = "PT-BDtool"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = PROJECT_ROOT / "build" / "controller-build-venv"
BUILD_ROOT = PROJECT_ROOT / "build" / "controller-app"
DIST_ROOT = PROJECT_ROOT / "dist" / "controller-app"
BUILD_REQUIREMENTS = [
    "paramiko>=3.5,<4",
    "PyInstaller>=6.6,<7",
]


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command))
    subprocess.run(command, check=True, env=env)


def ensure_linux_bundle() -> None:
    bundle_root = PROJECT_ROOT / "third_party" / "bundle" / "linux-amd64"
    required = [
        bundle_root / "bin" / "ffmpeg",
        bundle_root / "bin" / "ffprobe",
        bundle_root / "bin" / "mediainfo",
        bundle_root / "bin" / "BDInfo",
        bundle_root / "lib",
    ]
    if all(path.exists() for path in required):
        return
    ensure_script = PROJECT_ROOT / "scripts" / "ensure-bundle.py"
    if not ensure_script.exists():
        raise SystemExit(f"missing bundle helper: {ensure_script}")
    python_bin = sys.executable or "python3"
    run([python_bin, str(ensure_script)])


def venv_python_path(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    python_bin = venv_dir / "bin" / "python3"
    if python_bin.exists():
        return python_bin
    return venv_dir / "bin" / "python"


def ensure_build_venv() -> Path:
    python_bin = venv_python_path(VENV_DIR)
    if not python_bin.exists():
        builder = venv.EnvBuilder(with_pip=True, clear=False, upgrade=False)
        builder.create(str(VENV_DIR))
    python_bin = venv_python_path(VENV_DIR)
    if not python_bin.exists():
        raise SystemExit(f"build venv python missing: {python_bin}")
    return python_bin


def install_build_deps(python_bin: Path) -> None:
    run([str(python_bin), "-m", "pip", "install", "--upgrade", "pip"])
    run([str(python_bin), "-m", "pip", "install", *BUILD_REQUIREMENTS])


def add_data_sep() -> str:
    return ";" if os.name == "nt" else ":"


def format_add_data(source: Path, dest_dir: str) -> str:
    return f"{source}{add_data_sep()}{dest_dir}"


def iter_data_entries() -> list[tuple[Path, str]]:
    entries: list[tuple[Path, str]] = [
        (PROJECT_ROOT / "bdtool", "."),
        (PROJECT_ROOT / "bdtool.sh", "."),
        (PROJECT_ROOT / "ptbd-remote.sh", "."),
        (PROJECT_ROOT / "scripts" / "prepare-remote-runtime.sh", "scripts"),
        (PROJECT_ROOT / "scripts" / "remote-upload-server.py", "scripts"),
    ]
    for base_dir in (
        PROJECT_ROOT / "lib",
        PROJECT_ROOT / "third_party" / "bundle" / "linux-amd64",
    ):
        for file_path in sorted(path for path in base_dir.rglob("*") if path.is_file()):
            relative_parent = file_path.relative_to(PROJECT_ROOT).parent.as_posix()
            entries.append((file_path, relative_parent))
    return entries


def platform_name() -> str:
    system = platform.system()
    if system == "Windows":
        return "windows"
    if system == "Darwin":
        return "macos"
    return system.lower()


def clean_build_dirs(build_dir: Path, dist_dir: Path, spec_dir: Path) -> None:
    for path in (build_dir, dist_dir, spec_dir):
        if path.exists():
            shutil.rmtree(path)


def build_artifact(python_bin: Path) -> Path:
    system = platform.system()
    platform_dir = platform_name()
    build_dir = BUILD_ROOT / platform_dir / "work"
    spec_dir = BUILD_ROOT / platform_dir / "spec"
    dist_dir = DIST_ROOT / platform_dir
    clean_build_dirs(build_dir, dist_dir, spec_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    spec_dir.mkdir(parents=True, exist_ok=True)
    dist_dir.mkdir(parents=True, exist_ok=True)

    command = [
        str(python_bin),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        APP_NAME,
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(build_dir),
        "--specpath",
        str(spec_dir),
        "--windowed",
        "--collect-submodules",
        "paramiko",
    ]
    if system == "Windows":
        command.append("--onefile")
    else:
        command.append("--onedir")
    if system == "Darwin":
        command.extend(["--osx-bundle-identifier", "com.my15sir.ptbdtool"])

    for source, dest_dir in iter_data_entries():
        command.extend(["--add-data", format_add_data(source, dest_dir)])

    command.append(str(PROJECT_ROOT / "ptbd-gui.py"))
    run(command)

    if system == "Windows":
        artifact = dist_dir / f"{APP_NAME}.exe"
    elif system == "Darwin":
        artifact = dist_dir / f"{APP_NAME}.app"
    else:
        artifact = dist_dir / APP_NAME
    if not artifact.exists():
        raise SystemExit(f"build finished but artifact missing: {artifact}")
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser(description="Build standalone PT-BDtool controller app")
    parser.add_argument("--skip-deps", action="store_true", help="Skip pip install inside build venv")
    args = parser.parse_args()

    python_bin = ensure_build_venv()
    if not args.skip_deps:
        install_build_deps(python_bin)

    ensure_linux_bundle()
    artifact = build_artifact(python_bin)
    print(f"[build-controller] artifact={artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
