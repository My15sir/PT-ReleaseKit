# PT ReleaseKit

PT ReleaseKit (formerly PT-BDtool) scans videos, audio files, `BDMV` directories, and `ISO` images. It creates screenshots, MediaInfo, audio spectra or BDInfo reports, then packages the generated files.

> Rename compatibility: the GitHub repository now uses `PT-ReleaseKit`. Existing `bdtool` and `ptbd-*` commands, `pt-bdtool:*` Docker image tags, configuration paths, and `PT-BDtool-*` download filenames remain stable, so current installations and automation do not need migration.

The project now uses a **Python-first modular core**, keeps a Shell compatibility layer, retains the Windows/macOS desktop GUI, and adds a Docker deployment for local processing on the media VPS.

## Generated Files

- Video: `mediainfo.txt` and `1.png` through `6.png`
- Audio: `mediainfo.txt` and `频谱图.png`
- `BDMV` / `ISO`: `BDInfo.txt` and `1.png` through `6.png`
- Package: normally `.zip`, with `.tar.gz` as a fallback

## Choose a Runtime

### Windows and macOS desktop GUI

The desktop applications remain supported. They are intended for users who control a media VPS from a personal computer and want the scan, processing, download, and cleanup flow in one interface.

Download a package from the [`portable-latest`](https://github.com/My15sir/PT-ReleaseKit/releases/tag/portable-latest) release:

- Windows: extract `PT-BDtool-windows-portable.zip` and run `PT-BDtool.exe`
- macOS: extract `PT-BDtool-macos-portable.zip` and run `PT-BDtool.app`
- Linux: extract `PT-BDtool-linux-portable.tar.gz`

Source launchers are also retained:

- Windows: `PT-BDtool.bat`
- macOS: `PT-BDtool.command`
- Linux: `PT-BDtool.sh`
- Cross-platform GUI wrapper: `ptbd-gui`

Running the GUI from source requires Python 3 and Tk. Connection diagnostics and the built-in remote backend require Paramiko; without it, scan and processing operations fall back to system Bash and SSH. Release applications are built with PyInstaller and include the controller dependencies.

Before connecting to a new VPS for the first time, run `ssh -p PORT USER@HOST` in a system terminal and verify the displayed host-key fingerprint through a trusted channel before accepting it. The desktop controller reads `~/.ssh/known_hosts` and rejects unknown or changed host keys so SSH credentials are not sent to an unverified server.

On first use, enter the VPS target (for example `root@host`), SSH port, a password or existing key, and the local save directory. The normal flow is save configuration, scan candidates, select entries, process remotely, and download the archive. Automatic cleanup only removes output directories and temporary packages created by that run; it never deletes the source media.

Desktop configuration and log locations:

- Linux: `~/.config/ptbd-gui/config.json` and `PT-BDtool.log` in the same directory
- Windows portable app: `PT-BDtool-config.json` next to the executable
- macOS portable app: `PT-BDtool-config.json` next to `PT-BDtool.app`
- Shell remote mode: `~/.config/ptbd-remote/config.env`

If scanning or processing fails, first confirm that the system terminal can log in over SSH, that the VPS account can read the media directory, and that the log does not report missing dependencies or package-repository failures. Remote automatic installation primarily supports Debian, Ubuntu, and Alpine; other distributions may need manual dependency setup.

If the VPS disables the SFTP subsystem, the built-in backend automatically falls back to SSH pipe transfers. No additional network port is required.

### Docker on the media VPS

Deploy Docker on the **same VPS that stores the media**. The container reads media from a read-only bind mount and writes generated files to a host output directory. It is not a separate remote-processing hop.

```bash
export PTBD_UID=1000
export PTBD_GID=1000
sudo mkdir -p /srv/media /srv/ptbd/output /srv/ptbd/config
sudo chown "$PTBD_UID:$PTBD_GID" /srv/ptbd/output /srv/ptbd/config

PTBD_MEDIA_DIR=/srv/media \
PTBD_OUTPUT_DIR=/srv/ptbd/output \
PTBD_CONFIG_DIR=/srv/ptbd/config \
docker compose up -d --build
```

Compose binds the Web port to the VPS loopback interface by default. Create an SSH tunnel from the desktop computer:

```bash
ssh -L 8899:127.0.0.1:8899 user@VPS-IP
```

Then open locally:

```text
http://127.0.0.1:8899/
```

Compose mounts:

- `PTBD_MEDIA_DIR` at `/media` as read-only
- `PTBD_OUTPUT_DIR` at `/output` as writable
- `PTBD_CONFIG_DIR` at `/config` as writable
- `PTBD_UID` / `PTBD_GID` select the non-root container identity; the default is `1000:1000`
- `PTBD_WEB_PORT` selects the loopback host port; the default is `8899`

The container drops all Linux capabilities, enables `no-new-privileges`, and runs as a non-root user. The output and config directories must be writable by the configured UID/GID.

See [`DOCKER.md`](DOCKER.md) for deployment, upgrades, reverse proxy examples, permissions, and troubleshooting.

### Linux or VPS command line

Process one file or directory:

```bash
./bdtool /path/to/movie.mkv --out /path/to/output
```

Scan a directory and return JSON:

```bash
./bdtool scan-json --dir /path/to/media
```

Check runtime commands:

```bash
./bdtool doctor
./bdtool status
```

The Python core requires `python3`, `ffmpeg`, `ffprobe`, and `mediainfo`. `BDInfo` is recommended for Blu-ray processing. NumPy and Pillow are required only for combined audio spectra, not single-track spectra. Remote bootstrap installs them according to the selected mode, while the Docker image includes them. The installer and Docker image prepare the intended runtime environment.

Other retained entrypoints are `./ptbd` for the guided flow, `./ptbd-start.sh` for the local menu, and `./ptbd-remote.sh` for the direct Shell remote workflow.

## Python-first and Shell Compatibility

`bdtool` remains the stable entrypoint. Processing commands are dispatched to `python3 -m ptbd_core.cli` by default. The core is split by responsibility:

- `scanner.py`: media discovery and classification
- `media_tools.py`: external media-tool execution
- `artifacts.py`: screenshots, MediaInfo, spectra, and BDInfo artifacts
- `pipeline.py`: processing orchestration
- `returns.py`: packaging and return transports
- `config.py`, `models.py`, and `jobs.py`: shared configuration, models, and task state

Compatibility behavior is deliberate. `bdtool` delegates to `bdtool-legacy.sh` when:

- it is called without arguments, preserving the interactive menu
- it receives legacy menu arguments such as `start`, `install`, `--lang`, or `--non-interactive`
- `PTBD_PYTHON_CORE=0` is set
- Python 3 is unavailable

Run the compatibility implementation explicitly with:

```bash
./bdtool-legacy.sh
```

New processing behavior should be implemented in `ptbd_core/`; Shell remains for compatibility, the legacy menu, and GUI/Web remote fallback when Paramiko is unavailable.

## Desktop Workflow

The desktop app now separates the primary workbench from connection settings. The workbench keeps the four workflow stages, candidates, task controls, and live log visible in the default window; first launch opens the connection tab so placeholder credentials are not treated as a valid setup.

The remote scan defaults to `/home /root /data /mnt /media /srv`. Use an explicit scan whitelist to restrict the roots; it takes precedence over full scan. Enable full scan only when media is outside the preferred roots. Separate multiple roots with spaces or commas. Double-quote an individual path containing spaces, commas, or apostrophes, for example `"/data/PT Movies" "/mnt/O'Brien, Archive"`.

The normal workflow is **Save connection**, **Test connection**, **Scan VPS**, select one or more entries, then **Generate selected**. Batch processing continues after an individual failure, reports separate success and failure totals, lets the GUI retry failed entries, and exposes an **Open output folder** action.

## Web Modes

Start the source Web controller locally:

```bash
./ptbd-web --host 127.0.0.1 --port 8899 --open
```

The controller supports:

- `remote`: the desktop/controller machine operates a VPS over SSH
- `local`: the process scans a directory on its own host; Docker uses this mode with `/media` as its root

The Web API supports connection diagnostics and batch results. Process jobs expose `failed` and `result_summary`, and finish as `success`, `partial`, `error`, or `cancelled`. Reloading the page restores the active task and its log. Local mode hides SSH-only fields and labels the mounted media and host output paths explicitly.

Run local mode directly on Linux:

```bash
PTBD_WEB_MODE=local \
PTBD_WEB_LOCAL_ROOT=/srv/media \
./ptbd-web --host 127.0.0.1 --port 8899
```

After startup, set the save directory to `/srv/ptbd/output` in the Web configuration.

## Install from Source

```bash
bash install.sh --offline --no-launch
export PATH="$HOME/.local/bin:$PATH"
hash -r
```

Verify the installation:

```bash
bdtool --version
bdtool doctor
ptbd-gui --self-check
```

`install.sh` does not invoke a host package manager. The offline bundle can be prepared with `scripts/ensure-bundle.py`. Official downloads prefer the Release `.sha256` sidecar; the legacy official asset is authenticated by a repository-pinned digest during migration.

Custom bundle mirrors support `PTBD_BUNDLE_URL`, the highest-priority trusted digest `PTBD_BUNDLE_SHA256`, and `PTBD_BUNDLE_CHECKSUM_URL` (default: the archive URL plus `.sha256`). Downloads fail closed by default. `PTBD_BUNDLE_ALLOW_UNVERIFIED=1` explicitly permits a custom download only when its checksum URL is unavailable; a malformed sidecar still fails.

## Development Checks

```bash
python3 -m unittest discover -s tests
python3 -m compileall -q ptbd_core ptbd-gui.py ptbd-web.py ptbd_remote_backend.py
./scripts/full-test.sh
docker build -t pt-bdtool:local .
```

Maintainer documentation:

- [`DEVELOPMENT.md`](DEVELOPMENT.md)
- [`REPO-INDEX.md`](REPO-INDEX.md)
- [`DOCKER.md`](DOCKER.md)
