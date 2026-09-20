from enospc_doctor.core import (
    CAUSE_BLOCKS_FULL,
    CAUSE_DELETED_OPEN_FILES,
    CAUSE_DIAGNOSTIC_FAILED,
    CAUSE_EXPLANATIONS,
    CAUSE_INODES_FULL,
    CAUSE_OK,
    CAUSE_RESERVED_BLOCKS,
    CAUSE_RESERVED_CHECK_FAILED,
    DeletedOpenFile,
    assign_deleted_files_to_mounts,
    diagnose_all,
    diagnose_mount,
    get_block_usage,
    get_deleted_open_files,
    get_inode_usage,
    get_reserved_block_pct,
    parse_df_output,
    parse_lsof_deleted,
    run,
    run_capture,
)


DF_SAMPLE = """Filesystem     1024-blocks      Used  Available Capacity Mounted on
/dev/nvme0n1p2   104857600  63963136   35782656       65% /
tmpfs               102400        84     102316        1% /dev/shm
/dev/nvme0n1p1     523248     51248     472000       10% /boot
"""

DF_INODE_SAMPLE = """Filesystem      Inodes   IUsed   IFree IUse% Mounted on
/dev/nvme0n1p2 6553600 6553600       0  100% /
tmpfs           102400      50  102350    1% /dev/shm
/dev/nvme0n1p1  131072     300  130772    1% /boot
"""

LSOF_SAMPLE = """COMMAND     PID   USER   FD   TYPE DEVICE  SIZE/OFF   NODE NAME
java      21874   app   47w   REG  253,0 98765432101 445566 /var/log/app/access.log (deleted)
nginx      1234  root    6w   REG  253,0    204800 112233 /var/log/nginx/error.log (deleted)
"""


def test_parse_df_output():
    result = parse_df_output(DF_SAMPLE)
    assert result["/"] == ("/dev/nvme0n1p2", 65)
    assert result["/dev/shm"] == ("tmpfs", 1)
    assert result["/boot"] == ("/dev/nvme0n1p1", 10)


def test_get_block_usage_uses_runner():
    def fake_runner(cmd, timeout=20):
        assert cmd[0] == "df"
        return DF_SAMPLE

    result = get_block_usage(runner=fake_runner)
    assert result["/"][1] == 65


def test_get_inode_usage_uses_runner():
    def fake_runner(cmd, timeout=20):
        return DF_INODE_SAMPLE

    result = get_inode_usage(runner=fake_runner)
    assert result["/"][1] == 100


def test_parse_lsof_deleted():
    entries = parse_lsof_deleted(LSOF_SAMPLE)
    assert len(entries) == 2
    assert entries[0].command == "java"
    assert entries[0].pid == "21874"


def test_parse_lsof_deleted_reports_real_size_not_pid():
    # Regression test: size_bytes must come from the SIZE/OFF column, not
    # from "first digit token > 1024" -- which previously matched PID
    # (21874, 1234) before ever reaching the real SIZE/OFF value
    # (98765432101, 204800), silently reporting the wrong number as the
    # amount of space a deleted-but-open file is pinning.
    entries = parse_lsof_deleted(LSOF_SAMPLE)
    assert entries[0].size_bytes == 98765432101
    assert entries[0].size_bytes != int(entries[0].pid)
    assert entries[1].size_bytes == 204800
    assert entries[1].size_bytes != int(entries[1].pid)
    assert "(deleted)" in entries[0].path


def test_get_deleted_open_files_uses_runner():
    def fake_runner(cmd, timeout=20):
        assert cmd[0] == "lsof"
        return LSOF_SAMPLE

    entries = get_deleted_open_files(runner=fake_runner)
    assert len(entries) == 2


def test_get_reserved_block_pct_parses_tune2fs():
    sample = "Block count:              104857600\nReserved block count:     5242880\n"

    def fake_runner(cmd, timeout=20):
        return sample, "", 0

    pct, check_failed = get_reserved_block_pct("/dev/nvme0n1p2", runner=fake_runner)
    assert pct == 5.0
    assert check_failed is False


def test_get_reserved_block_pct_handles_missing_data():
    def fake_runner(cmd, timeout=20):
        return "", "", 0

    pct, check_failed = get_reserved_block_pct("/dev/sda1", runner=fake_runner)
    assert pct is None
    assert check_failed is False


def test_get_reserved_block_pct_reports_permission_denied_as_check_failed():
    # Regression: before this fix, get_reserved_block_pct() used the
    # stdout-only run() helper, so a permission-denied `tune2fs -l`
    # invocation (common for a non-root process reading a device node)
    # silently returned "" -- indistinguishable from "tune2fs ran fine
    # and reported zero reserved blocks". diagnose_mount() then treated
    # that as a confirmed CAUSE_BLOCKS_FULL (genuine exhaustion) verdict
    # for a mount whose reserved-block layer was never actually checked.
    def fake_runner(cmd, timeout=20):
        return "", "tune2fs: Permission denied to open /dev/nvme0n1p2\n", 1

    pct, check_failed = get_reserved_block_pct("/dev/nvme0n1p2", runner=fake_runner)
    assert pct is None
    assert check_failed is True


def test_get_reserved_block_pct_reports_missing_binary_as_check_failed():
    # Regression: run_capture() signals "could not execute the command at
    # all" (tune2fs/e2fsprogs not installed -- plausible on minimal or
    # container-focused Linux distros) via returncode == -1, with the
    # OSError text in stderr rather than a "permission denied"-style
    # message. Before this fix, get_reserved_block_pct() only checked
    # _is_permission_denied(err, rc), which requires a "permission
    # denied"/"must be root" phrase in stderr -- a plain "No such file or
    # directory" OSError text does not match, so this fell through to
    # (None, False), identical to "tune2fs ran fine, zero reserved
    # blocks". diagnose_mount() then reported a false CAUSE_BLOCKS_FULL
    # (genuine exhaustion) verdict for a near-full ext4 mount whose
    # reserved-block layer was never actually checked, on any host
    # missing the tune2fs binary.
    def fake_runner(cmd, timeout=20):
        return "", "[Errno 2] No such file or directory: 'tune2fs'", -1

    pct, check_failed = get_reserved_block_pct("/dev/nvme0n1p2", runner=fake_runner)
    assert pct is None
    assert check_failed is True


def test_diagnose_mount_inode_exhaustion():
    report = diagnose_mount(
        mountpoint="/", filesystem="/dev/nvme0n1p2",
        block_use_pct=60, inode_use_pct=100,
    )
    assert report.cause == CAUSE_INODES_FULL


def test_diagnose_mount_ok_when_both_low():
    report = diagnose_mount(
        mountpoint="/", filesystem="/dev/nvme0n1p2",
        block_use_pct=40, inode_use_pct=10,
    )
    assert report.cause == CAUSE_OK


def test_diagnose_mount_deleted_open_files():
    deleted = [DeletedOpenFile(command="java", pid="1234", size_bytes=999999999, path="/var/log/app.log (deleted)")]
    report = diagnose_mount(
        mountpoint="/", filesystem="/dev/nvme0n1p2",
        block_use_pct=99, inode_use_pct=10,
        deleted_open_files=deleted,
    )
    assert report.cause == CAUSE_DELETED_OPEN_FILES
    assert len(report.deleted_open_files) == 1


def test_diagnose_mount_reserved_blocks():
    report = diagnose_mount(
        mountpoint="/", filesystem="/dev/nvme0n1p2",
        block_use_pct=99, inode_use_pct=10,
        reserved_block_pct=5.0,
    )
    assert report.cause == CAUSE_RESERVED_BLOCKS


def test_diagnose_mount_reserved_check_failed_is_not_false_blocks_full():
    # Regression: when the reserved-block check itself failed (privilege
    # error reading tune2fs), diagnose_mount() must report the honest
    # CAUSE_RESERVED_CHECK_FAILED, never collapse to CAUSE_BLOCKS_FULL as
    # if the mount were confirmed genuinely exhausted.
    report = diagnose_mount(
        mountpoint="/", filesystem="/dev/nvme0n1p2",
        block_use_pct=99, inode_use_pct=10,
        reserved_block_pct=None,
        reserved_block_check_failed=True,
    )
    assert report.cause == CAUSE_RESERVED_CHECK_FAILED
    assert report.cause != CAUSE_BLOCKS_FULL
    assert report.explanation == CAUSE_EXPLANATIONS[CAUSE_RESERVED_CHECK_FAILED]


def test_diagnose_mount_plain_blocks_full():
    report = diagnose_mount(
        mountpoint="/", filesystem="/dev/nvme0n1p2",
        block_use_pct=99, inode_use_pct=10,
        reserved_block_pct=None,
    )
    assert report.cause == CAUSE_BLOCKS_FULL


def test_diagnose_mount_respects_custom_threshold():
    report = diagnose_mount(
        mountpoint="/", filesystem="/dev/nvme0n1p2",
        block_use_pct=85, inode_use_pct=10,
        near_full_threshold=80,
    )
    assert report.cause == CAUSE_BLOCKS_FULL


def test_diagnose_mount_to_dict_roundtrip():
    report = diagnose_mount(
        mountpoint="/", filesystem="/dev/nvme0n1p2",
        block_use_pct=99, inode_use_pct=10,
    )
    d = report.to_dict()
    assert d["mountpoint"] == "/"
    assert d["cause"] == CAUSE_BLOCKS_FULL
    assert isinstance(d["deleted_open_files"], list)


def test_diagnose_all_reports_diagnostic_failed_when_df_returns_nothing():
    # Regression: before this fix, diagnose_all() returned an empty list
    # when `df -P` produced no parseable lines at all (missing binary,
    # permission error, unexpected format) -- indistinguishable from
    # "every mount checked, none has a problem", so main()'s `any(...)`
    # exit-code check silently reported success/OK on a real diagnostic
    # failure instead of surfacing it. It must instead return exactly one
    # CAUSE_DIAGNOSTIC_FAILED report.
    def empty_runner(cmd, timeout=20):
        return ""

    reports = diagnose_all(runner=empty_runner)
    assert len(reports) == 1
    assert reports[0].cause == CAUSE_DIAGNOSTIC_FAILED
    assert reports[0].explanation == CAUSE_EXPLANATIONS[CAUSE_DIAGNOSTIC_FAILED]


def test_diagnose_all_integrates(monkeypatch):
    def fake_runner(cmd, timeout=20):
        if cmd[0] == "df" and "-Pi" in cmd:
            return DF_INODE_SAMPLE
        if cmd[0] == "df":
            return DF_SAMPLE
        if cmd[0] == "lsof":
            return LSOF_SAMPLE
        return ""

    def fake_capture_runner(cmd, timeout=20):
        if cmd[0] == "tune2fs":
            return "Block count:              104857600\nReserved block count:     0\n", "", 0
        return "", "", 0

    reports = diagnose_all(runner=fake_runner, capture_runner=fake_capture_runner)
    by_mount = {r.mountpoint: r for r in reports}
    assert by_mount["/"].cause == CAUSE_INODES_FULL
    assert by_mount["/dev/shm"].cause == CAUSE_OK


def test_run_returns_real_stdout_on_success():
    # The success path (subprocess.run completing normally and returning
    # its stdout) had zero direct coverage -- run() was only ever
    # exercised via error paths (missing binary, timeout) or via a fake
    # runner in other tests, never via a real subprocess actually
    # succeeding and this function returning its output.
    assert run(["echo", "enospc-doctor-marker"]) == "enospc-doctor-marker\n"


def test_run_swallows_missing_binary_oserror():
    # A nonexistent command raises OSError (FileNotFoundError) inside
    # subprocess.run; run() must degrade to "" rather than propagate.
    assert run(["/no/such/enospc-doctor-binary-xyz", "-v"]) == ""


def test_run_swallows_timeout(monkeypatch):
    import subprocess as sp

    def fake_run(cmd, capture_output, text, timeout, check):
        raise sp.TimeoutExpired(cmd=cmd, timeout=timeout)

    monkeypatch.setattr(sp, "run", fake_run)
    assert run(["df"], timeout=1) == ""


def test_diagnose_all_reports_reserved_check_failed_not_false_blocks_full():
    # Regression at the diagnose_all() integration level: a mount at 99%
    # block usage whose tune2fs read fails with permission-denied must be
    # reported as CAUSE_RESERVED_CHECK_FAILED, never as a false confirmed
    # CAUSE_BLOCKS_FULL.
    df_full_mount = (
        "Filesystem     1024-blocks      Used  Available Capacity Mounted on\n"
        "/dev/nvme0n1p2   104857600  103808000    1049600       99% /\n"
    )
    df_inodes_full_mount = (
        "Filesystem      Inodes   IUsed   IFree IUse% Mounted on\n"
        "/dev/nvme0n1p2 6553600   65536 6488064    1% /\n"
    )

    def fake_runner(cmd, timeout=20):
        if cmd[0] == "df" and "-Pi" in cmd:
            return df_inodes_full_mount
        if cmd[0] == "df":
            return df_full_mount
        if cmd[0] == "lsof":
            return ""
        return ""

    def fake_capture_runner(cmd, timeout=20):
        if cmd[0] == "tune2fs":
            return "", "tune2fs: Permission denied to open /dev/nvme0n1p2\n", 1
        return "", "", 0

    reports = diagnose_all(runner=fake_runner, capture_runner=fake_capture_runner)
    assert len(reports) == 1
    assert reports[0].cause == CAUSE_RESERVED_CHECK_FAILED
    assert reports[0].cause != CAUSE_BLOCKS_FULL


def test_run_capture_returns_stdout_stderr_returncode_on_success():
    stdout, stderr, rc = run_capture(["echo", "enospc-doctor-marker"])
    assert stdout == "enospc-doctor-marker\n"
    assert rc == 0


def test_run_capture_swallows_missing_binary_oserror():
    stdout, stderr, rc = run_capture(["/no/such/enospc-doctor-binary-xyz"])
    assert stdout == ""
    assert rc == -1


def test_parse_lsof_deleted_empty_text_returns_empty_list():
    assert parse_lsof_deleted("") == []


def test_get_reserved_block_pct_swallows_unparseable_counts():
    # Both "Block count:" and "Reserved block count:" lines present but
    # with non-integer values -- must not raise, and total/reserved stay
    # unset so the function returns None instead of crashing.
    out = "Block count:              not-a-number\nReserved block count:     also-bad\n"
    pct, check_failed = get_reserved_block_pct("/dev/nvme0n1p2", runner=lambda cmd, timeout=20: (out, "", 0))
    assert pct is None
    assert check_failed is False


def test_parse_lsof_deleted_falls_back_to_positional_columns_without_header_names():
    # Regression: when lsof's header lacks the literal "COMMAND"/"PID"
    # tokens (e.g. a locale/lsof-version variant with different column
    # labels), parse_lsof_deleted must still extract command/pid instead
    # of raising ValueError -- it falls back to the first two positional
    # columns. This except-ValueError branch (header.index raising) had
    # zero direct test coverage before this test.
    text = (
        "PROC       ID    USR   FD   TYPE DEVICE  SIZE/OFF   NODE NAME\n"
        "java    21874    app  47w   REG  253,0 98765432101 445566 /var/log/app/access.log (deleted)\n"
    )
    entries = parse_lsof_deleted(text)
    assert len(entries) == 1
    assert entries[0].command == "java"
    assert entries[0].pid == "21874"
    # SIZE/OFF header token is still present here, so size is still parsed.
    assert entries[0].size_bytes == 98765432101


def test_parse_lsof_deleted_size_none_when_size_off_column_missing():
    # Regression: when the header has no "SIZE/OFF" token at all, size_i
    # stays None and every entry's size_bytes must be None rather than
    # guessing at a column -- this except-ValueError branch (size_i
    # lookup failing) had zero direct test coverage before this test.
    text = (
        "COMMAND     PID   USER   FD   TYPE DEVICE   BYTES   NODE NAME\n"
        "java      21874   app   47w   REG  253,0    98765  445566 /var/log/app/access.log (deleted)\n"
    )
    entries = parse_lsof_deleted(text)
    assert len(entries) == 1
    assert entries[0].size_bytes is None


def test_parse_lsof_deleted_skips_blank_and_short_lines():
    # Regression: parse_lsof_deleted must tolerate a blank line between
    # entries (continue at line 161) and a line with fewer than 8
    # whitespace-split tokens, e.g. a truncated/corrupted lsof row
    # (continue at line 164), skipping both without raising or
    # fabricating an entry.
    text = (
        "COMMAND     PID   USER   FD   TYPE DEVICE  SIZE/OFF   NODE NAME\n"
        "\n"
        "short line\n"
        "java      21874   app   47w   REG  253,0 98765432101 445566 /var/log/app/access.log (deleted)\n"
    )
    entries = parse_lsof_deleted(text)
    assert len(entries) == 1
    assert entries[0].pid == "21874"


def test_assign_deleted_files_longest_prefix_wins():
    # Regression: a naive `path.startswith(mountpoint)` check matches a
    # file under /var/log against BOTH "/" and "/var" (every absolute
    # path starts with "/"), double-attributing evidence. The most
    # specific (longest) matching mountpoint must win instead.
    files = [
        DeletedOpenFile(command="nginx", pid="1", size_bytes=100, path="/var/log/app.log (deleted)"),
        DeletedOpenFile(command="root-proc", pid="2", size_bytes=200, path="/tmp/scratch (deleted)"),
    ]
    by_mount = assign_deleted_files_to_mounts(files, ["/", "/var"])
    assert by_mount["/var"] == [files[0]]
    assert by_mount["/"] == [files[1]]


def test_diagnose_all_does_not_leak_deleted_files_across_mounts(monkeypatch):
    # Regression for the same bug at the diagnose_all() integration level:
    # a deleted file under /var must not cause "/" to be misdiagnosed as
    # CAUSE_DELETED_OPEN_FILES when "/" itself has no deleted files and
    # is otherwise at CAUSE_OK.
    df_two_mounts = (
        "Filesystem     1024-blocks      Used  Available Capacity Mounted on\n"
        "/dev/nvme0n1p2   104857600  41943040   62914560       40% /\n"
        "/dev/nvme0n1p3    52428800  51380224     524288       99% /var\n"
    )
    df_inodes_two_mounts = (
        "Filesystem      Inodes   IUsed   IFree IUse% Mounted on\n"
        "/dev/nvme0n1p2 6553600   65536 6488064    1% /\n"
        "/dev/nvme0n1p3 3276800   32768 3244032    1% /var\n"
    )
    lsof_under_var = (
        "COMMAND     PID   USER   FD   TYPE DEVICE  SIZE/OFF   NODE NAME\n"
        "nginx      1234  root    6w   REG  253,0    204800 112233 /var/log/nginx/error.log (deleted)\n"
    )

    def fake_runner(cmd, timeout=20):
        if cmd[0] == "df" and "-Pi" in cmd:
            return df_inodes_two_mounts
        if cmd[0] == "df":
            return df_two_mounts
        if cmd[0] == "lsof":
            return lsof_under_var
        return ""

    def fake_capture_runner(cmd, timeout=20):
        if cmd[0] == "tune2fs":
            return "Block count:              104857600\nReserved block count:     0\n", "", 0
        return "", "", 0

    reports = diagnose_all(runner=fake_runner, capture_runner=fake_capture_runner)
    by_mount = {r.mountpoint: r for r in reports}
    # Before the fix, "/" would also pick up the /var/log deleted file
    # (via bare startswith("/")) and get misdiagnosed/annotated with it.
    assert by_mount["/"].cause == CAUSE_OK
    assert by_mount["/"].deleted_open_files == []
    # /var is genuinely full (99%) AND has the deleted file -- correctly
    # attributed to it, not to "/".
    assert by_mount["/var"].cause == CAUSE_DELETED_OPEN_FILES
    assert len(by_mount["/var"].deleted_open_files) == 1
