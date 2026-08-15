# 本地文件快速校验程序

用于把 RAR 分卷或其他归档文件上传网盘前后进行快速、可复查的完整性核验。程序在本机读取文件并建立哈希清单；清单不含本机绝对路径，可随归档一起保存。

## 设计选择

- 默认使用 **BLAKE2b-256**：Python 的 `hashlib` 会调用已编译的原生实现，流式读取时不会把大文件放进内存。对归档上传的意外损坏检测而言，256 位摘要的碰撞风险可忽略。
- SSD 模式以有限线程并行读文件；HDD 模式严格逐文件顺序读取，避免机械硬盘频繁寻道。
- 比对优先按相对路径配对；路径根不同则只在两边文件名均唯一时按文件名配对。重复文件名不会被猜测性配对。
- 状态颜色：绿色＝名称、大小、哈希一致；黄色＝同一文件配对成功但大小/哈希/算法有差异；灰色＝未找到安全配对项或仍未完成哈希。

## 运行

要求 Python 3.10 或更高版本。在 macOS 或 Windows 分别执行：

```bash
python -m venv .venv
python -m pip install -r requirements.txt
python main.py
```

将文件或文件夹直接拖到对应侧的列表，或通过“加载文件夹 / 添加文件”选择。先在左侧扫描并“保存清单”；从网盘重新下载后，右侧加载下载目录并扫描，程序会实时刷新比对颜色。清单也可以加载到任意一侧用于后续校验。

## 分发打包

PyInstaller 不做跨平台编译，应在 Windows 机器打包 Windows `.exe`，在 macOS 机器打包 `.app`：

```bash
python build.py
```

产物位于 `dist/`（Windows 为 `ArchiveHashCheck.exe`，macOS 为 `ArchiveHashCheck.app`）。程序无网络请求、无需管理员权限；扫描仅读取文件，保存清单时不保存源文件绝对路径。

## 项目结构

```text
archive_hash_checker/core.py  # 扫描、BLAKE2b/SHA-256、JSON 清单和比对规则
archive_hash_checker/ui.py    # PySide6 双列表界面、拖放和后台任务
main.py                       # 启动入口
tests/                        # 核心逻辑测试
```
