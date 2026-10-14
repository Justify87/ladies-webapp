#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INCLUDE_ROOT_FILES = {"ladies_explorer.py", "pyproject.toml", "uv.lock", "README.md"}
INCLUDE_PREFIXES = ("ladies/", "tools/", "packaging/")
EXCLUDED_PARTS = {"__pycache__", "node_modules", ".venv", ".runtime", "dist", "tests"}
FORBIDDEN_PREFIXES = ("ladies_data/", "logs/", ".git/", ".github/")
FORBIDDEN_NAMES = {"ladies.sqlite3", "ladies.sqlite3-wal", "ladies.sqlite3-shm"}
V13_RUNTIME_FILES = {
    "ladies/templates/index.html",
    "ladies/static/explorer/css/tokens.css",
    "ladies/static/explorer/css/base.css",
    "ladies/static/explorer/css/shell.css",
    "ladies/static/explorer/css/components.css",
    "ladies/static/explorer/css/screens.css",
    "ladies/static/explorer/js/bootstrap.js",
}


def _project_version(root: Path) -> str:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    version = str((data.get("project") or {}).get("version") or "")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise RuntimeError(f"Ungültige Projektversion: {version}")
    return version


def _schema_version(root: Path) -> int:
    text = (root / "ladies" / "database.py").read_text(encoding="utf-8")
    match = re.search(r"(?m)^CURRENT_SCHEMA_VERSION\s*=\s*(\d+)\s*$", text)
    if not match:
        raise RuntimeError("CURRENT_SCHEMA_VERSION konnte nicht ermittelt werden")
    return int(match.group(1))


def _commit(root: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def _tracked_files(root: Path) -> list[Path]:
    output = subprocess.check_output(["git", "ls-files"], cwd=root, text=True)
    result: list[Path] = []
    for raw in output.splitlines():
        path = Path(raw)
        posix = path.as_posix()
        if path.name in FORBIDDEN_NAMES or any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        if any(posix.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
            continue
        if posix in INCLUDE_ROOT_FILES or posix.startswith(INCLUDE_PREFIXES):
            result.append(path)
    return sorted(result, key=lambda item: item.as_posix())


def _validate_file_set(files: list[Path]) -> None:
    names = {path.as_posix() for path in files}
    required = {
        "ladies_explorer.py",
        "pyproject.toml",
        "uv.lock",
        "README.md",
        "tools/launcher_manager.py",
        "tools/launcher_core.py",
        "tools/launcher_manager.html",
        "tools/build_release.py",
        "packaging/install.command",
        "packaging/start.command",
        "packaging/update.command",
        "packaging/install.cmd",
        "packaging/start.cmd",
        "packaging/update.cmd",
        *V13_RUNTIME_FILES,
    }
    missing = sorted(required - names)
    if missing:
        raise RuntimeError("Release-Dateien fehlen: " + ", ".join(missing))
    if "tools/portable_manager.py" in names:
        raise RuntimeError("Veralteter Portable Manager darf nicht mehr ausgeliefert werden")
    forbidden = [
        name for name in names
        if name in FORBIDDEN_NAMES or any(name.startswith(prefix) for prefix in FORBIDDEN_PREFIXES)
    ]
    if forbidden:
        raise RuntimeError("Persistente Dateien im Release-Paket: " + ", ".join(sorted(forbidden)))


def _validate_archive(archive: Path, files: list[Path]) -> None:
    expected = {path.as_posix() for path in files} | {"release-manifest.json"}
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())

    missing = sorted(expected - names)
    unexpected = sorted(names - expected)
    if missing:
        raise RuntimeError("Dateien fehlen im Release-Archiv: " + ", ".join(missing))
    if unexpected:
        raise RuntimeError("Unerwartete Dateien im Release-Archiv: " + ", ".join(unexpected))

    missing_v13 = sorted(V13_RUNTIME_FILES - names)
    if missing_v13:
        raise RuntimeError("V13-Runtime fehlt im Release-Archiv: " + ", ".join(missing_v13))

    tracked_v13 = {path.as_posix() for path in files if path.as_posix().startswith("ladies/static/explorer/")}
    archived_v13 = {name for name in names if name.startswith("ladies/static/explorer/")}
    if tracked_v13 != archived_v13:
        missing_nested = sorted(tracked_v13 - archived_v13)
        extra_nested = sorted(archived_v13 - tracked_v13)
        details: list[str] = []
        if missing_nested:
            details.append("fehlend: " + ", ".join(missing_nested))
        if extra_nested:
            details.append("unerwartet: " + ", ".join(extra_nested))
        raise RuntimeError("V13-Asset-Set im Release-Archiv stimmt nicht: " + "; ".join(details))


def build(output_dir: Path, *, commit: str | None = None, root: Path = ROOT) -> Path:
    root = root.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    version = _project_version(root)
    tag = f"v{version}"
    files = _tracked_files(root)
    _validate_file_set(files)
    manifest = {
        "format_version": 1,
        "app_version": version,
        "tag": tag,
        "commit": commit or _commit(root),
        "entrypoint": "ladies_explorer.py",
        "db_schema_version": _schema_version(root),
    }
    archive = output_dir / f"ladies-explorer-{tag}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for relative in files:
            bundle.write(root / relative, relative.as_posix())
        bundle.writestr(
            "release-manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
    _validate_archive(archive, files)
    return archive


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Ladies Explorer portable release ZIP")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--commit")
    args = parser.parse_args(argv)
    archive = build(args.output, commit=args.commit)
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
