# Product

## Register

product

## Users

PT users who keep media files on a VPS and need to prepare release materials before posting a torrent. They usually want a low-friction control surface that connects to a VPS, finds candidate video, audio, BDMV, or ISO inputs, generates the required proof files, and downloads the result package back to the local machine.

## Product Purpose

PT-BDtool reduces the manual work around PT release preparation. It connects to a remote Linux host, scans likely media locations, generates screenshots, MediaInfo, audio spectrograms, and BDInfo reports where applicable, packages the output, returns it to the local computer, and optionally cleans temporary remote files.

Success means a non-specialist user can fill in VPS connection details, choose a detected item, start processing, and understand what happened from visible logs without touching shell commands.

## Brand Personality

Practical, calm, and exact. The product should feel like a focused utility for people doing a concrete job, not a marketing site or a decorative media manager.

## Anti-references

Avoid upload-assistant complexity in the core flow, especially tracker-specific fields, image host configuration, and torrent-client automation before the basic material-generation workflow is stable. Avoid flashy dashboard visuals, decorative animation, hidden state, and UI copy that sounds smarter than the task.

## Design Principles

- Keep the main path visible: configure, scan, select, generate, download.
- Prefer one reliable action over many clever options.
- Show operational truth: logs, errors, dependency status, and downloaded paths should be easy to inspect.
- Preserve the existing CLI and shell workflows; Web is another control surface, not a replacement.
- Default to local-only operation unless the user explicitly chooses otherwise.

## Accessibility & Inclusion

Use readable Chinese labels by default, high contrast text, keyboard-reachable controls, standard form elements, and reduced-motion-safe transitions. The interface should stay usable on desktop and tablet-width browsers.
