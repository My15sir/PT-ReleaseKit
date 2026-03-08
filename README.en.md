# PT-BDtool

PT-BDtool is a media info packaging tool.  
It turns videos, audio files, Blu-ray `BDMV` folders, and Blu-ray `ISO` images into a cleaner result package that is easier to organize, share, or archive.

Typical output looks like this:
- Video: `mediainfo.txt` + `1.png` to `6.png`
- Audio: `mediainfo.txt` + `频谱图.png`
- Disc / ISO: `BDInfo.txt` + `1.png` to `6.png`

If this is your first time using the project, follow the **quickest beginner path** below first.

## Quickest Beginner Path

### 1) Know which environment you are in

Most users fall into one of these two cases:

- **Local machine**: run on your own computer and keep the result there
- **VPS / remote Linux**: run on a server, then download the result later

If you are not sure, just start with the local-machine workflow.

### 2) Install

For beginners, this is the safer install path:

```bash
bash install.sh --offline --no-launch
export PATH="$HOME/.local/bin:$PATH"
hash -r
```

What this does:
- `--no-launch`: install first, do not jump straight into the menu yet
- `export PATH=...`: make the freshly installed commands visible in the current terminal
- `hash -r`: refresh the shell command cache so it does not keep pointing to an older install

Then verify these two commands first:

```bash
ptbd --help
bdtool status
```

If both look normal, continue.
If you also want to confirm that the GUI entry can locate its runtime files, run:

```bash
ptbd-gui --self-check
```

### 3) Make PATH persistent

A common beginner problem is: it works once, then fails in a new terminal.  
That usually means `~/.local/bin` is not permanently added to `PATH`.

For `bash`:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

For `zsh`, replace `~/.bashrc` with `~/.zshrc`.

### 4) Start

From now on, **`ptbd` is the recommended single entrypoint** for beginners.

The simplest start command is:

```bash
ptbd
```

Notes:
- `ptbd`: beginner entrypoint; uses local or VPS mode based on saved setup
- `pt` / `bdtool`: open menu mode by default
- `pt --help` / `bdtool --help`: show CLI help

If your workflow is “**control a VPS from your local machine and return the result to your local desktop automatically**”, use:

```bash
ptbd --setup
```

On first run, `--setup` asks for:
- VPS host
- SSH port
- password or key mode
- default scan roots
- local save directory

After setup, you can simply run:

```bash
ptbd
```

If you prefer a more double-click-friendly entry, you can also use:

```bash
ptbd-start
```

It automatically:
- starts a temporary local receive server
- creates the return tunnel to the VPS
- opens the remote `pt` menu
- lets you choose an item
- returns the generated package to your local desktop
- cleans the remote generated directory by default

If you want a more GUI-style workflow on **Windows / macOS / Linux**, you can also try:

```bash
ptbd-gui
```

This is the current cross-platform GUI MVP. It helps you:
- fill in the VPS address, password, and local save dir
- fetch the VPS candidate list directly into the GUI
- double-click a scanned candidate to run generate → return → cleanup automatically
- if only 1 candidate is found, clicking “one-click start” scans first and then starts automatically
- if multiple candidates are found, the GUI selects the first one but still waits for your double-click confirmation
- password mode now prefers SSH askpass, so it no longer hard-requires local `sshpass`

The repo also includes double-click launcher files:
- `PT-BDtool.bat`: better for Windows
- `PT-BDtool.command`: better for macOS
- `PT-BDtool.desktop`: better for Linux

These 3 launcher files now try to open the `ptbd-gui` window first, instead of dropping you straight into the old menu flow.

The practical idea is:
- **Windows**: double-click `PT-BDtool.bat`; install Python 3 and Git for Windows first
- **macOS**: double-click `PT-BDtool.command`; if macOS blocks the first launch, right-click and choose “Open” once
- **Linux**: double-click `PT-BDtool.desktop`, or use the installed PT-BDtool app launcher

Recommended beginner flow after the GUI opens:
1. fill in VPS host, port, password, and local save directory
2. click “scan VPS candidates”
3. double-click the item you want
4. wait for generate → return → cleanup to finish automatically

### 5) Follow the menu

Inside the menu, the normal path is:

1. type `1` to start scanning
2. choose full scan or directory scan
3. wait for the scan to finish
4. enter the item number you want
5. wait for generation and packaging
6. open the shown result directory

---

## The 3 Most Practical Workflows

### Option 1: Run on your local machine

```bash
export PATH="$HOME/.local/bin:$PATH"
pt
```

The result is stored on the current machine.

### Option 2: Run on a VPS and keep the result on the VPS first

This is the simplest and safest VPS workflow:

```bash
export PATH="$HOME/.local/bin:$PATH"
export BDTOOL_DOWNLOAD_DIR="$HOME/PT-BDtool-downloads"
pt
```

After processing, the result is usually here:

```bash
$HOME/PT-BDtool-downloads
```

Then download it from your local machine:

```bash
scp user@your-vps-ip:$HOME/PT-BDtool-downloads/*.zip .
```

If `zip` is not available, the package may be `tar.gz`.

### Option 3: Skip the menu and process one file directly

```bash
bdtool /path/to/movie.mp4 --out /path/to/output
```

Example:

```bash
bdtool ~/Videos/test.mp4 --out ~/PT-output
```

---

## Actual Runtime Model

This is the recommended way to think about it now:

- `install.sh`: installs the app and bundled offline dependencies
- `ptbd`: beginner entrypoint
- `ptbd --setup`: first-time setup
- `ptbd-start`: double-click-friendly starter
- `ptbd-gui`: cross-platform GUI launcher MVP
- `pt` / `bdtool`: legacy and advanced entrypoints
- `bdtool <file-or-dir>`: run direct CLI processing
- `bdtool doctor`: check runtime dependencies
- `bdtool status`: check installation state
- `bdtool clean`: remove the default output directory

For most beginners, only these two commands matter:

```bash
bash install.sh --offline
ptbd --setup
```

---

## Best Practice For VPS Users

If you are on a VPS, start with this and do not configure auto-return yet:

```bash
bash install.sh --offline
export PATH="$HOME/.local/bin:$PATH"
export BDTOOL_DOWNLOAD_DIR="$HOME/PT-BDtool-downloads"
pt
```

Why this is recommended:
- no desktop requirement
- no upload/return setup required
- easier troubleshooting
- lower chance of user error

### VPS Scan Advice

When `pt` runs a **full scan over SSH / on a VPS**, it now prefers these roots by default:

```bash
/home /root /data /mnt /media /srv
```

This helps avoid common noise such as:
- `node_modules`
- `.git`
- `.cache`
- `/var/lib/docker`
- `/proc` `/sys` `/dev` `/run`

If you want to define an explicit whitelist, use:

```bash
export BDTOOL_SCAN_INCLUDE_ROOTS="/home/admin/Downloads /data/media"
pt
```

If you also want extra excludes, use:

```bash
export BDTOOL_SCAN_EXCLUDE_ROOTS="/home/admin/.cache /home/admin/test"
pt
```

Notes:
- `BDTOOL_SCAN_INCLUDE_ROOTS`: whitelist roots, separated by spaces or commas
- `BDTOOL_SCAN_EXCLUDE_ROOTS`: extra excluded roots, separated by spaces or commas
- If you already know your media lives under `~/Downloads`, using a whitelist is strongly recommended

### One-Step VPS Workflow: Local Control + Auto Return

If you want end users to install once, select an item, and let everything else happen automatically, use:

```bash
ptbd --setup
```

Then for daily use:

```bash
ptbd
```

In this mode, the user only needs to:

1. run `ptbd` on the local machine
2. choose the target item in the remote menu

Desktop users can also run:

```bash
ptbd-start
```

Everything after that runs automatically:
- generate artifacts
- package the result
- return it to the local desktop
- clean the generated directory on the VPS

Default local target:

```bash
~/Desktop
```

To save somewhere else:

```bash
ptbd --setup
```

If you prefer a one-off command without setup, this still works:

```bash
ptbd-remote --host root@your-vps-ip --password 'your-password' --scan-include "/home/admin/Downloads" --save-dir /your/save/path
```

---

## If You Want Automatic Return To Your Local Machine

This is an advanced feature. It works, but only set it up after the basic flow works.

Use `BDTOOL_RETURN_MODE`:

- `local`: keep result on the current machine
- `http`: upload to an HTTP receiver
- `scp`: send back with `scp`

### Option A: HTTP auto-return

```bash
export BDTOOL_RETURN_MODE=http
export BDTOOL_RETURN_HTTP_URL='http://127.0.0.1:18080/upload'
pt
```

Legacy variable `BDTOOL_CLIENT_UPLOAD_URL` is still supported.

### Option B: SCP auto-return

SSH key authentication is strongly recommended.

```bash
export BDTOOL_RETURN_MODE=scp
export BDTOOL_RETURN_SCP_HOST='127.0.0.1'
export BDTOOL_RETURN_SCP_PORT='10022'
export BDTOOL_RETURN_SCP_USER='your-local-user'
export BDTOOL_RETURN_SCP_REMOTE_DIR='/home/your-local-user/Downloads/PT-BDtool'
export BDTOOL_RETURN_SCP_IDENTITY_FILE="$HOME/.ssh/id_ed25519"
pt
```

Optional variables:
- `BDTOOL_RETURN_SCP_PASSWORD`: only if password auth is unavoidable
- `BDTOOL_RETURN_SCP_STRICT_HOST_KEY_CHECKING`: default is `accept-new`

Notes:
- If the VPS cannot reach your local machine, set up port forwarding or a reverse tunnel first
- If you are unsure, use the “save on VPS first” workflow

---

## Useful Commands

### Show help

```bash
bdtool --help
```

### Check dependencies

```bash
bdtool doctor
```

### Check install status

```bash
bdtool status
```

### Start the menu

```bash
pt
```

### Clean the default output directory

```bash
bdtool clean
```

---

## Direct CLI Examples

### Process one video

```bash
bdtool /data/movie.mkv --out /data/output
```

### Process one audio file

```bash
bdtool /data/song.flac --out /data/output
```

### Process a whole directory

```bash
bdtool /data/media-dir --out /data/output
```

### Dry run only

```bash
bdtool /data/movie.mkv --mode dry --out /data/output
```

### Enable debug logs

```bash
bdtool /data/movie.mkv --log-level debug --out /data/output
```

---

## Common Problems

### 1) `pt: command not found` / `ptbd: command not found`

Usually `~/.local/bin` is not in `PATH`.

Try:

```bash
export PATH="$HOME/.local/bin:$PATH"
hash -r
ptbd --help
```

If that fixes it, add the PATH line to `~/.bashrc` or `~/.zshrc`.

### 2) You already installed it, but the shell still points to an old command

This usually means one of these:

- your current terminal still cached the old command path
- an older install is still earlier in `PATH`

Check it with:

```bash
export PATH="$HOME/.local/bin:$PATH"
hash -r
command -v ptbd
command -v bdtool
command -v pt
```

If the printed paths are not the ones you just installed, the simplest fix is to reinstall from the project root:

```bash
bash install.sh --offline --no-launch
export PATH="$HOME/.local/bin:$PATH"
hash -r
```

### 3) Missing `ffmpeg`, `mediainfo`, or `BDInfo`

Do not guess first. Check with:

```bash
bdtool doctor
```

If dependencies are incomplete, go back to the project root and run:

```bash
bash install.sh --offline
```

### 4) The menu opens but finds no files

Make sure you entered a directory, not an executable script path.  
Main supported inputs:

- video: `mkv`, `mp4`, `m2ts`, `ts`, `avi`, `mov`
- audio: `mp3`, `flac`, `wav`, `m4a`, `aac`
- Blu-ray: `BDMV` folders and `iso` files

### 5) On a VPS, you do not know where the package went

Set an explicit download directory:

```bash
export BDTOOL_DOWNLOAD_DIR="$HOME/PT-BDtool-downloads"
pt
```

Then everything is collected under `$HOME/PT-BDtool-downloads`.

---

## Already Verified

The current repository already has scripted verification for:

- `bdtool --help`
- `bdtool doctor`
- `bdtool status`
- direct CLI processing of a sample video
- menu scan + generate flow
- VPS-like local-save / SCP return flows

If you just want to get started, follow the install and launch steps above.

## What You Usually Get

### Video
- `mediainfo.txt`
- `1.png`
- `2.png`
- `3.png`
- `4.png`
- `5.png`
- `6.png`

### Audio
- `mediainfo.txt`
- `频谱图.png`

### Disc / ISO
- `BDInfo.txt`
- `1.png`
- `2.png`
- `3.png`
- `4.png`
- `5.png`
- `6.png`

## Uninstall

```bash
set -euo pipefail
rm -f "$HOME/.local/bin/bdtool" "$HOME/.local/bin/ptbd" "$HOME/.local/bin/ptbd-gui" \
  "$HOME/.local/bin/ptbd-start" "$HOME/.local/bin/ptbd-remote" "$HOME/.local/bin/ptbd-remote-start" \
  "$HOME/.local/bin/pt" "$HOME/.local/bin/pts" "$HOME/.local/bin/BDInfo"
rm -rf "$HOME/.local/share/pt-bdtool/PT-BDtool-app"
rm -f "$HOME/.local/share/applications/PT-BDtool.desktop" "$HOME/Desktop/PT-BDtool.desktop" "$HOME/桌面/PT-BDtool.desktop" 2>/dev/null || true
rm -f /usr/local/bin/bdtool /usr/local/bin/ptbd /usr/local/bin/ptbd-gui /usr/local/bin/ptbd-start \
  /usr/local/bin/ptbd-remote /usr/local/bin/ptbd-remote-start /usr/local/bin/pt /usr/local/bin/pts /usr/local/bin/BDInfo 2>/dev/null || true
```
