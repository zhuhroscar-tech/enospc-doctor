[![English](https://img.shields.io/badge/English-555555?style=flat)](README.md) [![简体中文](https://img.shields.io/badge/简体中文-555555?style=flat)](README.zh-CN.md)

# enospc-doctor

A read-only Linux CLI that helps distinguish common causes of `No space left on device` (`ENOSPC`): exhausted data blocks, exhausted inodes, deleted files still held open by a process, and space reserved for root on ext filesystems. It combines filesystem and process information into a report per mount instead of asking you to reconcile several commands manually.

![Example report](docs/images/example-output.png)

## Install

Requires Linux and Python 3.9+. It reads `df` and `lsof` output; the reserved-block check uses `tune2fs` for ext2/3/4 filesystems. Missing tools or insufficient permissions can limit diagnosis.

```bash
git clone https://github.com/zhuhroscar-tech/enospc-doctor.git
cd enospc-doctor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
```

A standalone `.pyz` is also available from [GitHub Releases](https://github.com/zhuhroscar-tech/enospc-doctor/releases). Check it against the release's `SHA256SUMS.txt` before running it with Python.

## Usage

```bash
enospc-doctor                 # mounts with findings
enospc-doctor --all           # include healthy mounts
enospc-doctor --json          # structured reports
enospc-doctor --threshold 90  # near-full threshold; default 95
```

Exit **0** means no issue was reported; **2** means a finding or diagnostic failure was reported. Review the cause and explanation, not just the exit code. An unavailable reserved-block check is not proof that all used space is ordinary file data.

## Interpreting findings

- `blocks_full`: investigate large files and data growth.
- `inodes_full`: investigate large numbers of small files, not just byte usage.
- `deleted_open_files`: inspect the named process before deciding whether to close files or restart it.
- `reserved_blocks_only`: review the filesystem reservation and underlying growth before changing either.

The tool does not delete or truncate files, restart services, or change filesystem settings. It makes no network requests and keeps no persistent configuration. Seeing other users' open files or reading device metadata may require elevated permissions. A clean report is limited to the information available to these checks; it is not an exhaustive filesystem-health assessment. Review any cleanup separately, especially on production systems.

## Development

```bash
python -m pip install -e '.[dev]'
python -m pytest -v
```

[Demo video](docs/demo.mp4) · [Release history](CHANGELOG.md) · [MIT license](LICENSE).
