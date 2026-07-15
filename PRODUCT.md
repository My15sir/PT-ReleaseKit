# Product

## Register

product

## Users

PT users who keep media on either a personal computer or a VPS and need to prepare release materials before posting a torrent. They want a low-friction control surface that finds candidate video, audio, BDMV, or ISO inputs, lets them choose individual items, generates the required proof files, and keeps the result package on the computer they control.

## Product Purpose

PT ReleaseKit reduces the manual work around PT release preparation. It can process selected roots on the current computer or connect to a remote Linux host, generate screenshots, MediaInfo, audio spectrograms, and BDInfo reports where applicable, package the output, and optionally upload generated images after the package reaches the controlling computer. Remote mode can also clean temporary files created on the VPS.

Success means a non-specialist user can choose local or remote processing, scan a deliberately bounded location, select one or more detected items, start processing, and understand what happened from visible progress and logs without touching shell commands.

## Brand Personality

Practical, calm, and exact. The product should feel like a focused utility for people doing a concrete job, not a marketing site or a decorative media manager.

## Anti-references

Keep upload assistance optional and downstream of material generation. Support a small, provider-oriented image-host integration, but do not turn the core flow into a tracker-specific posting form, torrent-client controller, or mandatory upload pipeline. Avoid flashy dashboard visuals, decorative animation, hidden state, and UI copy that sounds smarter than the task.

## Design Principles

- Keep the main path visible: choose location, configure, scan, select, generate, and optionally upload.
- Prefer one reliable action over many clever options.
- Show operational truth: logs, errors, dependency status, and downloaded paths should be easy to inspect.
- Preserve the existing CLI and shell workflows; Web is another control surface, not a replacement.
- Keep scans bounded by default: local mode uses the selected media root, while remote mode scans only `/home` until the user explicitly adds roots or enables full scan.
- Keep media and credentials in the narrowest practical trust boundary. Image hosting is off by default, and remote desktop uploads happen only after the result package returns so the image-host token is never sent to the media VPS.

## Accessibility & Inclusion

Use readable Chinese labels by default, high contrast text, keyboard-reachable controls, standard form elements, and reduced-motion-safe transitions. Give scan results enough room for individual selection while keeping progress and logs visible. The interface should stay usable on desktop and tablet-width browsers.
