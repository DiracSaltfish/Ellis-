#!/usr/bin/env python3
"""Build and validate offline evidence for a Machome Hub owner handoff.

This tool deliberately has no process, launchctl, network, or service-control
code.  It only reads local plist/JSON/executable files and writes narrowly
scoped JSON artifacts with mode 0600.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import secrets
import stat
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


TOOL_VERSION = "1.0"
MAX_PLIST_BYTES = 2 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_ARGUMENTS = 256
MAX_ARGUMENT_BYTES = 16 * 1024
MAX_MANIFEST_FILES = 4096
MAX_MANIFEST_PATH_BYTES = 4096
MODULE_IDS = ("upload", "premium", "webull", "redemption")
LABEL_RE = re.compile(r"^[A-Za-z0-9._-]{1,255}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DEPLOYMENT_MANIFEST_KIND = "machome_owner_deployment_manifest"
DEPLOYMENT_MANIFEST_PATH_BASE = "manifest_directory"
SHELL_LAUNCHERS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
GENERIC_LAUNCHERS = SHELL_LAUNCHERS | {
    "env",
    "node",
    "nodejs",
    "perl",
    "ruby",
    "osascript",
}


class HandoffError(RuntimeError):
    """A user-facing validation failure."""


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _json_bytes(value: Dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
    except OSError as exc:
        raise HandoffError(f"无法计算可执行文件 SHA-256：{path}：{exc}") from exc
    return digest.hexdigest()


def _artifact_record(raw_path: str, label: str) -> Dict[str, str]:
    path = _absolute_path(raw_path, label)
    try:
        info = path.stat()
    except OSError as exc:
        raise HandoffError(f"{label}不存在或无法读取：{path}：{exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise HandoffError(f"{label}不是普通文件：{path}")
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise HandoffError(f"{label}可被组或其他用户写入，已拒绝：{path}")
    return {"path": str(path), "sha256": _sha256_file(path)}


def _normalize_plist_path_argument(raw: str) -> Optional[Path]:
    """Normalize an absolute plist argument without requiring it to exist."""

    if not raw.startswith("/"):
        return None
    return _absolute_path(raw, "ProgramArguments path")


def _validate_python_entry(arguments: Sequence[str], raw_entry: str) -> Path:
    """Bind --python-entry to the actual entry recorded by ProgramArguments.

    A separately supplied path is not evidence that launchd will execute it.  The
    normalized entry must occur exactly once in the plist arguments.  When the
    plist exposes one or more .py/.pyw arguments, the first such argument is the
    executable Python entry rather than (for example) a later config/plugin path.
    """

    entry = _absolute_path(raw_entry, "Python entry script")
    normalized_arguments: List[Tuple[int, Path]] = []
    for index, argument in enumerate(arguments):
        normalized = _normalize_plist_path_argument(argument)
        if normalized is not None:
            normalized_arguments.append((index, normalized))

    matching_indexes = [
        index for index, normalized in normalized_arguments if normalized == entry
    ]
    if len(matching_indexes) != 1:
        raise HandoffError(
            "--python-entry 规范化后必须在 plist ProgramArguments 中精确出现一次；"
            f"当前出现 {len(matching_indexes)} 次：{entry}"
        )

    script_candidates = [
        (index, normalized)
        for index, normalized in normalized_arguments
        if normalized.suffix.lower() in (".py", ".pyw")
    ]
    if script_candidates and script_candidates[0][1] != entry:
        raise HandoffError(
            "--python-entry 与 ProgramArguments 中首个 Python 脚本（真实入口）不一致："
            f"期望 {script_candidates[0][1]}，收到 {entry}"
        )
    return entry


def _launcher_payload(
    program_path: Path, arguments: Sequence[str], working_directory: Any
) -> Optional[Path]:
    """Resolve the executable payload of a generic interpreter/launcher.

    Hashing only /bin/zsh, /usr/bin/env, or a language runtime does not bind
    the service implementation.  For these launchers the first non-option
    payload is therefore frozen automatically.  Relative payloads are accepted
    only when the plist declares an absolute WorkingDirectory.
    """

    launcher = program_path.name.lower()
    if launcher.startswith("python"):
        return None
    if launcher not in GENERIC_LAUNCHERS:
        return None
    if launcher in SHELL_LAUNCHERS and "-c" in arguments:
        raise HandoffError(
            "owner 不接受 shell -c 隐式实现；请改为独立脚本并在 ProgramArguments 中显式列出"
        )

    payload_argument: Optional[str] = None
    after_options = False
    for argument in arguments:
        if argument == "--":
            after_options = True
            continue
        if not after_options and argument.startswith("-"):
            continue
        if launcher == "env" and "=" in argument and not argument.startswith("/"):
            key = argument.split("=", 1)[0]
            if key and all(ch.isalnum() or ch == "_" for ch in key):
                continue
        payload_argument = argument
        break
    if not payload_argument:
        raise HandoffError(
            f"通用 launcher {program_path} 未在 ProgramArguments 中暴露可冻结的 payload"
        )

    if payload_argument.startswith("/"):
        return _absolute_path(payload_argument, "launcher payload")
    if not isinstance(working_directory, str) or not working_directory.startswith("/"):
        raise HandoffError(
            f"通用 launcher {program_path} 使用相对 payload={payload_argument!r}；"
            "plist 必须提供绝对 WorkingDirectory，或将 payload 改为绝对路径"
        )
    working_root = _absolute_path(working_directory, "WorkingDirectory")
    return _absolute_path(str(working_root / payload_argument), "launcher payload")


def _deployment_manifest_artifacts(
    raw_manifest_path: str, python_entry: Path
) -> Tuple[Dict[str, str], List[Dict[str, str]]]:
    """Validate the deployment manifest and return every frozen file artifact.

    Schema v1 resolves POSIX relative paths against the manifest's directory.
    Canonical-path checks prevent both lexical ``..`` traversal and symlink
    escape.  The Python entry itself must be one of the declared files.
    """

    manifest_path = _absolute_path(raw_manifest_path, "Python deployment manifest")
    manifest_record = _artifact_record(str(manifest_path), "Python deployment manifest")
    raw = _read_limited(manifest_path, MAX_JSON_BYTES, "Python deployment manifest")
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HandoffError(
            f"Python deployment manifest 不是有效 UTF-8 JSON：{manifest_path}：{exc}"
        ) from exc
    if not isinstance(document, dict):
        raise HandoffError("Python deployment manifest 根必须是 JSON 对象")

    required_keys = {"schema_version", "kind", "path_base", "files"}
    allowed_keys = required_keys | {"metadata"}
    missing_keys = sorted(required_keys - set(document))
    unknown_keys = sorted(set(document) - allowed_keys)
    if missing_keys:
        raise HandoffError(
            "Python deployment manifest 缺少字段：" + ", ".join(missing_keys)
        )
    if unknown_keys:
        raise HandoffError(
            "Python deployment manifest 含未知字段：" + ", ".join(unknown_keys)
        )
    if document.get("schema_version") != 1:
        raise HandoffError("Python deployment manifest schema_version 必须为 1")
    if document.get("kind") != DEPLOYMENT_MANIFEST_KIND:
        raise HandoffError(
            "Python deployment manifest kind 必须为 " + DEPLOYMENT_MANIFEST_KIND
        )
    if document.get("path_base") != DEPLOYMENT_MANIFEST_PATH_BASE:
        raise HandoffError(
            "Python deployment manifest path_base 必须为 "
            + DEPLOYMENT_MANIFEST_PATH_BASE
        )
    if "metadata" in document and not isinstance(document["metadata"], dict):
        raise HandoffError("Python deployment manifest metadata 必须是 JSON 对象")

    files = document.get("files")
    if not isinstance(files, list) or not files:
        raise HandoffError("Python deployment manifest files 必须是非空数组")
    if len(files) > MAX_MANIFEST_FILES:
        raise HandoffError(
            f"Python deployment manifest files 超过 {MAX_MANIFEST_FILES} 项上限"
        )

    try:
        lexical_root = manifest_path.parent
        root = lexical_root.resolve(strict=True)
        entry_resolved = python_entry.resolve(strict=True)
        manifest_resolved = manifest_path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HandoffError(f"无法规范化 deployment manifest/entry 路径：{exc}") from exc
    if not root.is_dir():
        raise HandoffError(f"deployment manifest 父路径不是目录：{root}")

    artifacts: List[Dict[str, str]] = []
    seen_relative_paths = set()
    seen_resolved_paths = set()
    entry_declared = False
    for index, item in enumerate(files):
        item_label = f"Python deployment manifest files[{index}]"
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise HandoffError(f"{item_label} 必须且只能包含 path 与 sha256")
        relative = item.get("path")
        expected_hash = item.get("sha256")
        if (
            not isinstance(relative, str)
            or not relative
            or "\x00" in relative
            or "\n" in relative
            or "\r" in relative
            or "\\" in relative
            or len(relative.encode("utf-8")) > MAX_MANIFEST_PATH_BYTES
        ):
            raise HandoffError(f"{item_label}.path 必须是安全的 POSIX 相对路径")
        relative_path = PurePosixPath(relative)
        if (
            relative_path.is_absolute()
            or relative_path.as_posix() != relative
            or any(part in ("", ".", "..") for part in relative_path.parts)
        ):
            raise HandoffError(f"{item_label}.path 必须是规范且不含目录逃逸的相对路径")
        if relative in seen_relative_paths:
            raise HandoffError(f"Python deployment manifest 路径重复：{relative}")
        seen_relative_paths.add(relative)
        if not isinstance(expected_hash, str) or not SHA256_RE.fullmatch(expected_hash):
            raise HandoffError(f"{item_label}.sha256 必须是 64 位小写十六进制")

        lexical_target = lexical_root.joinpath(*relative_path.parts)
        try:
            resolved_target = lexical_target.resolve(strict=True)
            resolved_target.relative_to(root)
        except FileNotFoundError as exc:
            raise HandoffError(f"deployment manifest 文件不存在：{relative}") from exc
        except ValueError as exc:
            raise HandoffError(f"deployment manifest 路径逃逸根目录：{relative}") from exc
        except (OSError, RuntimeError) as exc:
            raise HandoffError(f"无法解析 deployment manifest 文件 {relative}：{exc}") from exc
        if resolved_target == manifest_resolved:
            raise HandoffError("deployment manifest 不得把自身列入 files（无法形成稳定自校验 hash）")
        resolved_key = str(resolved_target)
        if resolved_key in seen_resolved_paths:
            raise HandoffError(
                f"deployment manifest 多个相对路径指向同一文件：{relative}"
            )
        seen_resolved_paths.add(resolved_key)

        record = _artifact_record(str(lexical_target), f"deployment file {relative}")
        if record["sha256"] != expected_hash:
            raise HandoffError(
                f"deployment manifest hash 不符：{relative}；"
                f"期望 {expected_hash}，实际 {record['sha256']}"
            )
        if resolved_target == entry_resolved:
            entry_declared = True
        artifacts.append(record)

    if not entry_declared:
        raise HandoffError(
            "Python deployment manifest files 必须包含 --python-entry 对应文件"
        )
    return manifest_record, artifacts


def _absolute_path(raw: str, label: str) -> Path:
    if not raw or "\x00" in raw or "\n" in raw or "\r" in raw:
        raise HandoffError(f"{label}为空或含非法字符")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise HandoffError(f"{label}必须是绝对路径（可使用 ~/ 前缀）：{raw}")
    normalized = Path(os.path.normpath(str(path)))
    if normalized in (Path("/"), Path.home()):
        raise HandoffError(f"{label}指向过宽路径，已拒绝：{normalized}")
    return normalized


def _lstat_regular(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise HandoffError(f"无法读取{label}：{path}：{exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise HandoffError(f"{label}必须是非符号链接的普通文件：{path}")
    return info


def _require_owned_by_current_user(info: os.stat_result, path: Path, label: str) -> None:
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise HandoffError(f"{label}不属于当前用户，已拒绝：{path}")


def _require_secure_parent(path: Path) -> None:
    parent = path.parent
    try:
        info = parent.lstat()
    except OSError as exc:
        raise HandoffError(f"输出目录不存在或无法访问：{parent}：{exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise HandoffError(f"输出父路径必须是非符号链接目录：{parent}")
    _require_owned_by_current_user(info, parent, "输出目录")
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise HandoffError(f"输出目录可被其他用户写入，已拒绝：{parent}")


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        descriptor = os.open(str(path), flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _path_entry_exists(path: Path, label: str) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise HandoffError(f"无法检查{label}：{path}：{exc}") from exc


def _preflight_private_output(path: Path, *, replace: bool = False) -> None:
    """Validate an output before a multi-file operation starts."""

    _require_secure_parent(path)
    try:
        existing = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise HandoffError(f"无法检查输出目标：{path}：{exc}") from exc
    if stat.S_ISLNK(existing.st_mode) or not stat.S_ISREG(existing.st_mode):
        raise HandoffError(f"输出目标不是安全普通文件：{path}")
    _require_owned_by_current_user(existing, path, "输出目标")
    if not replace:
        raise HandoffError(f"输出文件已存在，未进行覆盖：{path}")


def _atomic_private_write(path: Path, payload: bytes, *, replace: bool = False) -> None:
    """Write a 0600 file atomically; never follow or silently replace symlinks."""

    _preflight_private_output(path, replace=replace)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            # The existing target was lstat'ed above. os.replace is atomic and
            # replaces the directory entry itself, never a symlink destination.
            os.replace(str(temporary), str(path))
        else:
            # link() gives atomic create-if-absent semantics and closes the
            # check/write race that os.replace would introduce.
            try:
                os.link(str(temporary), str(path))
            except FileExistsError as exc:
                raise HandoffError(f"输出文件在写入期间已被创建，已拒绝覆盖：{path}") from exc
            temporary.unlink()
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        _fsync_directory(path.parent)
    except HandoffError:
        raise
    except OSError as exc:
        raise HandoffError(f"无法原子写入 {path}：{exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_limited(path: Path, maximum: int, label: str) -> bytes:
    info = _lstat_regular(path, label)
    if info.st_size > maximum:
        raise HandoffError(f"{label}超过 {maximum} 字节上限：{path}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise HandoffError(f"无法读取{label}：{path}：{exc}") from exc


def _read_private_json(path: Path, label: str) -> Tuple[Dict[str, Any], os.stat_result]:
    info = _lstat_regular(path, label)
    _require_owned_by_current_user(info, path, label)
    if info.st_mode & 0o077:
        raise HandoffError(f"{label}权限必须仅当前用户可读写（0600）：{path}")
    raw = _read_limited(path, MAX_JSON_BYTES, label)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HandoffError(f"{label}不是有效 UTF-8 JSON：{path}：{exc}") from exc
    if not isinstance(value, dict):
        raise HandoffError(f"{label}根必须是 JSON 对象：{path}")
    return value, info


def _clean_values(values: Iterable[str], label: str, *, absolute: bool = False) -> List[str]:
    result: List[str] = []
    seen = set()
    for raw in values:
        value = raw.strip()
        if not value or "\x00" in value or "\n" in value or "\r" in value:
            raise HandoffError(f"{label}不得为空或含换行")
        if absolute and not value.startswith("/"):
            raise HandoffError(f"{label}必须是 ps 中的绝对命令前缀：{value}")
        if value in seen:
            raise HandoffError(f"{label}重复：{value}")
        seen.add(value)
        result.append(value)
    return result


def _parse_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise HandoffError(f"{label}缺少有效 UTC 时间")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise HandoffError(f"{label}时间格式无效：{value}") from exc
    if parsed.tzinfo is None:
        raise HandoffError(f"{label}必须含时区：{value}")
    return parsed.astimezone(timezone.utc)


def _emit_json(value: Dict[str, Any]) -> None:
    sys.stdout.buffer.write(_json_bytes(value))


def command_inspect_plist(args: argparse.Namespace) -> int:
    plist_path = _absolute_path(args.plist, "plist")
    info = _lstat_regular(plist_path, "plist")
    _require_owned_by_current_user(info, plist_path, "plist")
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise HandoffError(f"plist 可被组或其他用户写入，不能用于 owner：{plist_path}")
    raw = _read_limited(plist_path, MAX_PLIST_BYTES, "plist")
    try:
        document = plistlib.loads(raw)
    except (plistlib.InvalidFileException, ValueError, TypeError) as exc:
        raise HandoffError(f"plist 解析失败：{plist_path}：{exc}") from exc
    if not isinstance(document, dict):
        raise HandoffError("plist 根必须是 dictionary")

    label = document.get("Label")
    if not isinstance(label, str) or not LABEL_RE.fullmatch(label):
        raise HandoffError("plist Label 缺失或含非法字符")
    if args.expected_label and label != args.expected_label:
        raise HandoffError(f"plist Label={label} 与 --expected-label={args.expected_label} 不一致")

    program = document.get("Program", "")
    if program is not None and not isinstance(program, str):
        raise HandoffError("plist Program 必须是字符串")
    arguments = document.get("ProgramArguments", [])
    if not isinstance(arguments, list) or any(not isinstance(item, str) for item in arguments):
        raise HandoffError("plist ProgramArguments 必须是字符串数组")
    if len(arguments) > MAX_ARGUMENTS:
        raise HandoffError(f"ProgramArguments 超过 {MAX_ARGUMENTS} 项上限")
    for argument in arguments:
        if "\x00" in argument or len(argument.encode("utf-8")) > MAX_ARGUMENT_BYTES:
            raise HandoffError("ProgramArguments 含 NUL 或单项过长")

    expected_arguments = list(arguments)
    if not program and expected_arguments:
        program = expected_arguments[0]
    if expected_arguments and expected_arguments[0] == program:
        expected_arguments.pop(0)
    if not isinstance(program, str) or not program.startswith("/"):
        raise HandoffError(f"最终 Program 必须是绝对路径：{program!r}")
    program_path = _absolute_path(program, "Program")
    program = str(program_path)
    try:
        executable_info = program_path.stat()
    except OSError as exc:
        raise HandoffError(f"Program 不存在或无法读取：{program}：{exc}") from exc
    if not stat.S_ISREG(executable_info.st_mode):
        raise HandoffError(f"Program 不是普通文件：{program}")
    if executable_info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise HandoffError(f"Program 可被组或其他用户写入，已拒绝：{program}")

    looks_like_python = "python" in program_path.name.lower() or any(
        argument.lower().endswith((".py", ".pyw")) for argument in expected_arguments
    )
    if looks_like_python and not args.python_unit:
        raise HandoffError(
            "该 unit 似乎是 Python；必须显式使用 --python-unit "
            "--python-entry --deployment-manifest，不得只冻结解释器"
        )
    if args.python_unit and (not args.python_entry or not args.deployment_manifest):
        raise HandoffError(
            "--python-unit 必须同时提供 --python-entry 与 --deployment-manifest"
        )
    if not args.python_unit and (args.python_entry or args.deployment_manifest):
        raise HandoffError(
            "--python-entry/--deployment-manifest 只能与 --python-unit 一起使用"
        )

    requested_artifacts: List[Tuple[str, str]] = [(program, "Program")]
    launcher_payload = _launcher_payload(
        program_path, expected_arguments, document.get("WorkingDirectory")
    )
    if launcher_payload is not None:
        requested_artifacts.append((str(launcher_payload), "launcher payload"))
    manifest_artifacts: List[Dict[str, str]] = []
    if args.python_unit:
        python_entry = _validate_python_entry(arguments, args.python_entry)
        manifest_record, manifest_files = _deployment_manifest_artifacts(
            args.deployment_manifest, python_entry
        )
        requested_artifacts.extend(
            [
                (str(python_entry), "Python entry script"),
                (manifest_record["path"], "Python deployment manifest"),
            ]
        )
        manifest_artifacts = manifest_files
    requested_artifacts.extend((path, "additional artifact") for path in args.artifact)
    expected_artifacts: List[Dict[str, str]] = []
    seen_artifact_paths = set()
    for raw_artifact, artifact_label in requested_artifacts:
        record = _artifact_record(raw_artifact, artifact_label)
        if record["path"] in seen_artifact_paths:
            continue
        seen_artifact_paths.add(record["path"])
        expected_artifacts.append(record)
    for record in manifest_artifacts:
        if record["path"] in seen_artifact_paths:
            continue
        seen_artifact_paths.add(record["path"])
        expected_artifacts.append(record)

    program_hash = _sha256_file(program_path)
    if not expected_artifacts or expected_artifacts[0] != {
        "path": program,
        "sha256": program_hash,
    }:
        raise HandoffError("expected_artifacts 内部不变式失败：Program/hash 未居首")

    unit_id = args.unit_id.strip()
    if not unit_id or not LABEL_RE.fullmatch(unit_id):
        raise HandoffError("--unit-id 必须是非空安全标识")
    snippet: Dict[str, Any] = {
        "id": unit_id,
        "label": label,
        "plist": str(plist_path),
        "expected_program": program,
        "expected_arguments": expected_arguments,
        "artifact_sha256": program_hash,
        "expected_artifacts": expected_artifacts,
        "start_delay_ms": args.start_delay_ms,
        "required": not args.optional,
    }
    result = {
        "schema_version": 1,
        "kind": "machome_owner_launchd_identity",
        "inspected_at": _iso_utc(datetime.now(timezone.utc)),
        "plist_sha256": _sha256_bytes(raw),
        "launchd_unit": snippet,
    }
    if args.output:
        output = _absolute_path(args.output, "output")
        if output == plist_path or output == program_path:
            raise HandoffError("输出不得覆盖 plist 或 Program")
        _atomic_private_write(output, _json_bytes(result))
        result["output"] = str(output)
    _emit_json(result)
    return 0


def _validate_existing_marker_for_replacement(
    marker_path: Path, module_id: str, owner_id: str, generation: int
) -> None:
    marker, _ = _read_private_json(marker_path, "旧 handoff marker")
    if marker.get("schema_version") != 1:
        raise HandoffError("旧 marker schema_version 不受支持")
    if marker.get("module_id") != module_id or marker.get("owner_id") != owner_id:
        raise HandoffError("旧 marker 不属于同一 module/owner，已拒绝替换")
    old_generation = marker.get("generation")
    if not isinstance(old_generation, int) or isinstance(old_generation, bool):
        raise HandoffError("旧 marker generation 无效")
    if old_generation > generation:
        raise HandoffError("旧 marker 世代更新，已拒绝降级替换")
    if _parse_utc(marker.get("expires_at"), "旧 marker expires_at") > datetime.now(timezone.utc):
        raise HandoffError("旧 marker 尚未过期，已拒绝覆盖")


def _validate_generation_against_state(
    state_path: Path, module_id: str, owner_id: str, generation: int
) -> None:
    if not _path_entry_exists(state_path, "owner state"):
        return
    state, _ = _read_private_json(state_path, "owner state")
    if state.get("schema_version") != 1:
        raise HandoffError(f"owner state schema_version 不受支持：{state_path}")
    if state.get("module_id") != module_id:
        raise HandoffError(f"owner state module_id 不匹配，疑似 lock-path 用错：{state_path}")
    if state.get("owner_id") != owner_id:
        raise HandoffError(f"owner state owner_id 不匹配，必须先按 runbook 回退：{state_path}")
    state_generation = state.get("generation")
    if not isinstance(state_generation, int) or isinstance(state_generation, bool):
        raise HandoffError("owner state generation 无效")
    if state.get("active") is True:
        raise HandoffError("当前 owner state 仍 active；先停止 Hub owner 并用 invalidate-state 失效化")
    if generation <= state_generation:
        raise HandoffError(
            f"新 generation={generation} 必须大于已失效 generation={state_generation}"
        )


def command_prepare_marker(args: argparse.Namespace) -> int:
    if not args.previous_owner_stopped:
        raise HandoffError(
            "未收到 --previous-owner-stopped 明确声明；本工具不会代替人工停止和核对旧 owner"
        )
    owner_id = args.owner_id.strip()
    if len(owner_id) < 8 or len(owner_id) > 128 or any(ch in owner_id for ch in "\x00\r\n"):
        raise HandoffError("--owner-id 长度必须为 8–128 且不含换行/NUL")
    if args.generation < 1:
        raise HandoffError("--generation 必须 >= 1")
    if args.ttl_seconds < 60 or args.ttl_seconds > 900:
        raise HandoffError("--ttl-seconds 必须在 60–900 之间（Agent 不接受超过 15 分钟）")

    lock_path = _absolute_path(args.lock_path, "lock-path")
    marker_path = _absolute_path(args.marker, "marker")
    if lock_path == marker_path or marker_path == Path(str(lock_path) + ".state.json"):
        raise HandoffError("lock、state 与 marker 路径必须不同")
    _require_secure_parent(lock_path)
    _require_secure_parent(marker_path)
    state_path = Path(str(lock_path) + ".state.json")
    _validate_generation_against_state(
        state_path, args.module_id, owner_id, args.generation
    )

    previous_labels = _clean_values(args.previous_label, "previous-label")
    for label in previous_labels:
        if not LABEL_RE.fullmatch(label):
            raise HandoffError(f"previous-label 含非法字符：{label}")
    previous_processes = _clean_values(
        args.previous_process, "previous-process", absolute=True
    )
    if not previous_labels and not previous_processes:
        raise HandoffError("至少需要一个 --previous-label 或 --previous-process")

    replace_marker = False
    if _path_entry_exists(marker_path, "marker"):
        if not args.replace_expired_marker:
            raise HandoffError(
                f"marker 已存在，已拒绝覆盖：{marker_path}；"
                "仅可对同 owner/module 的过期 marker 使用 --replace-expired-marker"
            )
        _validate_existing_marker_for_replacement(
            marker_path, args.module_id, owner_id, args.generation
        )
        replace_marker = True

    receipt_path: Optional[Path] = None
    if args.receipt:
        receipt_path = _absolute_path(args.receipt, "receipt")
        if receipt_path in (marker_path, lock_path, state_path):
            raise HandoffError("receipt 不得与 marker/lock/state 共用路径")
        _preflight_private_output(receipt_path)

    now = datetime.now(timezone.utc)
    token = secrets.token_urlsafe(48)
    nonce = secrets.token_urlsafe(32)
    marker = {
        "schema_version": 1,
        "module_id": args.module_id,
        "owner_id": owner_id,
        "generation": args.generation,
        "handoff_token": token,
        "nonce": nonce,
        "issued_at": _iso_utc(now),
        "expires_at": _iso_utc(now + timedelta(seconds=args.ttl_seconds)),
        "previous_owner_stopped": True,
    }
    marker_bytes = _json_bytes(marker)
    _atomic_private_write(marker_path, marker_bytes, replace=replace_marker)

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    receipt: Dict[str, Any] = {
        "schema_version": 1,
        "kind": "machome_owner_handoff_receipt",
        "prepared_at": _iso_utc(now),
        "module_id": args.module_id,
        "owner_lease": {
            "lock_path": str(lock_path),
            "handoff_marker": str(marker_path),
            "owner_id": owner_id,
            "generation": args.generation,
            "handoff_token_sha256": token_hash,
            "previous_owner_processes": previous_processes,
            "previous_owner_labels": previous_labels,
        },
        "marker_sha256": _sha256_bytes(marker_bytes),
        "marker_expires_at": marker["expires_at"],
        "operator_assertion": "previous_owner_stopped",
        "warning": (
            "本工具未核对也未启停任何进程/launchd；"
            "Hub Agent 仍会独立校验旧监管器不存在"
        ),
    }
    if receipt_path is not None:
        try:
            _atomic_private_write(receipt_path, _json_bytes(receipt))
        except Exception:
            # The sensitive marker has already been committed. Keep it intact
            # and report the exact path so the operator can deliberately remove
            # or consume it; silently deleting evidence would be less safe.
            raise HandoffError(
                f"marker 已安全写入 {marker_path}，但 receipt 写入失败；"
                "不要重试生成，请先处理该 marker"
            )
        receipt["receipt_file"] = str(receipt_path)
    _emit_json(receipt)
    return 0


def command_invalidate_state(args: argparse.Namespace) -> int:
    if not args.confirm_owner_stopped:
        raise HandoffError(
            "未收到 --confirm-owner-stopped 明确声明；"
            "必须先安全停止本模块 Hub 托管服务并退出 Hub Agent"
        )
    state_path = _absolute_path(args.state, "state")
    state, _ = _read_private_json(state_path, "owner state")
    if state.get("schema_version") != 1:
        raise HandoffError("owner state schema_version 不受支持")
    if state.get("module_id") != args.module_id:
        raise HandoffError(
            f"state module_id={state.get('module_id')!r} 与期望 {args.module_id!r} 不一致"
        )
    if state.get("owner_id") != args.owner_id:
        raise HandoffError("state owner_id 与 --owner-id 不一致")
    generation = state.get("generation")
    if not isinstance(generation, int) or isinstance(generation, bool) or generation != args.generation:
        raise HandoffError(
            f"state generation={generation!r} 与期望 {args.generation!r} 不一致"
        )
    nonce_hash = state.get("marker_nonce_sha256")
    if not isinstance(nonce_hash, str) or not SHA256_RE.fullmatch(nonce_hash):
        raise HandoffError("state marker_nonce_sha256 缺失或无效")
    if args.marker_nonce_sha256 and nonce_hash != args.marker_nonce_sha256.lower():
        raise HandoffError("state marker_nonce_sha256 与显式期望值不一致")
    reason = args.reason.strip()
    if len(reason) < 8 or len(reason) > 512 or any(ch in reason for ch in "\x00\r\n"):
        raise HandoffError("--reason 必须是 8–512 个无换行字符")

    if state.get("active") is not True:
        _emit_json(
            {
                "changed": False,
                "state": str(state_path),
                "module_id": args.module_id,
                "generation": args.generation,
                "message": "owner state 已是 inactive，未写入文件",
            }
        )
        return 0

    invalidated_at = _iso_utc(datetime.now(timezone.utc))
    state["active"] = False
    state["invalidated_at"] = invalidated_at
    state["invalidation_reason"] = reason
    state["invalidated_by"] = "owner_handoff.py"
    _atomic_private_write(state_path, _json_bytes(state), replace=True)
    _emit_json(
        {
            "changed": True,
            "state": str(state_path),
            "module_id": args.module_id,
            "owner_id": args.owner_id,
            "generation": args.generation,
            "invalidated_at": invalidated_at,
            "marker_nonce_sha256": nonce_hash,
            "warning": (
                "仅 owner state 文件已失效；本工具未停止服务、"
                "未释放运行中 Agent 的 lease，也未恢复旧 owner"
            ),
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Machome Hub owner 交接的离线文件工具；"
            "永远不调用 launchctl、不启停进程、不访问网络。"
        )
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {TOOL_VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser(
        "inspect-plist",
        help="读取本地 plist 和 Program，生成 launchd 精确身份/SHA-256 片段",
    )
    inspect_parser.add_argument("--plist", required=True, help="待检查 plist 的绝对路径")
    inspect_parser.add_argument("--unit-id", required=True, help="写入 launchd_unit.id 的稳定标识")
    inspect_parser.add_argument("--expected-label", help="可选；要求 plist Label 精确等于此值")
    inspect_parser.add_argument("--start-delay-ms", type=int, default=0)
    inspect_parser.add_argument("--optional", action="store_true", help="生成 required=false")
    inspect_parser.add_argument(
        "--artifact",
        action="append",
        default=[],
        help="附加冻结的绝对文件路径（可重复）",
    )
    inspect_parser.add_argument(
        "--python-unit",
        action="store_true",
        help="标记 Python/打包 Python unit；将强制入口脚本与部署 manifest",
    )
    inspect_parser.add_argument("--python-entry", help="Python 真实入口脚本的绝对路径")
    inspect_parser.add_argument(
        "--deployment-manifest",
        help=(
            "schema_v1 部署 manifest 绝对路径；files 为相对 manifest 目录的"
            "逐文件 path/sha256 清单"
        ),
    )
    inspect_parser.add_argument("--output", help="可选 0600 JSON 输出；已存在则拒绝")
    inspect_parser.set_defaults(handler=command_inspect_plist)

    marker_parser = subparsers.add_parser(
        "prepare-marker",
        help="在旧 owner 已由人工停止/核对后，生成一次性 0600 handoff marker",
    )
    marker_parser.add_argument("--module-id", choices=MODULE_IDS, required=True)
    marker_parser.add_argument("--owner-id", required=True)
    marker_parser.add_argument("--generation", type=int, required=True)
    marker_parser.add_argument("--lock-path", required=True, help="owner_lease.lock_path")
    marker_parser.add_argument("--marker", required=True, help="owner_lease.handoff_marker")
    marker_parser.add_argument("--ttl-seconds", type=int, default=600)
    marker_parser.add_argument("--previous-label", action="append", default=[])
    marker_parser.add_argument("--previous-process", action="append", default=[])
    marker_parser.add_argument(
        "--previous-owner-stopped",
        action="store_true",
        help="必选声明：操作员已按 runbook 停止并核对旧 owner",
    )
    marker_parser.add_argument(
        "--replace-expired-marker",
        action="store_true",
        help="仅替换同 module/owner 且已过期、世代不更新的 marker",
    )
    marker_parser.add_argument("--receipt", help="可选 0600 脱敏回执；已存在则拒绝")
    marker_parser.set_defaults(handler=command_prepare_marker)

    invalidate_parser = subparsers.add_parser(
        "invalidate-state",
        help="在 Hub owner/Agent 已安全停止后，原子将精确 owner state 置为 inactive",
    )
    invalidate_parser.add_argument("--state", required=True, help="lock_path + .state.json 的绝对路径")
    invalidate_parser.add_argument("--module-id", choices=MODULE_IDS, required=True)
    invalidate_parser.add_argument("--owner-id", required=True)
    invalidate_parser.add_argument("--generation", type=int, required=True)
    invalidate_parser.add_argument(
        "--marker-nonce-sha256",
        help="可选第二校验；要求精确匹配 state 中的 marker_nonce_sha256",
    )
    invalidate_parser.add_argument("--reason", required=True, help="8–512 字符的回退/失效原因")
    invalidate_parser.add_argument(
        "--confirm-owner-stopped",
        action="store_true",
        help="必选声明：本模块 Hub 服务与 Agent 已安全停止",
    )
    invalidate_parser.set_defaults(handler=command_invalidate_state)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    os.umask(0o077)
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(args, "start_delay_ms") and not 0 <= args.start_delay_ms <= 300_000:
        parser.error("--start-delay-ms 必须在 0–300000 之间")
    if hasattr(args, "marker_nonce_sha256") and args.marker_nonce_sha256:
        normalized_hash = args.marker_nonce_sha256.strip().lower()
        if not SHA256_RE.fullmatch(normalized_hash):
            parser.error("--marker-nonce-sha256 必须是 64 位十六进制")
        args.marker_nonce_sha256 = normalized_hash
    try:
        return int(args.handler(args))
    except HandoffError as exc:
        print(f"owner_handoff: ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("owner_handoff: ERROR: 操作已取消", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
