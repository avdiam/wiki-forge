#!/usr/bin/env python3
"""Download and install ONNX model + tokenizer for wiki semantic search.

Usage:
  python setup_search.py [--wiki <wiki-root>] [--yes] [--check]

Modes:
  (default)    Interactive setup — prompts before installing packages
  --yes        Non-interactive — auto-install packages, no prompts
  --check      Check if semantic search is set up (exit 0 = yes, 1 = no)

Downloads:
  - all-MiniLM-L6-v2 ONNX model (~30MB) from HuggingFace
  - tokenizer.json (~500KB) from HuggingFace

Checks for:
  - onnxruntime (pip, ~50MB)
  - tokenizers (pip, ~5MB)
  - numpy

Installs model to: <wiki>/.scripts/models/all-MiniLM-L6-v2/
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

MODEL_NAME = "all-MiniLM-L6-v2"
HF_BASE = f"https://huggingface.co/sentence-transformers/{MODEL_NAME}/resolve/main"

FILES_TO_DOWNLOAD = {
    "model.onnx": {
        "url": f"{HF_BASE}/onnx/model.onnx",
        "min_size_bytes": 20_000_000,  # ~30MB, must be at least 20MB
    },
    "tokenizer.json": {
        "url": f"{HF_BASE}/tokenizer.json",
        "min_size_bytes": 100_000,  # ~500KB, must be at least 100KB
    },
}

REQUIRED_PACKAGES = ["onnxruntime", "tokenizers", "numpy"]


def check_packages() -> list[str]:
    """Return list of missing packages."""
    missing = []
    for pkg in REQUIRED_PACKAGES:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    return missing


def install_packages(packages: list[str]) -> bool:
    """Attempt to pip install missing packages."""
    print(f"Installing: {', '.join(packages)}")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet"] + packages,
            stdout=subprocess.DEVNULL,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def download_file(url: str, dest: Path, min_size: int) -> bool:
    """Download a file with size validation."""
    print(f"  Downloading {dest.name}...", end=" ", flush=True)
    try:
        urllib.request.urlretrieve(url, str(dest))
        actual_size = dest.stat().st_size
        size_mb = actual_size / (1024 * 1024)

        if actual_size < min_size:
            print(f"FAILED (file too small: {actual_size} bytes, expected >= {min_size})")
            dest.unlink(missing_ok=True)
            return False

        print(f"done ({size_mb:.1f} MB)")
        return True
    except (urllib.error.URLError, OSError) as e:
        print(f"FAILED: {e}")
        dest.unlink(missing_ok=True)
        return False


def find_wiki_root(start: Path) -> Path | None:
    """Walk upward from start looking for a directory with CLAUDE.md."""
    current = start.resolve()
    for _ in range(10):
        if (current / "CLAUDE.md").is_file():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def is_setup_complete(wiki_root: Path) -> bool:
    """Check if semantic search is fully set up."""
    model_dir = wiki_root / ".scripts" / "models" / MODEL_NAME
    has_model = (model_dir / "model.onnx").is_file()
    has_tokenizer = (model_dir / "tokenizer.json").is_file()
    has_packages = len(check_packages()) == 0
    return has_model and has_tokenizer and has_packages


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Set up ONNX semantic search for Wiki Forge"
    )
    parser.add_argument(
        "--wiki",
        default=None,
        help="Path to the wiki root (auto-detected if omitted)",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Non-interactive mode — auto-install packages without prompting",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check if semantic search is set up (exit 0 = yes, 1 = no)",
    )
    args = parser.parse_args()

    # Find wiki root
    if args.wiki:
        wiki_root = Path(args.wiki).resolve()
    else:
        wiki_root = find_wiki_root(Path.cwd())

    if wiki_root is None or not (wiki_root / "CLAUDE.md").is_file():
        print(
            "error: Could not find wiki root (no CLAUDE.md). "
            "Use --wiki <path> to specify it.",
            file=sys.stderr,
        )
        return 2

    # --check mode: just report status
    if args.check:
        if is_setup_complete(wiki_root):
            print("installed")
            return 0
        else:
            print("not installed")
            return 1

    print(f"Wiki root: {wiki_root}")
    print()

    # Check Python packages
    print("Checking dependencies...")
    missing = check_packages()
    if missing:
        print(f"  Missing packages: {', '.join(missing)}")
        if args.yes:
            do_install = True
        else:
            try:
                reply = input("  Install them now? (y/n) ").strip().lower()
                do_install = reply in ("y", "yes")
            except EOFError:
                # Non-interactive context (e.g., called from subagent)
                print("  Non-interactive context detected. Use --yes to auto-install.")
                return 1

        if do_install:
            if not install_packages(missing):
                print("  Failed to install packages. Try manually:")
                print(f"    pip install {' '.join(missing)}")
                return 1
            still_missing = check_packages()
            if still_missing:
                print(f"  Still missing: {', '.join(still_missing)}")
                return 1
            print("  All packages installed.")
        else:
            print("  Skipping package install. Semantic search will not work.")
            print(f"  To install later: pip install {' '.join(missing)}")
            return 1
    else:
        print("  All packages present.")
    print()

    # Download model files
    model_dir = wiki_root / ".scripts" / "models" / MODEL_NAME
    model_dir.mkdir(parents=True, exist_ok=True)

    print(f"Downloading {MODEL_NAME} model to {model_dir}")
    all_ok = True
    for filename, info in FILES_TO_DOWNLOAD.items():
        dest = model_dir / filename
        if dest.is_file():
            actual_size = dest.stat().st_size
            if actual_size >= info["min_size_bytes"]:
                size_mb = actual_size / (1024 * 1024)
                print(f"  {filename} already exists ({size_mb:.1f} MB), skipping")
                continue
            else:
                print(f"  {filename} exists but is too small ({actual_size} bytes), re-downloading")
                dest.unlink()
        if not download_file(info["url"], dest, info["min_size_bytes"]):
            all_ok = False

    if not all_ok:
        print("\nSome downloads failed. Check your network and try again.")
        return 1

    print()
    print("Setup complete!")
    print()
    print("Semantic search is now available. The wiki-query skill will")
    print("automatically use it when the wiki grows beyond ~200 pages.")
    print()
    print("Tip: Add this to .gitignore to keep the model out of git:")
    print("  .scripts/models/")
    print("  .meta/embeddings_cache.json")
    print("  .meta/vectors.zvec")
    print()
    print("Optional: For faster ANN search on large wikis (2000+ pages):")
    print("  pip install zvec")
    print("  (~45MB, auto-detected — falls back to brute-force cosine without it)")

    # Write .gitignore suggestion if not already there
    gitignore = wiki_root / ".gitignore"
    lines_to_add = [".scripts/models/", ".meta/embeddings_cache.json"]
    if gitignore.is_file():
        existing = gitignore.read_text(encoding="utf-8")
        new_lines = [l for l in lines_to_add if l not in existing]
        if new_lines:
            with gitignore.open("a", encoding="utf-8") as f:
                f.write("\n# Semantic search (large, downloadable)\n")
                for line in new_lines:
                    f.write(line + "\n")
            print(f"\n  (Added to {gitignore})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
