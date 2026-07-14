#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    port = int(os.environ.get("PTBD_WEB_PORT", "8899"))
    base_path = os.environ.get("PTBD_WEB_BASE_PATH", "").strip()
    if base_path and not base_path.startswith("/"):
        base_path = "/" + base_path
    base_path = base_path.rstrip("/")
    url = f"http://127.0.0.1:{port}{base_path}/api/status"
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            payload = json.load(response)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        print(f"healthcheck failed: {exc}", file=sys.stderr)
        return 1
    if response.status != 200 or payload.get("ok") is not True:
        print(f"healthcheck returned an unhealthy response: {payload!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
