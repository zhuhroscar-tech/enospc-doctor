"""enospc-doctor CLI."""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .core import diagnose_all, CAUSE_OK, CAUSE_DIAGNOSTIC_FAILED
from .style import print_fields, resolve_style, status_headline


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="enospc-doctor",
        description=(
            "Diagnose which distinct cause is behind a 'No space left on "
            "device' (ENOSPC) error: genuine block exhaustion, inode "
            "exhaustion, deleted-but-open files, or reserved-block false-full. "
            "Strictly read-only: never deletes, truncates, or restarts anything."
        ),
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument(
        "--threshold", type=int, default=95,
        help="Percent usage considered 'near full' for diagnosis (default: 95).",
    )
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    p.add_argument(
        "--all", action="store_true",
        help="Show every mount, including ones with no issue (default: only show issues).",
    )
    p.add_argument("--no-color", action="store_true", help="Disable colored output.")
    return p


def _print_text(reports, show_all: bool, style) -> None:
    shown = 0
    for r in reports:
        if not show_all and r.cause == CAUSE_OK:
            continue
        shown += 1
        level = "ok" if r.cause == CAUSE_OK else "fail"
        print()
        print(status_headline(style, level, f"{r.mountpoint} ({r.filesystem}): {r.cause}"))
        print(f"  {r.explanation}")
        if r.cause == CAUSE_DIAGNOSTIC_FAILED:
            continue
        rows = [
            ("block use", f"{r.block_use_pct}%"),
            ("inode use", f"{r.inode_use_pct}%"),
        ]
        if r.reserved_block_pct:
            rows.append(("reserved blocks", f"{r.reserved_block_pct}%"))
        print_fields(rows)
        if r.deleted_open_files:
            print("  deleted-but-open files pinning space:")
            for f in r.deleted_open_files[:10]:
                size = f"{f.size_bytes} bytes" if f.size_bytes else "size unknown"
                print(f"    pid {f.pid} ({f.command}): {f.path} [{size}]")
    if shown == 0:
        print(status_headline(style, "ok", "No ENOSPC-relevant issues found on any mounted filesystem."))


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    reports = diagnose_all(near_full_threshold=args.threshold)

    if args.json:
        print(json.dumps([r.to_dict() for r in reports], indent=2))
    else:
        style = resolve_style(no_color_flag=args.no_color)
        _print_text(reports, show_all=args.all, style=style)

    if any(r.cause != CAUSE_OK for r in reports):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
