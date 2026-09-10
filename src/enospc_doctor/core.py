"""Core logic for enospc-doctor.

The problem: "No space left on device" (errno 28, ENOSPC) is one message
covering at least four genuinely distinct root causes on Linux:

  1. Genuine block exhaustion  -- df -h shows Use% at/near 100%.
  2. Inode exhaustion          -- df -i shows IUse% at/near 100% while
                                   df -h still shows free bytes. Common
                                   with millions of tiny files (mail
                                   spools, session caches, node_modules
                                   trees).
  3. Deleted-but-open files    -- a process still holds a file descriptor
                                   open to a file that was unlinked (e.g.
                                   log rotation without a reload signal);
                                   `du` can't see it, so `df` and `du`
                                   disagree, sometimes by tens of GB.
  4. ext4/xfs reserved blocks  -- a filesystem can report "full" for a
                                   non-root process while a few percent
                                   is still reserved for root (tune2fs -l
                                   reserved-block-count).

Multiple 2026-dated blog posts document the exact same multi-step manual
diagnostic ritual for this (df -h, then df -i, then du -x, then lsof
+L1, then reserved-block check) because none of the individual tools
distinguishes between these causes on its own -- the operator has to run
all of them and reconcile the numbers by hand. This tool automates that
reconciliation into one command and one clear verdict per filesystem.

Strictly read-only: never deletes, truncates, or restarts anything. It
only reads df/lsof/tune2fs/xfs_info output.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from typing import Optional


CAUSE_BLOCKS_FULL = "blocks_full"
CAUSE_INODES_FULL = "inodes_full"
CAUSE_DELETED_OPEN_FILES = "deleted_open_files"
CAUSE_RESERVED_BLOCKS = "reserved_blocks_only"
CAUSE_OK = "ok"

CAUSE_EXPLANATIONS = {
    CAUSE_BLOCKS_FULL: (
        "The filesystem has genuinely run out of data blocks (bytes). "
        "Find and remove/relocate real data -- 'df -h' and 'du' will agree here."
    ),
    CAUSE_INODES_FULL: (
        "Inodes (file-count slots) are exhausted while data blocks remain "
        "free -- 'df -h' looks fine but 'df -i' is at or near 100%. This "
        "happens with millions of tiny files (caches, session files, mail "
        "queues). You must delete files (not just big ones) to free inodes; "
        "adding disk space does not help on ext2/3/4 (the inode count is "
        "fixed at format time)."
    ),
    CAUSE_DELETED_OPEN_FILES: (
        "A process is holding open a file that was already deleted (unlinked). "
        "The blocks are not released until the descriptor closes, so 'df' and "
        "'du' disagree. Restart the owning process, or truncate the file "
        "through its /proc/<pid>/fd/<fd> path (this tool does not do this for "
        "you -- it only identifies the process)."
    ),
    CAUSE_RESERVED_BLOCKS: (
        "The filesystem is not actually 100% full: a percentage of blocks is "
        "reserved for root (ext4 'tune2fs -l' reserved-block-count), so "
        "ordinary user writes fail with ENOSPC while root's writes still "
        "succeed and 'df -h' shows a few percent 'used' beyond 100% of the "
        "non-reserved capacity."
    ),
    CAUSE_OK: "This filesystem shows no signs of block or inode exhaustion.",
}


def run(cmd: list, timeout: int = 20) -> str:
    """Run a read-only subprocess command, returning stdout (empty on error)."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return result.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


@dataclass
class MountUsage:
    filesystem: str
    mountpoint: str
    block_use_pct: Optional[int] = None
    inode_use_pct: Optional[int] = None


_DF_LINE_RE = re.compile(r"^(\S+)\s+\d+\s+\d+\s+\d+\s+(\d+)%\s+(.+)$")


def parse_df_output(text: str) -> dict:
    """Parse `df -P` (POSIX format) output into filesystem -> use_pct."""
    result = {}
    for line in text.splitlines()[1:]:
        m = _DF_LINE_RE.match(line.strip())
        if m:
            fs, pct, mountpoint = m.group(1), int(m.group(2)), m.group(3)
            result[mountpoint] = (fs, pct)
    return result


def get_block_usage(runner=run) -> dict:
    """Return {mountpoint: (filesystem, use_pct)} from `df -P`."""
    return parse_df_output(runner(["df", "-P"]))


def get_inode_usage(runner=run) -> dict:
    """Return {mountpoint: (filesystem, use_pct)} from `df -Pi`."""
    return parse_df_output(runner(["df", "-Pi"]))


_LSOF_DELETED_RE = re.compile(r"\(deleted\)")


@dataclass
class DeletedOpenFile:
    command: str
    pid: str
    size_bytes: Optional[int]
    path: str


def parse_lsof_deleted(text: str) -> list:
    """Parse `lsof +L1` output for entries with a zero (or dropped) link
    count -- i.e. files that are unlinked but still held open."""
    entries = []
    lines = text.splitlines()
    if not lines:
        return entries
    header = lines[0].split()
    try:
        cmd_i = header.index("COMMAND")
        pid_i = header.index("PID")
    except ValueError:
        cmd_i, pid_i = 0, 1
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split(None, 8)
        if len(parts) < 8:
            continue
        command = parts[cmd_i] if cmd_i < len(parts) else parts[0]
        pid = parts[pid_i] if pid_i < len(parts) else parts[1]
        # SIZE/OFF is typically the 7th column in `lsof +L1` output; NAME is
        # the last column and includes "(deleted)" when unlinked-but-open.
        name = parts[-1]
        size = None
        for token in parts:
            if token.isdigit() and int(token) > 1024:
                size = int(token)
                break
        entries.append(DeletedOpenFile(command=command, pid=pid, size_bytes=size, path=name))
    return entries


def get_deleted_open_files(runner=run) -> list:
    return parse_lsof_deleted(runner(["lsof", "+L1"]))


def get_reserved_block_pct(device: str, runner=run) -> Optional[float]:
    """Best-effort: read the ext4 reserved-block percentage via tune2fs."""
    out = runner(["tune2fs", "-l", device])
    total = reserved = None
    for line in out.splitlines():
        if line.startswith("Block count:"):
            try:
                total = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("Reserved block count:"):
            try:
                reserved = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
    if total and reserved is not None and total > 0:
        return round(100.0 * reserved / total, 2)
    return None


@dataclass
class MountDiagnosis:
    mountpoint: str
    filesystem: str
    block_use_pct: Optional[int]
    inode_use_pct: Optional[int]
    cause: str
    explanation: str
    deleted_open_files: list = field(default_factory=list)
    reserved_block_pct: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "mountpoint": self.mountpoint,
            "filesystem": self.filesystem,
            "block_use_pct": self.block_use_pct,
            "inode_use_pct": self.inode_use_pct,
            "cause": self.cause,
            "explanation": self.explanation,
            "deleted_open_files": [
                {"command": f.command, "pid": f.pid, "size_bytes": f.size_bytes, "path": f.path}
                for f in self.deleted_open_files
            ],
            "reserved_block_pct": self.reserved_block_pct,
        }


def diagnose_mount(
    mountpoint: str,
    filesystem: str,
    block_use_pct: Optional[int],
    inode_use_pct: Optional[int],
    deleted_open_files: Optional[list] = None,
    reserved_block_pct: Optional[float] = None,
    near_full_threshold: int = 95,
) -> MountDiagnosis:
    """Classify a single mount's ENOSPC-relevant state.

    Priority: inode exhaustion is checked first because it produces the
    most surprising symptom (df -h looks fine); then deleted-but-open
    files (df/du disagreement); then genuine block exhaustion; then
    reserved-block false-full; else ok.
    """
    deleted_open_files = deleted_open_files or []
    mount_deleted = [f for f in deleted_open_files if f.path.startswith(mountpoint) or mountpoint == "/"]

    if inode_use_pct is not None and inode_use_pct >= near_full_threshold and (
        block_use_pct is None or block_use_pct < near_full_threshold
    ):
        cause = CAUSE_INODES_FULL
    elif mount_deleted and block_use_pct is not None and block_use_pct >= near_full_threshold:
        cause = CAUSE_DELETED_OPEN_FILES
    elif block_use_pct is not None and block_use_pct >= near_full_threshold:
        if reserved_block_pct is not None and reserved_block_pct > 0:
            cause = CAUSE_RESERVED_BLOCKS
        else:
            cause = CAUSE_BLOCKS_FULL
    else:
        cause = CAUSE_OK

    return MountDiagnosis(
        mountpoint=mountpoint,
        filesystem=filesystem,
        block_use_pct=block_use_pct,
        inode_use_pct=inode_use_pct,
        cause=cause,
        explanation=CAUSE_EXPLANATIONS[cause],
        deleted_open_files=mount_deleted,
        reserved_block_pct=reserved_block_pct,
    )


def diagnose_all(runner=run, near_full_threshold: int = 95) -> list:
    """Diagnose every mounted filesystem reported by df."""
    blocks = get_block_usage(runner=runner)
    inodes = get_inode_usage(runner=runner)
    deleted = get_deleted_open_files(runner=runner)

    reports = []
    for mountpoint, (fs, block_pct) in blocks.items():
        inode_pct = inodes.get(mountpoint, (fs, None))[1]
        reserved_pct = None
        if fs.startswith("/dev/"):
            reserved_pct = get_reserved_block_pct(fs, runner=runner)
        reports.append(
            diagnose_mount(
                mountpoint=mountpoint,
                filesystem=fs,
                block_use_pct=block_pct,
                inode_use_pct=inode_pct,
                deleted_open_files=deleted,
                reserved_block_pct=reserved_pct,
                near_full_threshold=near_full_threshold,
            )
        )
    return reports
