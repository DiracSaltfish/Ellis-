"""与界面无关的文件扫描、清单和比对逻辑。"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable

MANIFEST_FORMAT = "archive-hash-checker"
MANIFEST_VERSION = 1
DEFAULT_ALGORITHM = "BLAKE2b-256"
HASH_CHUNK_SIZE = 4 * 1024 * 1024


class ScanMode(str, Enum):
    SSD = "ssd"
    HDD = "hdd"


@dataclass(slots=True)
class FileRecord:
    """一个待核查文件或从清单恢复的文件条目。"""

    key: str
    display_name: str
    relative_path: str
    size: int
    modified_ns: int | None = None
    source_path: str | None = None
    digest: str | None = None
    algorithm: str | None = None
    error: str | None = None

    @property
    def has_local_file(self) -> bool:
        return bool(self.source_path)

    @property
    def is_hashed(self) -> bool:
        return self.digest is not None and self.error is None

    @classmethod
    def from_path(cls, path: Path, root: Path | None = None) -> "FileRecord":
        stat = path.stat()
        relative = str(path.relative_to(root)) if root else path.name
        return cls(
            key=relative.replace("\\", "/").casefold(),
            display_name=path.name,
            relative_path=relative,
            size=stat.st_size,
            modified_ns=stat.st_mtime_ns,
            source_path=str(path),
        )


@dataclass(slots=True)
class ComparisonResult:
    """一条记录在左右清单间的显示状态。"""

    status: str
    message: str


def discover_files(
    paths: Iterable[str | Path], cancel_event: threading.Event | None = None
) -> list[FileRecord]:
    """展开文件/目录，并按相对路径创建条目；忽略不存在的路径和目录链接。"""

    records: list[FileRecord] = []
    seen: set[str] = set()
    for raw_path in paths:
        if cancel_event and cancel_event.is_set():
            break
        path = Path(raw_path).expanduser()
        if not path.exists():
            continue
        if path.is_file():
            candidates = [(path, None)]
        elif path.is_dir():
            candidates = ((child, path) for child in path.rglob("*") if child.is_file())
        else:
            continue
        for child, root in candidates:
            if cancel_event and cancel_event.is_set():
                return records
            # 同一文件被拖入多次时只保留一次，避免重复读取。
            canonical = str(child.resolve())
            if canonical in seen:
                continue
            seen.add(canonical)
            try:
                records.append(FileRecord.from_path(child, root))
            except OSError:
                # 枚举到文件后被移动、删除时，跳过即可。
                continue
    return sorted(records, key=lambda record: record.relative_path.casefold())


def _new_hasher(algorithm: str):
    if algorithm == "BLAKE2b-256":
        return hashlib.blake2b(digest_size=32)
    if algorithm == "SHA-256":
        return hashlib.sha256()
    raise ValueError(f"不支持的哈希算法：{algorithm}")


def hash_file(
    record: FileRecord,
    algorithm: str = DEFAULT_ALGORITHM,
    cancel_event: threading.Event | None = None,
    chunk_size: int = HASH_CHUNK_SIZE,
) -> FileRecord:
    """以流式方式计算单个文件；hashlib 在 C 层执行，且大块更新可释放 GIL。"""

    if not record.source_path:
        record.error = "此条目来自清单，没有可读取的本地文件"
        return record

    hasher = _new_hasher(algorithm)
    try:
        with Path(record.source_path).open("rb", buffering=chunk_size) as file:
            while True:
                if cancel_event and cancel_event.is_set():
                    record.error = "扫描已取消"
                    record.digest = None
                    return record
                block = file.read(chunk_size)
                if not block:
                    break
                hasher.update(block)
    except OSError as exc:
        record.error = f"读取失败：{exc.strerror or str(exc)}"
        record.digest = None
        return record

    record.digest = hasher.hexdigest()
    record.algorithm = algorithm
    record.error = None
    return record


ProgressCallback = Callable[[int, int, FileRecord], None]


def scan_records(
    records: list[FileRecord],
    algorithm: str,
    mode: ScanMode,
    max_workers: int,
    cancel_event: threading.Event | None = None,
    progress: ProgressCallback | None = None,
) -> None:
    """原地扫描条目。

    HDD 模式严格按列表顺序一次读取一个文件，降低磁头来回寻道；SSD 模式最多并行
    ``max_workers`` 个文件，让多个读请求与 CPU 哈希并发进行。
    """

    cancel_event = cancel_event or threading.Event()
    total = len(records)
    completed = 0

    def report(record: FileRecord) -> None:
        nonlocal completed
        completed += 1
        if progress:
            progress(completed, total, record)

    if mode is ScanMode.HDD or max_workers <= 1:
        for record in records:
            if cancel_event.is_set():
                break
            hash_file(record, algorithm, cancel_event)
            report(record)
        return

    with ThreadPoolExecutor(max_workers=max(1, max_workers), thread_name_prefix="hash") as executor:
        jobs: dict[Future[FileRecord], FileRecord] = {
            executor.submit(hash_file, record, algorithm, cancel_event): record
            for record in records
        }
        for future in as_completed(jobs):
            record = jobs[future]
            try:
                future.result()
            except Exception as exc:  # 防御性兜底，保证一个异常不终止全批任务。
                record.digest = None
                record.error = f"扫描异常：{exc}"
            report(record)
            if cancel_event.is_set():
                for pending in jobs:
                    pending.cancel()
                break


def _index_for_matching(records: list[FileRecord]) -> tuple[dict[str, list[FileRecord]], dict[str, list[FileRecord]]]:
    by_relative: dict[str, list[FileRecord]] = {}
    by_name: dict[str, list[FileRecord]] = {}
    for record in records:
        by_relative.setdefault(record.key, []).append(record)
        by_name.setdefault(record.display_name.casefold(), []).append(record)
    return by_relative, by_name


def compare_records(
    left: list[FileRecord], right: list[FileRecord]
) -> tuple[dict[int, ComparisonResult], dict[int, ComparisonResult]]:
    """按相对路径优先、唯一文件名兜底配对，返回两侧逐行颜色状态。

    若某个文件名在一侧出现多次而相对路径又不同，不会猜测配对关系，保留灰色提示，
    防止错误地把不同文件判定为一致。
    """

    left_relative, left_names = _index_for_matching(left)
    right_relative, right_names = _index_for_matching(right)
    paired: dict[int, FileRecord] = {}
    used_right: set[int] = set()

    for left_record in left:
        exact = [item for item in right_relative.get(left_record.key, []) if id(item) not in used_right]
        if len(exact) == 1:
            paired[id(left_record)] = exact[0]
            used_right.add(id(exact[0]))
            continue
        # 顶层拖入的文件通常没有相同根目录；仅在文件名唯一时安全兜底匹配。
        same_name = [item for item in right_names.get(left_record.display_name.casefold(), []) if id(item) not in used_right]
        if len(left_names[left_record.display_name.casefold()]) == 1 and len(same_name) == 1:
            paired[id(left_record)] = same_name[0]
            used_right.add(id(same_name[0]))

    left_result: dict[int, ComparisonResult] = {}
    right_result: dict[int, ComparisonResult] = {}
    for left_record in left:
        right_record = paired.get(id(left_record))
        if right_record is None:
            result = ComparisonResult("unmatched", "另一侧没有可安全配对的同名文件")
            left_result[id(left_record)] = result
            continue
        if left_record.error or right_record.error:
            result = ComparisonResult("different", "文件读取失败或扫描被取消")
        elif not left_record.is_hashed or not right_record.is_hashed:
            result = ComparisonResult("unmatched", "已找到同名文件，等待两侧哈希完成")
        elif left_record.algorithm != right_record.algorithm:
            result = ComparisonResult("different", "哈希算法不同，无法直接比对")
        elif left_record.size != right_record.size:
            result = ComparisonResult("different", "文件大小不同")
        elif left_record.digest == right_record.digest:
            result = ComparisonResult("match", "名称、大小和哈希均一致")
        else:
            result = ComparisonResult("different", "哈希不一致")
        left_result[id(left_record)] = result
        right_result[id(right_record)] = result

    for right_record in right:
        right_result.setdefault(id(right_record), ComparisonResult("unmatched", "另一侧没有可安全配对的同名文件"))
    return left_result, right_result


def save_manifest(path: str | Path, records: list[FileRecord], side_name: str) -> None:
    """保存可携带 JSON 清单；不写入本机绝对路径以保护隐私和可移植性。"""

    payload = {
        "format": MANIFEST_FORMAT,
        "version": MANIFEST_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "side_name": side_name,
        "records": [],
    }
    for record in records:
        data = asdict(record)
        data["source_path"] = None
        payload["records"].append(data)
    with Path(path).open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def load_manifest(path: str | Path) -> list[FileRecord]:
    """读取并校验清单格式。"""

    with Path(path).open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if payload.get("format") != MANIFEST_FORMAT or payload.get("version") != MANIFEST_VERSION:
        raise ValueError("不是本程序生成的兼容清单，或清单版本不受支持")
    raw_records = payload.get("records")
    if not isinstance(raw_records, list):
        raise ValueError("清单中缺少 records 列表")
    records: list[FileRecord] = []
    fields = set(FileRecord.__dataclass_fields__)
    for raw in raw_records:
        if not isinstance(raw, dict):
            raise ValueError("清单记录格式无效")
        # 忽略未来版本新增字段，但保持当前构造器严格。
        data = {key: value for key, value in raw.items() if key in fields}
        data["source_path"] = None
        try:
            records.append(FileRecord(**data))
        except TypeError as exc:
            raise ValueError(f"清单记录缺少必要字段：{exc}") from exc
    return records
