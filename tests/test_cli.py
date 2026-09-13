import json

import pytest

from enospc_doctor.cli import main
from enospc_doctor.core import (
    DeletedOpenFile,
    MountDiagnosis,
    CAUSE_DIAGNOSTIC_FAILED,
    CAUSE_INODES_FULL,
    CAUSE_OK,
    CAUSE_RESERVED_BLOCKS,
    CAUSE_RESERVED_CHECK_FAILED,
)


def _fake_report(cause=CAUSE_INODES_FULL, mountpoint="/"):
    return MountDiagnosis(
        mountpoint=mountpoint, filesystem="/dev/nvme0n1p2",
        block_use_pct=60, inode_use_pct=100,
        cause=cause, explanation="example explanation",
    )


def test_version(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    assert "enospc-doctor" in capsys.readouterr().out


def test_text_output_shows_issue(monkeypatch, capsys):
    monkeypatch.setattr("enospc_doctor.cli.diagnose_all", lambda near_full_threshold: [_fake_report()])
    rc = main([])
    out = capsys.readouterr().out
    assert "inodes_full" in out
    assert rc == 2


def test_json_output(monkeypatch, capsys):
    monkeypatch.setattr("enospc_doctor.cli.diagnose_all", lambda near_full_threshold: [_fake_report()])
    rc = main(["--json"])
    parsed = json.loads(capsys.readouterr().out)
    assert len(parsed) == 1
    assert parsed[0]["cause"] == CAUSE_INODES_FULL
    assert rc == 2


def test_no_issues_returns_zero(monkeypatch, capsys):
    monkeypatch.setattr(
        "enospc_doctor.cli.diagnose_all",
        lambda near_full_threshold: [_fake_report(cause=CAUSE_OK)],
    )
    rc = main([])
    out = capsys.readouterr().out
    assert "No ENOSPC-relevant issues" in out
    assert rc == 0


def test_all_flag_shows_ok_mounts(monkeypatch, capsys):
    monkeypatch.setattr(
        "enospc_doctor.cli.diagnose_all",
        lambda near_full_threshold: [_fake_report(cause=CAUSE_OK, mountpoint="/boot")],
    )
    rc = main(["--all"])
    out = capsys.readouterr().out
    assert "/boot" in out
    assert rc == 0


def test_text_output_shows_reserved_block_pct_row(monkeypatch, capsys):
    report = MountDiagnosis(
        mountpoint="/", filesystem="/dev/nvme0n1p2",
        block_use_pct=97, inode_use_pct=10,
        cause=CAUSE_RESERVED_BLOCKS, explanation="reserved for root",
        reserved_block_pct=5.0,
    )
    monkeypatch.setattr("enospc_doctor.cli.diagnose_all", lambda near_full_threshold: [report])
    rc = main([])
    out = capsys.readouterr().out
    assert "reserved blocks" in out
    assert "5.0%" in out
    assert rc == 2


def test_text_output_lists_deleted_open_files(monkeypatch, capsys):
    report = MountDiagnosis(
        mountpoint="/", filesystem="/dev/nvme0n1p2",
        block_use_pct=99, inode_use_pct=10,
        cause=CAUSE_INODES_FULL, explanation="deleted files pinning space",
        deleted_open_files=[
            DeletedOpenFile(pid="1234", command="nginx", path="/var/log/nginx/access.log", size_bytes=52428800),
            DeletedOpenFile(pid="5678", command="python3", path="/tmp/data.tmp", size_bytes=None),
        ],
    )
    monkeypatch.setattr("enospc_doctor.cli.diagnose_all", lambda near_full_threshold: [report])
    rc = main([])
    out = capsys.readouterr().out
    assert "deleted-but-open files pinning space" in out
    assert "pid 1234 (nginx)" in out
    assert "52428800 bytes" in out
    assert "pid 5678 (python3)" in out
    assert "size unknown" in out
    assert rc == 2


def test_text_output_diagnostic_failed_skips_metric_rows(monkeypatch, capsys):
    # Regression: when df/lsof itself couldn't be run (diagnostic_failed),
    # _print_text must print the headline+explanation and then skip the
    # block/inode metric rows entirely (they're meaningless without real
    # data) rather than printing "None%" or raising on missing fields.
    # This continue-on-diagnostic_failed branch had zero test coverage.
    report = MountDiagnosis(
        mountpoint="/", filesystem="unknown",
        block_use_pct=None, inode_use_pct=None,
        cause=CAUSE_DIAGNOSTIC_FAILED,
        explanation="'df -P' produced no usable output.",
    )
    monkeypatch.setattr("enospc_doctor.cli.diagnose_all", lambda near_full_threshold: [report])
    rc = main([])
    out = capsys.readouterr().out
    assert "diagnostic_failed" in out
    assert "block use" not in out
    assert "inode use" not in out
    assert rc == 2


def test_text_output_reserved_check_failed_shown_as_warn_not_fail(monkeypatch, capsys):
    # Regression: CAUSE_RESERVED_CHECK_FAILED (an honest "could not
    # determine" verdict, not a confirmed problem) must render with the
    # "warn" status level (yellow dot / [!] in no-color mode), never the
    # "fail" level used for confirmed CAUSE_BLOCKS_FULL/etc.
    report = MountDiagnosis(
        mountpoint="/", filesystem="/dev/nvme0n1p2",
        block_use_pct=99, inode_use_pct=10,
        cause=CAUSE_RESERVED_CHECK_FAILED,
        explanation="could not determine reserved blocks",
    )
    monkeypatch.setattr("enospc_doctor.cli.diagnose_all", lambda near_full_threshold: [report])
    rc = main(["--no-color"])
    out = capsys.readouterr().out
    assert "[!]" in out
    assert "[X]" not in out
    assert rc == 2
