# -*- coding: utf-8 -*-
"""Build a source-only, ready-to-drop-in plugin zip for distribution.

The zip root contains one folder per plugin (named exactly as UE expects under
<Project>/Plugins/). No build outputs, VCS metadata, caches, machine-generated
IDE declarations or sample projects are included. Third-party vendored sources
that are compile-time requirements (UnLuaNova's Lua / Luau submodules) are
copied as real files, with their .ubtignore markers preserved.

NoRedist: the dev-only UEAssetLensMCP engine module, any official
ModelContextProtocol plugin artifacts and a descriptor enabling that optional
official plugin must never ship. The development UEAssetLens.uplugin is never
modified; a NoRedist-clean release descriptor is generated in memory from it
and written into the archive instead.

Usage:
    python Tools/package_plugin.py                  # writes dist/<archive>.zip
    python Tools/package_plugin.py --out-dir DIR
    python Tools/package_plugin.py --verify-only ZIP_OR_DESCRIPTOR

--verify-only is deterministic and offline: it inspects a zip archive or a
standalone .uplugin descriptor, without network, clock or random identifiers.

Requires Python 3.8+. No third-party dependencies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DIST = REPO / "dist"

# Experimental + NoRedist: dev-only engine module and the optional official
# plugin are development aids only; neither may appear in a release archive.
DEV_ONLY_MODULE = "UEAssetLensMCP"
NOREDIST_OFFICIAL_PLUGIN = "ModelContextProtocol"

# Directory names excluded wherever they appear.
SKIP_DIR_NAMES = {
    ".git", ".github", ".vs", ".vscode", ".idea", ".clang-format-cache",
    "Binaries", "Intermediate", "Saved", "DerivedDataCache", "Build",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".workbuddy", ".ual", "_site", "site", "dist", ".venv", "venv",
    "TestResults", "Tests", "TestsData", "evidence", "obj",
}
# Exact file names excluded. VCS metadata only - .ubtignore is intentionally
# kept because UBT needs it to avoid compiling lua.c / luau CLI directly.
SKIP_FILE_NAMES = {".git", ".gitmodules", ".gitattributes", ".gitignore",
                   ".DS_Store", "Thumbs.db"}
SKIP_SUFFIXES = (".pyc", ".pyo", ".duckdb", ".duckdb.wal", ".parquet",
                 ".pdb", ".exp", ".lib", ".obj", ".ilk", ".sln", ".slnx",
                 ".suo", ".user", ".code-workspace", ".tmp", ".log")
# Path prefixes (relative to each plugin source dir) excluded from the archive.
# NOTE: Content/IntelliSense holds hand-written annotations and IS kept; only
# the machine-generated top-level IntelliSense/ is dropped.
# The engine MCP module is a development-only integration with the optional
# official plugin, so its whole source tree is excluded from release packages.
SKIP_REL_PREFIXES = ["Source/UEAssetLensMCP/"]

# Extra root-level files copied next to the plugin folders (licences, docs).
ROOT_FILES = [('LICENSE', 'LICENSE'), ('NOTICE', 'NOTICE'), ('README.md', 'README.md')]
# Each: (source dir relative to repo root, plugin folder name inside the zip).
PLUGIN_DIRS = [('.', 'UEAssetLens')]
DESCRIPTOR = 'UEAssetLens.uplugin'
RELEASE_DESCRIPTOR_PATHS = {f"{d}/{DESCRIPTOR}" for _, d in PLUGIN_DIRS}
RELEASE_NAME = 'UEAssetLens'
REQUIRED_MARKERS = []

# Fixed zip metadata keeps release archives reproducible: no file mtimes,
# no host-dependent paths or timestamps leak into the archive.
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def _norm(name: str) -> str:
    """Normalize an archive/relative path to forward slashes."""
    return name.replace("\\", "/")


def _segments(name: str) -> list:
    return [part.lower() for part in _norm(name).split("/") if part]


def _is_noredist_path(name: str) -> bool:
    """True for the dev-only engine MCP module or official plugin artifacts."""
    return any(
        NOREDIST_OFFICIAL_PLUGIN.lower() in part
        or part == DEV_ONLY_MODULE.lower()
        for part in _segments(name)
    )


def _skip_rel(rel: Path) -> bool:
    posix = rel.as_posix()
    return any(posix == p.rstrip("/") or posix.startswith(p)
               for p in SKIP_REL_PREFIXES)


def iter_files(src: Path, dest: Path):
    for root, dirnames, filenames in os.walk(src):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIR_NAMES and not _is_noredist_path(d)
        )
        rel_root = Path(root).relative_to(src)
        for filename in sorted(filenames):
            if filename in SKIP_FILE_NAMES or filename.endswith(SKIP_SUFFIXES):
                continue
            rel = rel_root / filename
            if (_skip_rel(rel_root) or _skip_rel(rel)
                    or _is_noredist_path(rel.as_posix())):
                continue
            f = Path(root) / filename
            if f.is_symlink():
                continue
            yield f, dest / rel


def collect() -> list:
    entries = []
    for src_rel, dest_name in PLUGIN_DIRS:
        src = REPO / src_rel
        if not src.is_dir():
            raise SystemExit(f"ERROR: plugin source dir not found: {src}")
        entries.extend(iter_files(src, Path(dest_name)))
    for src_rel, dest_name in ROOT_FILES:
        src = REPO / src_rel
        if not src.is_file():
            raise SystemExit(f"ERROR: required release file not found: {src}")
        entries.append((src, Path(dest_name)))
    return entries


def version_name() -> str:
    with (REPO / DESCRIPTOR).open("r", encoding="utf-8-sig") as fh:
        return json.load(fh)["VersionName"]


def release_descriptor_bytes(src: Path) -> bytes:
    """Build a NoRedist-clean descriptor without touching the dev .uplugin."""
    data = json.loads(src.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise SystemExit("ERROR: plugin descriptor is not a JSON object")
    modules = data.get("Modules")
    if isinstance(modules, list):
        data["Modules"] = [
            module for module in modules
            if not (isinstance(module, dict)
                    and str(module.get("Name", "")).lower() == DEV_ONLY_MODULE.lower())
        ]
    plugins = data.get("Plugins")
    if isinstance(plugins, list):
        data["Plugins"] = [
            plugin for plugin in plugins
            if not (isinstance(plugin, dict)
                    and str(plugin.get("Name", "")).lower()
                    == NOREDIST_OFFICIAL_PLUGIN.lower())
        ]
    payload = json.dumps(data, indent="\t", ensure_ascii=False) + "\n"
    return payload.encode("utf-8")


def _iter_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)


def descriptor_violations(text: str, label: str) -> list:
    """Reject a descriptor that enables the official plugin or the dev module."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        return [f"{label}: descriptor is not valid JSON ({type(exc).__name__})"]
    violations = []
    for value in _iter_strings(data):
        lowered = value.lower()
        if NOREDIST_OFFICIAL_PLUGIN.lower() in lowered:
            violations.append(
                f"{label}: descriptor references official NoRedist plugin "
                f"{NOREDIST_OFFICIAL_PLUGIN}"
            )
        if DEV_ONLY_MODULE.lower() in lowered:
            violations.append(
                f"{label}: descriptor references dev-only module {DEV_ONLY_MODULE}"
            )
    return sorted(set(violations))


def validate(names) -> None:
    name_set = set(names)
    for _, dest_name in PLUGIN_DIRS:
        leaf = Path(dest_name).name
        descriptor = f"{dest_name}/{leaf}.uplugin"
        if descriptor not in name_set:
            raise SystemExit(f"ERROR: descriptor missing from archive: {descriptor}")
    for marker in REQUIRED_MARKERS:
        if marker not in name_set:
            raise SystemExit(f"ERROR: required vendored source missing: {marker}")
    bad = [n for n in names
           if n.endswith("/.git") or "/.git/" in f"/{n}"
           or "/Binaries/" in f"/{n}" or "/Intermediate/" in f"/{n}"
           or "/Saved/" in f"/{n}" or "/__pycache__/" in f"/{n}"
           or "/IntelliSense/" in f"/{n}" and "/Content/IntelliSense/" not in f"/{n}"]
    if bad:
        raise SystemExit("ERROR: forbidden paths in archive:\n  "
                         + "\n  ".join(bad[:10]))
    noredist = [n for n in names if _is_noredist_path(n)]
    if noredist:
        raise SystemExit("ERROR: NoRedist paths in archive:\n  "
                         + "\n  ".join(noredist[:10]))


def verify_only(path: Path) -> int:
    """Offline, deterministic NoRedist check of an archive or a descriptor."""
    violations = []
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            validate(names)
            for name in names:
                if _norm(name).endswith(".uplugin"):
                    try:
                        text = zf.read(name).decode("utf-8-sig")
                    except (KeyError, UnicodeDecodeError):
                        violations.append(f"{name}: descriptor is not UTF-8 text")
                        continue
                    violations.extend(descriptor_violations(text, name))
            count = len(names)
    else:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise SystemExit(
                f"ERROR: cannot read {path.name}: {type(exc).__name__}"
            )
        violations.extend(descriptor_violations(text, path.name))
        count = 1
    if violations:
        raise SystemExit("ERROR: NoRedist violations:\n  "
                         + "\n  ".join(sorted(set(violations))[:10]))
    return count


def _write_entry(zf: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=ZIP_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o644 << 16
    zf.writestr(info, data)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default=str(DIST))
    ap.add_argument("--verify-only", metavar="ZIP_OR_DESCRIPTOR")
    args = ap.parse_args()

    if args.verify_only:
        z = Path(args.verify_only)
        count = verify_only(z)
        digest = hashlib.sha256(z.read_bytes()).hexdigest()
        print(f"OK {z.name}: {count} entries, sha256={digest}")
        return 0

    entries = collect()
    validate([p.as_posix() for _, p in entries])
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    version = version_name()
    archive = out_dir / f"{RELEASE_NAME}-{version}-src.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for src, dest in entries:
            name = dest.as_posix()
            if name in RELEASE_DESCRIPTOR_PATHS:
                _write_entry(zf, name, release_descriptor_bytes(src))
            else:
                _write_entry(zf, name, src.read_bytes())

    verify_only(archive)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (out_dir / f"{archive.name}.sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8")
    print(f"OK  {archive}")
    print(f"    files={len(entries)}  size={archive.stat().st_size/1024/1024:.2f} MiB")
    print(f"    sha256={digest}")
    print("    plugins=" + ", ".join(sorted({d for _, d in PLUGIN_DIRS})))
    return 0


if __name__ == "__main__":
    sys.exit(main())
