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
  4. ext2/3/4 reserved blocks -- a filesystem can report "full" for a
                                   non-root process while a few percent
                                   is still reserved for root (tune2fs -l
                                   reserved-block-count). This check is
                                   ext-family only: XFS has no comparable
                                   admin-tunable "reserved for root"
                                   percentage (its small internal
                                   privileged-transaction reservation is
                                   fixed, not a root-vs-everyone-else
                                   carve-out), so this tool does not
                                   claim to detect it on XFS -- a near-
                                   full XFS mount is reported as genuine
                                   block exhaustion (CAUSE_BLOCKS_FULL)
                                   rather than a false "reserved" verdict.

Multiple 2026-dated blog posts document the exact same multi-step manual
diagnostic ritual for this (df -h, then df -i, then du -x, then lsof
+L1, then reserved-block check) because none of the individual tools
distinguishes between these causes on its own -- the operator has to run
all of them and reconcile the numbers by hand. This tool automates that
reconciliation into one command and one clear verdict per filesystem.

Strictly read-only: never deletes, truncates, or restarts anything. It
only reads df/lsof/tune2fs output (no xfs_info call exists -- XFS
reserved-space detection is intentionally not implemented, see above).
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
CAUSE_RESERVED_CHECK_FAILED = "reserved_block_check_could_not_be_determined"
CAUSE_DIAGNOSTIC_FAILED = "diagnostic_failed"

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
    CAUSE_RESERVED_CHECK_FAILED: (
        "The filesystem's block usage is at or near 100%, but whether some "
        "of that is actually reserved (ext4 'tune2fs -l' reserved-block-count, "
        "not a genuine exhaustion) could not be determined -- 'tune2fs' failed "
        "for a privilege reason (permission denied reading the device node, or "
        "requires root). This is NOT a confirmed 'genuinely out of space' "
        "verdict: re-run with sudo/root to get a real reserved-blocks answer "
        "instead of an assumed genuine-full."
    ),
    CAUSE_DIAGNOSTIC_FAILED: (
        "'df' returned no parseable mount data at all (empty output, unexpected "
        "format, or the binary is missing/unreadable in this environment). This "
        "is NOT the same as 'no issues found' -- no filesystem was actually "
        "checked, so a real ENOSPC condition could be silently missed. Verify "
        "'df -P' works in this environment and re-run."
    ),
}


def run(cmd: list, timeout: int = 20) -> str:
    """Run a read-only subprocess command, returning stdout (empty on error)."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return result.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


_PERMISSION_DENIED_RE = re.compile(
    r"permission denied|must be superuser|must be root|requires? (?:root|superuser)|not permitted",
    re.IGNORECASE,
)


def run_capture(cmd: list, timeout: int = 20):
    """Run a read-only subprocess command, returning (stdout, stderr, returncode).

    Unlike run(), this preserves stderr/exit status so callers can tell a
    genuinely empty/negative result apart from a command that failed because
    it needs elevated privileges (e.g. `tune2fs -l` on a device node this
    process can't read)."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return result.stdout or "", result.stderr or "", result.returncode
    except (OSError, subprocess.SubprocessError) as exc:
        return "", str(exc), -1


def _is_permission_denied(stderr: str, returncode: int) -> bool:
    """True if a command's failure looks like a privilege/permission problem
    rather than a genuine 'no such thing exists' answer."""
    if returncode == 0:
        return False
    return bool(_PERMISSION_DENIED_RE.search(stderr))


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
    # SIZE/OFF's column index in the header tells us which split token is
    # the real size, instead of guessing by "first digit token > 1024" --
    # that heuristic previously matched PID (also a large digit string)
    # before ever reaching the real SIZE/OFF column, silently reporting the
    # PID as the file size for every entry.
    try:
        size_i = header.index("SIZE/OFF")
    except ValueError:
        size_i = None
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split(None, 8)
        if len(parts) < 8:
            continue
        command = parts[cmd_i] if cmd_i < len(parts) else parts[0]
        pid = parts[pid_i] if pid_i < len(parts) else parts[1]
        # NAME is the last column and includes "(deleted)" when
        # unlinked-but-open.
        name = parts[-1]
        size = None
        if size_i is not None and size_i < len(parts) and parts[size_i].isdigit():
            size = int(parts[size_i])
        entries.append(DeletedOpenFile(command=command, pid=pid, size_bytes=size, path=name))
    return entries


def get_deleted_open_files(runner=run) -> list:
    return parse_lsof_deleted(runner(["lsof", "+L1"]))


def get_reserved_block_pct(device: str, runner=run_capture) -> tuple:
    """Best-effort: read the ext4 reserved-block percentage via tune2fs.

    Returns (pct_or_None, check_failed). check_failed is True only when
    tune2fs itself failed for a privilege reason (permission denied on the
    device node, or "requires root") -- distinct from tune2fs succeeding
    but simply reporting a device with no reserved blocks (returns
    (None, False) in that case, same as before this fix). Without this
    distinction, a permission-denied tune2fs read silently produced the
    same (None) result as "there genuinely are no reserved blocks",
    causing diagnose_mount() to report a false CAUSE_BLOCKS_FULL (genuine
    exhaustion) verdict on a mount that was never actually checked for
    reserved space.
    """
    out, err, rc = runner(["tune2fs", "-l", device])
    if _is_permission_denied(err, rc):
        return None, True
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
        return round(100.0 * reserved / total, 2), False
    return None, False


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


def assign_deleted_files_to_mounts(deleted_open_files: list, mountpoints: list) -> dict:
    """Attribute each deleted-but-open file to exactly one mountpoint: the
    longest matching path prefix among the given mountpoints (standard
    "most specific mount wins" rule, same as how the kernel itself
    resolves which mount a path belongs to).

    Without this, a naive `path.startswith(mountpoint)` check matches a
    file under e.g. /var/log against BOTH "/" and "/var" (every absolute
    path starts with "/"), so the same evidence gets double-attributed to
    unrelated mounts and can misdiagnose one mount using another mount's
    open-deleted-file evidence.
    """
    by_mount: dict = {mp: [] for mp in mountpoints}
    # Longest mountpoint first so the most specific match wins.
    sorted_mounts = sorted(mountpoints, key=len, reverse=True)
    for f in deleted_open_files:
        for mp in sorted_mounts:
            if mp == "/" or f.path.startswith(mp.rstrip("/") + "/") or f.path == mp:
                by_mount[mp].append(f)
                break
    return by_mount


def diagnose_mount(
    mountpoint: str,
    filesystem: str,
    block_use_pct: Optional[int],
    inode_use_pct: Optional[int],
    deleted_open_files: Optional[list] = None,
    reserved_block_pct: Optional[float] = None,
    reserved_block_check_failed: bool = False,
    near_full_threshold: int = 95,
) -> MountDiagnosis:
    """Classify a single mount's ENOSPC-relevant state.

    Priority: inode exhaustion is checked first because it produces the
    most surprising symptom (df -h looks fine); then deleted-but-open
    files (df/du disagreement); then a reserved-block check that itself
    failed for a privilege reason (must not be silently treated as
    "genuinely full" -- that would be a false-positive block-exhaustion
    verdict on a mount whose reserved-block layer was never actually
    checked); then genuine block exhaustion; then reserved-block
    false-full; else ok.

    `deleted_open_files` here is expected to already be scoped to this
    mount (see `assign_deleted_files_to_mounts`); callers that pass an
    unscoped, fleet-wide list (as older versions of this function did)
    will over-attribute evidence to every mount that is "/" or whose
    path happens to be a string prefix -- use `diagnose_all`, which does
    the scoping correctly, rather than calling this directly with a raw
    lsof list for anything but a single-mount system.
    """
    mount_deleted = deleted_open_files or []

    if inode_use_pct is not None and inode_use_pct >= near_full_threshold and (
        block_use_pct is None or block_use_pct < near_full_threshold
    ):
        cause = CAUSE_INODES_FULL
    elif mount_deleted and block_use_pct is not None and block_use_pct >= near_full_threshold:
        cause = CAUSE_DELETED_OPEN_FILES
    elif (
        block_use_pct is not None
        and block_use_pct >= near_full_threshold
        and reserved_block_check_failed
    ):
        cause = CAUSE_RESERVED_CHECK_FAILED
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


def diagnose_all(runner=run, capture_runner=run_capture, near_full_threshold: int = 95) -> list:
    """Diagnose every mounted filesystem reported by df.

    If `df -P` itself returns no parseable mount lines (missing binary,
    permission failure, unexpected output format, etc.), this returns a
    single CAUSE_DIAGNOSTIC_FAILED report rather than an empty list --
    an empty list is otherwise indistinguishable from "every mount was
    checked and none has a problem", which would let a real ENOSPC
    condition go completely unreported.
    """
    blocks = get_block_usage(runner=runner)
    if not blocks:
        return [
            MountDiagnosis(
                mountpoint="(unknown)",
                filesystem="(unknown)",
                block_use_pct=None,
                inode_use_pct=None,
                cause=CAUSE_DIAGNOSTIC_FAILED,
                explanation=CAUSE_EXPLANATIONS[CAUSE_DIAGNOSTIC_FAILED],
            )
        ]
    inodes = get_inode_usage(runner=runner)
    deleted = get_deleted_open_files(runner=runner)
    deleted_by_mount = assign_deleted_files_to_mounts(deleted, list(blocks.keys()))

    reports = []
    for mountpoint, (fs, block_pct) in blocks.items():
        inode_pct = inodes.get(mountpoint, (fs, None))[1]
        reserved_pct = None
        reserved_check_failed = False
        if fs.startswith("/dev/"):
            reserved_pct, reserved_check_failed = get_reserved_block_pct(fs, runner=capture_runner)
        reports.append(
            diagnose_mount(
                mountpoint=mountpoint,
                filesystem=fs,
                block_use_pct=block_pct,
                inode_use_pct=inode_pct,
                deleted_open_files=deleted_by_mount.get(mountpoint, []),
                reserved_block_pct=reserved_pct,
                reserved_block_check_failed=reserved_check_failed,
                near_full_threshold=near_full_threshold,
            )
        )
    return reports
