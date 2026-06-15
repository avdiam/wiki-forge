#!/usr/bin/env python3
"""Idempotent, fail-soft provisioner for wiki-forge's optional semantic-search deps.

Invoked by the plugin's SessionStart hook (``hooks/hooks.json``):

    python install_deps.py <plugin_root> <plugin_data>

It installs ``<plugin_root>/requirements.txt`` into ``<plugin_data>/pylib`` via
``pip install --target`` — isolated from the user's Python environment (no
global pollution, and ``--target`` sidesteps PEP 668 externally-managed-env
errors entirely). Search callsites then prepend
``PYTHONPATH="${CLAUDE_PLUGIN_DATA}/pylib"`` so ``hybrid_search.py`` can import
numpy / onnxruntime / tokenizers from there.

Two design constraints drive the shape:

* **Paths arrive as argv, NOT os.environ.** ``${CLAUDE_PLUGIN_ROOT}`` and
  ``${CLAUDE_PLUGIN_DATA}`` are command-text substitutions performed by the
  harness templating layer; they are NOT exported as environment variables into
  the spawned subprocess (verified Phase-1 Step-1: a bare ``echo
  "$CLAUDE_PLUGIN_ROOT"`` returns empty). So the hook passes them as positional
  arguments and this script reads ``sys.argv``, never ``os.environ``.

* **Fail-soft — NEVER block session start.** Semantic search degrades
  gracefully to BM25 keyword mode, so any failure here (no network, pip absent,
  timeout, disk) is logged to stderr and the script still exits 0. Only a
  successful install stamps the cached manifest, so a failed run is retried on
  the next session.

Idempotency: the bundled ``requirements.txt`` is compared against a copy cached
in ``<plugin_data>/requirements.txt`` (the documented Claude Code plugin
manifest-diff pattern). pip is invoked only when they differ or the target dir
is missing — so the steady-state cost after first provisioning is one file read.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PYLIB_DIRNAME = "pylib"
CACHED_MANIFEST = "requirements.txt"


def needs_install(req: Path, cached: Path, target: Path) -> bool:
    """True when pip must run: target missing, no cached manifest, or manifest drift.

    Pure (no side effects) so it is unit-testable without invoking pip.
    """
    if not target.is_dir():
        return True
    if not cached.is_file():
        return True
    try:
        return req.read_text(encoding="utf-8") != cached.read_text(encoding="utf-8")
    except OSError:
        # Unreadable cache → reinstall to be safe.
        return True


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(
            "install_deps: usage: install_deps.py <plugin_root> <plugin_data> "
            "(both substituted by the SessionStart hook); skipping",
            file=sys.stderr,
        )
        return 0  # fail-soft

    plugin_root = Path(argv[1])
    plugin_data = Path(argv[2])
    req = plugin_root / "requirements.txt"
    target = plugin_data / PYLIB_DIRNAME
    cached = plugin_data / CACHED_MANIFEST

    if not req.is_file():
        print(f"install_deps: no requirements.txt at {req}; skipping", file=sys.stderr)
        return 0

    if not needs_install(req, cached, target):
        return 0  # already provisioned for this manifest — silent no-op

    try:
        plugin_data.mkdir(parents=True, exist_ok=True)
        subprocess.check_call(
            [
                sys.executable, "-m", "pip", "install",
                "--quiet", "--disable-pip-version-check",
                "--upgrade",                # force replacement on manifest drift
                "--target", str(target),
                "-r", str(req),
            ],
            stdout=subprocess.DEVNULL,
        )
    except Exception as exc:  # noqa: BLE001 — fail-soft on ANY error
        print(
            f"install_deps: semantic-search deps not provisioned "
            f"({exc.__class__.__name__}); search falls back to BM25 keyword mode. "
            f"Retry next session, or run setup_search.py manually.",
            file=sys.stderr,
        )
        return 0  # fail-soft — DO NOT block session start

    # Stamp the manifest ONLY on success, so a failed install retries next session.
    try:
        cached.write_text(req.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError:
        pass  # non-fatal — worst case we reinstall next session

    print(f"install_deps: semantic-search deps provisioned into {target}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
