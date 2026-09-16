[![English](https://img.shields.io/badge/English-555555?style=flat)](README.md) [![简体中文](https://img.shields.io/badge/简体中文-555555?style=flat)](README.zh-CN.md)

# enospc-doctor

只读的 Linux 诊断 CLI，用于区分 `No space left on device`（`ENOSPC`）的几种常见原因：数据块耗尽、inode 耗尽、已删除的文件仍被进程打开，以及 ext 文件系统为 root 预留的空间。它将文件系统和进程信息汇总为每个挂载点的报告，减少手工对照多条命令的工作。

![报告示例](docs/images/example-output.png)

## 安装

需要 Linux 和 Python 3.9+。工具读取 `df`、`lsof` 的输出，并通过 `tune2fs` 检查 ext2/3/4 的预留块。缺少命令或权限不足会影响诊断范围。

```bash
git clone https://github.com/zhuhroscar-tech/enospc-doctor.git
cd enospc-doctor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
```

[GitHub Releases](https://github.com/zhuhroscar-tech/enospc-doctor/releases) 也提供独立的 `.pyz`。请先核对同一 release 中的 `SHA256SUMS.txt`，再用 Python 运行。

## 使用

```bash
enospc-doctor                 # 只显示有诊断结果的挂载点
enospc-doctor --all           # 包含正常的挂载点
enospc-doctor --json          # 输出结构化报告
enospc-doctor --threshold 90  # 接近满载的阈值，默认为 95
```

退出码 **0** 表示未报告问题；**2** 表示发现问题或诊断失败。请结合 cause 和说明判断，不要只看退出码。无法检查预留块，并不意味着所有已用空间都是普通文件占用。

## 如何理解结果

- `blocks_full`：排查大文件和持续增长的数据。
- `inodes_full`：排查大量小文件，不能只看字节数。
- `deleted_open_files`：先确认报告中的进程，再决定关闭文件或重启服务。
- `reserved_blocks_only`：先了解预留空间的用途和数据增长原因，再考虑调整。

工具不会删除或截断文件、重启服务，也不会更改文件系统设置；不发送网络请求，不保存持久化配置。查看其他用户的文件句柄或读取设备元数据可能需要更高权限。正常结果仅代表现有信息下这些检查未发现问题，不是完整的文件系统健康保证。清理操作应另行评估，生产环境尤其如此。

## 开发

```bash
python -m pip install -e '.[dev]'
python -m pytest -v
```

[演示视频](docs/demo.mp4) · [MIT 许可证](LICENSE)。
