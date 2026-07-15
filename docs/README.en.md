# PT ReleaseKit

PT ReleaseKit (formerly PT-BDtool) scans videos, audio files, `BDMV` directories, and `ISO` images. It creates screenshots, MediaInfo, audio spectra or BDInfo reports, then packages the generated files.

> Rename compatibility: the GitHub repository now uses `PT-ReleaseKit`. Existing `bdtool` and `ptbd-*` commands, `PTBD_*` environment variables, `pt-bdtool:*` Docker image tags, install paths, configuration paths, and legacy `PT-BDtool.*` source launchers remain compatible. New Release archives and packaged applications use `PT-ReleaseKit` filenames.

The project now uses a **Python-first modular core**, keeps a Shell compatibility layer, supports local or remote processing in the Windows/macOS/Linux desktop GUI, and adds a Docker deployment for local processing on the media VPS.

## Generated Files

- Video: `mediainfo.txt` and `1.png` through `6.png`
- Audio: `mediainfo.txt` and `频谱图.png`
- `BDMV` / `ISO`: `BDInfo.txt` and `1.png` through `6.png`
- Package: normally `.zip`, with `.tar.gz` as a fallback
- Optional image-host upload: `image-host.json`, `image-host-links.txt`, and `image-host-bbcode.txt` are added to the result ZIP

## Choose a Runtime

### Windows and macOS desktop GUI

The desktop applications remain supported and offer two processing locations: media on the current computer, or a media VPS controlled from the personal computer. Local mode works without a VPS.

Download a package from the [`portable-latest`](https://github.com/My15sir/PT-ReleaseKit/releases/tag/portable-latest) release:

- Windows: extract `PT-ReleaseKit-windows-portable.zip` and run `PT-ReleaseKit.exe`
- macOS: extract `PT-ReleaseKit-macos-portable.zip` and run `PT-ReleaseKit.app`
- Linux: extract `PT-ReleaseKit-linux-portable.tar.gz`

Source launchers are also retained:

- Windows: `PT-ReleaseKit.bat`
- macOS: `PT-ReleaseKit.command`
- Linux: `PT-ReleaseKit.sh` or `PT-ReleaseKit.desktop`
- Cross-platform GUI wrapper: `ptbd-gui`

The older `PT-BDtool.bat`, `PT-BDtool.command`, `PT-BDtool.sh`, and `PT-BDtool.desktop` files remain as compatibility launchers.

Running the GUI from source requires Python 3 and Tk. Connection diagnostics and the built-in remote backend require Paramiko; without it, remote scan and processing operations fall back to system Bash and SSH. Release applications are built with PyInstaller and include the controller dependencies.

Portable packages **do not bundle** `ffmpeg`, `ffprobe`, `mediainfo`, or `BDInfo`. When **Local computer** is selected, `ffmpeg`, `ffprobe`, and `mediainfo` must be available on the current computer through `PATH`; `BDInfo` is optional for Blu-ray processing. Use **Check local dependencies** before scanning. A missing required tool blocks local processing and is reported explicitly. The Docker image includes the media-processing dependencies instead.

The frozen desktop applications currently generate **single-track audio spectra**. Do not treat the portable package as a combined-spectrum runtime. Use Docker/Web or the source CLI with NumPy and Pillow when a multi-track combined spectrum is required.

Only remote mode needs SSH. Before connecting to a new VPS for the first time, run `ssh -p PORT USER@HOST` in a system terminal and verify the displayed host-key fingerprint through a trusted channel before accepting it. The desktop controller reads `~/.ssh/known_hosts` and rejects unknown or changed host keys so SSH credentials are not sent to an unverified server.

On first use, choose **Local computer** or **Remote VPS**, then enter either the local media root or the SSH settings and a local save directory. The normal flow is save configuration, check the environment, scan candidates, select individual entries, generate, and inspect the archive. Automatic cleanup only removes work directories and temporary packages created by that run; it never deletes the source media.

An empty remote **Scan directories** field scans only `/home`. `/root`, `/data`, `/mnt`, `/media`, `/srv`, and other top-level directories are no longer traversed by default. Add the required paths explicitly, or enable full VPS scan only when it is genuinely needed. Local mode scans only the chosen local media root and explicitly added roots, not the entire computer.

Desktop configuration and log locations:

- Linux: `~/.config/ptbd-gui/config.json` and `PT-ReleaseKit.log` in the same directory
- Windows portable app: `PT-ReleaseKit-config.json` next to the executable
- macOS portable app: `PT-ReleaseKit-config.json` next to `PT-ReleaseKit.app`
- Shell remote mode: `~/.config/ptbd-remote/config.env`

Existing settings remain usable after upgrading. When the new configuration does not exist, the application reads the legacy `PT-BDtool-config.json`, Windows `%APPDATA%/PT-BDtool/gui-config.json`, or macOS `Application Support/PT-BDtool/gui-config.json`; the next save writes the new filename.

If remote scanning or processing fails, first confirm that the system terminal can log in over SSH, that the VPS account can read the explicit scan roots, and that the log does not report missing dependencies or package-repository failures. Remote automatic installation primarily supports Debian, Ubuntu, and Alpine; other distributions may need manual dependency setup. For local mode, run **Check local dependencies** and verify the media-root and output-directory permissions.

If the VPS disables the SFTP subsystem, the built-in backend automatically falls back to SSH pipe transfers. No additional network port is required.

### Docker on the media VPS

Deploy Docker on the **same VPS that stores the media**. The container reads media from a read-only bind mount and writes generated files to a host output directory. It is not a separate remote-processing hop.

The Docker image includes `ffmpeg`, `ffprobe`, `mediainfo`, and the image-provided Blu-ray tooling. It is a dependencies-included local runtime, unlike the desktop portable controller, and does not require those media tools to be installed separately on the host for container use.

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

The Python core requires `python3`, `ffmpeg`, `ffprobe`, and `mediainfo`. `BDInfo` is recommended for Blu-ray processing. NumPy and Pillow are required only for combined audio spectra, not single-track spectra. Remote bootstrap and the Docker image prepare the intended runtime environment; desktop portable packages do not bundle the large system media tools.

Other retained entrypoints are `./ptbd` for the guided flow, `./ptbd-start.sh` for the local menu, and `./ptbd-remote.sh` for the direct Shell remote workflow.

### Optional image-host upload

**Upload generated screenshots to an image host** is disabled by default. Enabling it requires a provider and API token:

- `ImgBB`: uses the built-in upload endpoint unless a compatible endpoint override is entered
- `Lsky Pro v2`: requires the full upload API endpoint
- `S.EE / SM.MS`: defaults to S.EE and accepts a compatible SM.MS endpoint override
- `custom`: uses a Bearer-token API that returns an image URL in JSON

Image-host endpoints follow an HTTPS-by-default policy. Only loopback endpoints such as `localhost`, `127.0.0.1`, and `[::1]` may use plain HTTP; every other endpoint must use HTTPS. If you explicitly accept plaintext exposure of the token and screenshots on a trusted private network, set `PTBD_ALLOW_INSECURE_IMAGE_HOST=1` before starting the controller. Do not use this override for a public HTTP endpoint.

Remote image hosting in the desktop GUI or Web controller is supported only by the built-in Python/Paramiko backend. PT ReleaseKit first generates the materials on the VPS and returns the package to the controller; the controller then reads the screenshots from the ZIP and uploads them. The token is not placed in SSH commands or the remote runtime and is never sent to the media VPS. If a source environment lacks Paramiko and falls back to local Bash/SSH, material generation and download may continue but image hosting is explicitly skipped. Install Paramiko or use a portable package for remote image hosting. Local processing does not use this remote Shell fallback and can perform image-host post-processing normally.

In Docker local mode, the controller itself runs in the VPS container, so its token is stored in that deployment's `/config/config.json`; keep the service behind its loopback binding, an SSH tunnel, or an authenticated reverse proxy.

After upload, the result ZIP contains:

- `image-host.json`: provider, per-image success/failure state, and public URLs
- `image-host-links.txt`: one successful image URL per line
- `image-host-bbcode.txt`: ready-to-paste `[img]...[/img]` lines

Upload is non-critical post-processing. A single-image or whole-upload failure does not delete generated materials or turn an otherwise successful generation job into a failure. Archive metadata is replaced atomically; if it cannot be updated safely, the original ZIP remains intact. Image-host processing supports regular ZIP results only; a `.tar.gz` fallback is preserved but not rewritten.

Passwords and image-host tokens are stored only in private configuration. On POSIX systems the file is written with mode `0600`; public Web configuration responses, normal logs, and errors expose only whether a token is saved, never its value.

## Python-first and Shell Compatibility

`bdtool` remains the stable entrypoint. Processing commands are dispatched to `python3 -m ptbd_core.cli` by default. The core is split by responsibility:

- `scanner.py`: media discovery and classification
- `media_tools.py`: external media-tool execution
- `artifacts.py`: screenshots, MediaInfo, spectra, and BDInfo artifacts
- `pipeline.py`: processing orchestration
- `returns.py`: packaging and return transports
- `local_runtime.py`: desktop/Web local execution and path boundaries
- `image_hosts.py`: optional post-return upload and ZIP manifests
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

The desktop app separates the primary workbench from connection settings. The workbench gives most of its space to a scrollable candidate list while keeping the four workflow stages, task controls, and live log visible. In Tk, click the **Select** column or press Space to toggle an individual item; **Only current**, **Select all results**, and **Clear selection** provide explicit bulk actions. Filtering preserves checked paths, while a new scan drops paths that no longer exist.

First launch opens the settings tab. Choose **Local computer** and a media root, or **Remote VPS** and its SSH settings, then select the local output directory. Local mode requires `ffmpeg`, `ffprobe`, and `mediainfo` on the current computer; `BDInfo` is optional. The dependency check reports missing tools before processing.

The remote scan defaults to `/home` only. `/root`, `/data`, `/mnt`, `/media`, `/srv`, and any other top-level media root must be entered explicitly in **Scan directories**, or reached by deliberately enabling full scan. An explicit whitelist takes precedence over full scan. Separate multiple roots with spaces or commas. Double-quote an individual path containing spaces, commas, or apostrophes, for example `"/data/PT Movies" "/mnt/O'Brien, Archive"`. Local mode is bounded to the selected media root plus explicit extra roots.

The normal workflow is **Save settings**, check the local dependencies or remote connection, scan the selected location, check one or more entries, then **Generate selected**. Batch processing continues after an individual failure, reports separate success and failure totals, lets the GUI retry failed entries, and exposes an **Open output folder** action. If image hosting is enabled, upload runs after the archive is local and the GUI exposes copyable links and BBCode.

Scanning reports real work rather than an estimated percentage. Directory walking shows live directory, file, candidate, and current-path counters. Candidate resolution switches to a determinate completed/total ratio and identifies the file being inspected by `ffprobe`. Desktop and Web controllers stop a scan after 120 seconds without any output instead of waiting indefinitely.

## Web Modes

Start the source Web controller locally:

```bash
./ptbd-web --host 127.0.0.1 --port 8899 --open
```

The controller supports:

- `remote`: the desktop/controller machine operates a VPS over SSH
- `local`: the process scans a directory on its own host; Docker uses this mode with `/media` as its root

The Web API supports environment diagnostics and batch results. The candidate workbench occupies the main content area, gives each item its own checkbox, and provides **Only this item** without forcing a select-all action. Selection survives filtering and pagination. Process jobs expose `failed`, `result_summary`, and non-fatal image-upload reports, and finish as `success`, `partial`, `error`, or `cancelled`. Reloading the page restores the active task and its log. Local mode hides SSH-only fields and labels the mounted media and host output paths explicitly; remote mode uses the same `/home`-only default as the desktop GUI.

Web and desktop GUI share the optional image-host configuration and result manifests. The page shows successful links and offers BBCode copying, while public configuration and task status never expose the token.

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

`install.sh` does not invoke a host package manager. The offline bundle can be prepared with `scripts/ensure-bundle.py`. The renamed official bundle requires its Release `.sha256` sidecar or an explicit digest; only the legacy filename may use the repository-pinned migration digest.

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
