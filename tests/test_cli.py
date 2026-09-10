import json

import pytest

from enospc_doctor.cli import main
from enospc_doctor.core import MountDiagnosis, CAUSE_INODES_FULL, CAUSE_OK


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
