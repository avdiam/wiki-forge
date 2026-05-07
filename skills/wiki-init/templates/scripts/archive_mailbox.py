#!/usr/bin/env python3
"""Archive aged terminal-status mailbox messages.

Mtime-based archival: enumerates `*.md` at the top level of the mailbox
directory (skips README.md and any subdirectory, including `.archive/`),
parses `status:` from frontmatter, and moves messages matching the
retention rules into `<mailbox>/.archive/YYYY-MM/`.

Retention rules (must match vault/.clipper-rules.md § Step 5):
  - status: closed       and age >= 7 days  → archive
  - status: acknowledged and age >= 7 days  → archive
  - status: replied      and age >= 14 days → archive
  - status: open                            → never archive

Mtime is the trigger because the recipient's status-flip already updates
it as a side effect — no protocol change needed. Filenames retained so
cross-references from still-open threads remain resolvable via path nav.

Used by the wake-up drill (vault/.wiki-identity.md drill step 1) after
the mailbox sweep. The clipper side runs the symmetric procedure in
JavaScript via the Obsidian REST API — see vault/.clipper-rules.md § 5.

Silent on no-op so the drill stays terse.

Usage:
    python archive_mailbox.py vault/_mailbox
    python archive_mailbox.py vault/_mailbox --dry-run
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


SECONDS_PER_DAY = 86400
RETENTION_DAYS = {
    "closed": 7,
    "acknowledged": 7,
    "replied": 14,
}
STATUS_RE = re.compile(r"^status:\s*(\S+)", re.MULTILINE)


def parse_status(text: str) -> str | None:
    """Pull the `status:` value from the leading frontmatter block, if any."""
    parts = text.split("\n---", 2)
    if len(parts) < 2:
        return None
    m = STATUS_RE.search(parts[0])
    return m.group(1) if m else None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Archive aged terminal-status mailbox messages by mtime + status."
    )
    parser.add_argument("mailbox", help="Path to the mailbox directory (e.g., vault/_mailbox).")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be archived; do not move any files.",
    )
    args = parser.parse_args(argv)

    mailbox = Path(args.mailbox)
    if not mailbox.is_dir():
        print(f"error: {mailbox} is not a directory", file=sys.stderr)
        return 2

    now = time.time()
    moved = 0

    for msg in sorted(mailbox.glob("*.md")):
        if msg.name == "README.md":
            continue
        try:
            text = msg.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            print(f"warning: skipped {msg.name} ({e})", file=sys.stderr)
            continue

        status = parse_status(text)
        if status not in RETENTION_DAYS:
            continue

        age_days = (now - msg.stat().st_mtime) / SECONDS_PER_DAY
        if age_days < RETENTION_DAYS[status]:
            continue

        target_dir = mailbox / ".archive" / datetime.now().strftime("%Y-%m")
        target = target_dir / msg.name
        if args.dry_run:
            print(
                f"[dry-run] would archive: {msg.name} "
                f"(status={status}, age={age_days:.1f}d) → {target}"
            )
        else:
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(msg), str(target))
            print(f"archived: {msg.name} → {target.relative_to(mailbox.parent)}")
        moved += 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
