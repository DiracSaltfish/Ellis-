#!/usr/bin/env python3
"""Bundle non-system Mach-O dylib dependencies next to a macOS app.

macdeployqt intentionally focuses on Qt. This helper handles the extra
protobuf/Abseil closure used by the optional IBKR bridge, rewrites references
to @rpath, and fails closed on missing libraries or basename collisions.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys


SYSTEM_PREFIXES = ("/System/Library/", "/usr/lib/")
DEPENDENCY_RE = re.compile(r"^\s*(.+?) \(compatibility version")


def run(*args: str) -> str:
    completed = subprocess.run(args, check=True, text=True, capture_output=True)
    return completed.stdout


def dependencies(binary: Path) -> list[str]:
    lines = run("/usr/bin/otool", "-L", str(binary)).splitlines()[1:]
    result: list[str] = []
    for line in lines:
        match = DEPENDENCY_RE.match(line)
        if match:
            result.append(match.group(1))
    # For dylibs/framework binaries, otool -L includes the object's own
    # LC_ID_DYLIB as the first item. It is metadata, not a load dependency.
    identity = subprocess.run(
        ["/usr/bin/otool", "-D", str(binary)],
        check=False, text=True, capture_output=True,
    )
    identity_lines = [line.strip() for line in identity.stdout.splitlines()[1:] if line.strip()]
    if identity_lines and result and result[0] == identity_lines[0]:
        result.pop(0)
    return result


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def find_dependency(reference: str, referring: Path, frameworks: Path,
                    search_dirs: list[Path]) -> Path | None:
    if reference.startswith(SYSTEM_PREFIXES):
        return None
    if reference.startswith("@rpath/"):
        relative = reference.removeprefix("@rpath/")
        bundled = frameworks / relative
        if bundled.exists():
            return bundled
        name = Path(relative).name
        for directory in search_dirs:
            candidate = directory / name
            if candidate.exists():
                return candidate.resolve()
        raise FileNotFoundError(f"cannot resolve {reference} needed by {referring}")
    if reference.startswith("@loader_path/"):
        candidate = referring.parent / reference.removeprefix("@loader_path/")
        if candidate.exists():
            return candidate.resolve()
        raise FileNotFoundError(f"cannot resolve {reference} needed by {referring}")
    if reference.startswith("@executable_path/"):
        # Existing app-local paths are already portable and are validated by
        # the caller's bundle dependency check.
        return None
    candidate = Path(reference)
    if candidate.is_absolute() and candidate.exists():
        return candidate.resolve()
    raise FileNotFoundError(f"cannot resolve {reference} needed by {referring}")


def ensure_writable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IWUSR)


def ensure_framework_rpath(binary: Path) -> None:
    load_commands = run("/usr/bin/otool", "-l", str(binary))
    if "path @executable_path/../Frameworks " not in load_commands:
        subprocess.run(
            ["/usr/bin/install_name_tool", "-add_rpath",
             "@executable_path/../Frameworks", str(binary)],
            check=True,
        )


def bundle(bundle_path: Path, roots: list[Path], search_dirs: list[Path]) -> int:
    frameworks = bundle_path / "Contents" / "Frameworks"
    frameworks.mkdir(parents=True, exist_ok=True)
    for root in roots:
        ensure_framework_rpath(root)

    queue = list(roots)
    visited: set[Path] = set()
    copied: dict[str, Path] = {}
    while queue:
        referring = queue.pop(0).resolve()
        if referring in visited:
            continue
        visited.add(referring)
        for reference in dependencies(referring):
            resolved = find_dependency(reference, referring, frameworks, search_dirs)
            if resolved is None:
                continue
            if str(resolved.resolve()).startswith(str(bundle_path.resolve())):
                if resolved.is_file():
                    queue.append(resolved)
                continue
            destination = frameworks / resolved.name
            previous = copied.get(destination.name)
            if previous is not None and digest(previous) != digest(resolved):
                raise RuntimeError(
                    f"dylib basename collision: {previous} and {resolved}"
                )
            if not destination.exists():
                shutil.copy2(resolved, destination)
                ensure_writable(destination)
            # macdeployqt may already have copied and rewritten this exact
            # basename. Its bytes then differ from the source because Mach-O
            # load commands changed; the source-to-source collision check
            # above is the reliable guard.
            ensure_writable(destination)
            subprocess.run(
                ["/usr/bin/install_name_tool", "-id",
                 f"@rpath/{destination.name}", str(destination)],
                check=True,
            )
            copied[destination.name] = resolved
            new_reference = f"@rpath/{destination.name}"
            if reference != new_reference:
                ensure_writable(referring)
                subprocess.run(
                    ["/usr/bin/install_name_tool", "-change", reference,
                     new_reference, str(referring)],
                    check=True,
                )
            queue.append(destination)

    # Validate the complete copied closure, not only direct executable edges.
    for binary in sorted(visited):
        for reference in dependencies(binary):
            if reference.startswith(SYSTEM_PREFIXES):
                continue
            if reference.startswith("@rpath/"):
                expected = frameworks / reference.removeprefix("@rpath/")
                if expected.exists():
                    continue
            if reference.startswith("@loader_path/"):
                expected = binary.parent / reference.removeprefix("@loader_path/")
                if expected.exists():
                    continue
            if reference.startswith("@executable_path/"):
                expected = bundle_path / "Contents" / "MacOS" / reference.removeprefix(
                    "@executable_path/"
                )
                if expected.exists():
                    continue
            raise RuntimeError(f"non-portable dependency remains in {binary}: {reference}")

    print(f"BUNDLED_MACHO_DEPS libraries={len(copied)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--binary", action="append", required=True, type=Path)
    parser.add_argument("--search-dir", action="append", default=[], type=Path)
    args = parser.parse_args()
    if not (args.bundle / "Contents" / "MacOS").is_dir():
        parser.error("--bundle is not a macOS app bundle")
    for binary in args.binary:
        if not binary.is_file():
            parser.error(f"binary does not exist: {binary}")
    search_dirs = [path.resolve() for path in args.search_dir if path.is_dir()]
    try:
        return bundle(args.bundle.resolve(), args.binary, search_dirs)
    except (FileNotFoundError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"bundle-macho-deps: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
