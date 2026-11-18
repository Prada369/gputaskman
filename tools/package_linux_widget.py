#!/usr/bin/env python3
"""Build a small, self-contained Ubuntu source bundle for the GTK GPU widget."""
from __future__ import annotations

import argparse
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "packaging" / "linux-widget"
VERSION = (PACKAGE / "VERSION").read_text(encoding="utf-8").strip()


def files_to_package() -> list[tuple[Path, Path]]:
    files: list[tuple[Path, Path]] = []
    for name in ("VERSION", "README.md", "requirements.txt", "install.sh", "uninstall.sh"):
        path = PACKAGE / name
        files.append((path, Path("gputaskman-widget") / name))
    files.append((ROOT / "LICENSE", Path("gputaskman-widget/LICENSE")))
    for source, target_root in (
        (ROOT / "tools" / "desktop_widget.py", Path("gputaskman-widget/tools")),
        (ROOT / "tools" / "gputaskman-vram-fan-target", Path("gputaskman-widget/tools")),
        (ROOT / "src" / "gputaskman", Path("gputaskman-widget/src/gputaskman")),
        (ROOT / "assets" / "gpus", Path("gputaskman-widget/assets/gpus")),
    ):
        paths = [source] if source.is_file() else sorted(source.rglob("*"))
        for path in paths:
            if not path.is_file() or "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            files.append((path, target_root / path.relative_to(source) if source.is_dir() else target_root / path.name))
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / f"gputaskman-widget-{VERSION}-ubuntu.zip")
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    files = files_to_package()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source, target in files:
            archive.write(source, target.as_posix())
    print(f"Wrote {output} ({len(files)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
