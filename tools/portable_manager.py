#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

REPOSITORY = "Justify87/ladies-webapp"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
ASSET_PREFIX = "ladies-explorer-v"
ASSET_SUFFIX = ".zip"
CURRENT_FILE = "current"
RELEASES_DIR = "releases"
DATA_DIR = "data"
STATE_DIR = ".state"
PID_FILE = "app.pid"
MAX_RELEASES = 2
SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


class PortableError(RuntimeError):
    pass


def _semver(value: str) -> tuple[int, int, int]:
    match = SEMVER_RE.fullmatch(str(value).strip())
    if not match:
        raise PortableError(f"Ungültige Version: {value}")
    return tuple(int(part) for part in match.groups())


def _tag(value: str) -> str:
    major, minor, patch = _semver(value)
    return f"v{major}.{minor}.{patch}"


def infer_install_root(script: Path | None = None) -> Path:
    path = (script or Path(__file__)).resolve()
    for candidate in (path.parent, *path.parents):
        if (candidate / RELEASES_DIR).is_dir() and (candidate / CURRENT_FILE).exists():
            return candidate
    # Installed manager: <root>/releases/<version>/tools/portable_manager.py
    try:
        return path.parents[2]
    except IndexError as exc:
        raise PortableError("Installationsverzeichnis konnte nicht bestimmt werden") from exc


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            Path(temporary).unlink()
        except FileNotFoundError:
            pass


def read_current(root: Path) -> str:
    try:
        value = (root / CURRENT_FILE).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise PortableError("Keine aktive Ladies-Explorer-Version gefunden") from exc
    return _tag(value)


def set_current(root: Path, version: str) -> None:
    tag = _tag(version)
    release_dir = root / RELEASES_DIR / tag
    if not release_dir.is_dir():
        raise PortableError(f"Release {tag} ist nicht installiert")
    _atomic_write(root / CURRENT_FILE, tag + "\n")


def _manifest(path: Path) -> dict[str, Any]:
    manifest_path = path / "release-manifest.json"
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise PortableError(f"Ungültiges Release-Manifest: {manifest_path}") from exc
    if not isinstance(data, dict):
        raise PortableError("Release-Manifest muss ein JSON-Objekt sein")
    return data


def validate_release_dir(path: Path, expected_tag: str | None = None) -> dict[str, Any]:
    required = ["ladies_explorer.py", "pyproject.toml", "uv.lock", "ladies", "tools/portable_manager.py"]
    missing = [item for item in required if not (path / item).exists()]
    if missing:
        raise PortableError("Release ist unvollständig: " + ", ".join(missing))
    data = _manifest(path)
    if int(data.get("format_version") or 0) != 1:
        raise PortableError("Nicht unterstütztes Release-Manifestformat")
    tag = _tag(str(data.get("tag") or data.get("app_version") or ""))
    if expected_tag and tag != _tag(expected_tag):
        raise PortableError(f"Manifest-Version {tag} passt nicht zu {expected_tag}")
    if str(data.get("entrypoint") or "") != "ladies_explorer.py":
        raise PortableError("Release-Entrypoint ist ungültig")
    try:
        schema = int(data.get("db_schema_version"))
    except (TypeError, ValueError) as exc:
        raise PortableError("DB-Schema-Version fehlt im Release-Manifest") from exc
    if schema < 0:
        raise PortableError("DB-Schema-Version ist ungültig")
    return data


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return (mode & 0o170000) == 0o120000


def safe_extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            member = PurePosixPath(info.filename)
            if not info.filename or member.is_absolute() or ".." in member.parts or _is_symlink(info):
                raise PortableError(f"Unsicherer ZIP-Eintrag: {info.filename}")
        bundle.extractall(destination)


def _request_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "LadiesExplorerUpdater/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
        raise PortableError(f"GitHub konnte nicht abgefragt werden: {exc}") from exc
    if not isinstance(payload, dict):
        raise PortableError("Unerwartete Antwort der GitHub Release API")
    return payload


def latest_release() -> dict[str, Any]:
    release = _request_json(LATEST_RELEASE_URL)
    if release.get("draft") or release.get("prerelease"):
        raise PortableError("Latest-Release ist kein stabiler Release")
    _tag(str(release.get("tag_name") or ""))
    return release


def _asset_for_release(release: dict[str, Any]) -> dict[str, Any]:
    tag = _tag(str(release.get("tag_name") or ""))
    expected_name = f"{ASSET_PREFIX}{tag}{ASSET_SUFFIX}"
    for asset in release.get("assets") or []:
        if isinstance(asset, dict) and asset.get("name") == expected_name:
            return asset
    raise PortableError(f"Release-Asset fehlt: {expected_name}")


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "LadiesExplorerUpdater/1"})
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
    except (OSError, urllib.error.URLError) as exc:
        try:
            destination.unlink()
        except FileNotFoundError:
            pass
        raise PortableError(f"Release-Download fehlgeschlagen: {exc}") from exc


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_asset_digest(path: Path, asset: dict[str, Any]) -> None:
    raw = str(asset.get("digest") or "")
    if not raw.startswith("sha256:"):
        raise PortableError("GitHub liefert keinen SHA-256-Digest für das Release-Asset")
    expected = raw.split(":", 1)[1].strip().lower()
    actual = _sha256(path)
    if not expected or actual != expected:
        raise PortableError("SHA-256-Prüfung des Release-Assets fehlgeschlagen")


def _pid_path(root: Path) -> Path:
    return root / STATE_DIR / PID_FILE


def _process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def active_pid(root: Path) -> int | None:
    path = _pid_path(root)
    try:
        pid = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None
    if _process_running(pid):
        return pid
    try:
        path.unlink()
    except OSError:
        pass
    return None


def _ensure_not_running(root: Path) -> None:
    pid = active_pid(root)
    if pid is not None:
        raise PortableError(f"Ladies Explorer läuft noch (PID {pid}). Bitte zuerst beenden.")


def _data_paths(root: Path) -> tuple[Path, Path, Path]:
    data_root = root / DATA_DIR
    return data_root / "ladies.sqlite3", data_root / "ladies_data", data_root / "logs" / "ladies_explorer.log"


def start(root: Path, extra_args: list[str] | None = None) -> int:
    if active_pid(root) is not None:
        raise PortableError("Ladies Explorer läuft bereits")
    current = read_current(root)
    release_dir = root / RELEASES_DIR / current
    validate_release_dir(release_dir, current)
    db_path, data_dir, log_path = _data_paths(root)
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(release_dir / "ladies_explorer.py"),
        "--host", "0.0.0.0",
        "--db", str(db_path),
        "--data-dir", str(data_dir),
        "--log", str(log_path),
        *(extra_args or []),
    ]
    process = subprocess.Popen(command, cwd=release_dir)
    _atomic_write(_pid_path(root), f"{process.pid}\n")
    try:
        return int(process.wait())
    finally:
        try:
            _pid_path(root).unlink()
        except OSError:
            pass


def _copy_tree(source: Path, destination: Path) -> None:
    ignored = shutil.ignore_patterns(
        ".git", ".github", ".venv", ".runtime", "node_modules", "__pycache__",
        "tests", "dist", "logs", "ladies.sqlite3", "ladies.sqlite3-wal", "ladies.sqlite3-shm", "ladies_data",
    )
    shutil.copytree(source, destination, ignore=ignored)


def _copy_if_exists(source: Path, destination: Path) -> None:
    if not source.exists() or destination.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        shutil.copy2(source, destination)


def migrate_legacy_data(legacy_root: Path, install_root: Path) -> list[str]:
    legacy_root = legacy_root.resolve()
    data_root = install_root / DATA_DIR
    data_root.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    mapping = [
        (legacy_root / "ladies.sqlite3", data_root / "ladies.sqlite3"),
        (legacy_root / "ladies.sqlite3-wal", data_root / "ladies.sqlite3-wal"),
        (legacy_root / "ladies.sqlite3-shm", data_root / "ladies.sqlite3-shm"),
        (legacy_root / "ladies_data", data_root / "ladies_data"),
        (legacy_root / "logs", data_root / "logs"),
    ]
    for source, destination in mapping:
        before = destination.exists()
        _copy_if_exists(source, destination)
        if source.exists() and not before and destination.exists():
            copied.append(source.name)
    return copied


def _copy_launchers(source_release: Path, root: Path) -> None:
    packaging = source_release / "packaging"
    for name in ("start.command", "update.command", "start.cmd", "update.cmd"):
        source = packaging / name
        if source.is_file():
            target = root / name
            shutil.copy2(source, target)
            if target.suffix == ".command":
                try:
                    target.chmod(target.stat().st_mode | 0o111)
                except OSError:
                    pass


def install(source: Path, root: Path, legacy_root: Path | None = None) -> str:
    source = source.resolve()
    root = root.resolve()
    manifest = validate_release_dir(source)
    tag = _tag(str(manifest["tag"]))
    target = root / RELEASES_DIR / tag
    if target.exists():
        raise PortableError(f"Release {tag} ist bereits installiert: {target}")
    root.mkdir(parents=True, exist_ok=True)
    (root / RELEASES_DIR).mkdir(parents=True, exist_ok=True)
    (root / DATA_DIR).mkdir(parents=True, exist_ok=True)
    staging = root / RELEASES_DIR / f".staging-{tag}"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        _copy_tree(source, staging)
        validate_release_dir(staging, tag)
        os.replace(staging, target)
        _copy_launchers(target, root)
        if legacy_root:
            migrate_legacy_data(legacy_root, root)
        set_current(root, tag)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return tag


def _db_schema_version(root: Path) -> int:
    db_path, _, _ = _data_paths(root)
    if not db_path.exists():
        return 0
    try:
        with sqlite3.connect(str(db_path)) as connection:
            row = connection.execute("PRAGMA user_version").fetchone()
            return int((row or [0])[0] or 0)
    except sqlite3.Error as exc:
        raise PortableError(f"Datenbank-Schema konnte nicht gelesen werden: {exc}") from exc


def _release_schema(root: Path, tag: str) -> int:
    data = validate_release_dir(root / RELEASES_DIR / _tag(tag), tag)
    return int(data["db_schema_version"])


def prune_releases(root: Path, keep: int = MAX_RELEASES) -> None:
    current = read_current(root)
    releases = []
    for path in (root / RELEASES_DIR).iterdir():
        if path.is_dir() and not path.name.startswith("."):
            try:
                key = _semver(path.name)
            except PortableError:
                continue
            releases.append((key, path))
    releases.sort(reverse=True)
    keep_paths = {path for _, path in releases[:max(1, keep)]}
    keep_paths.add(root / RELEASES_DIR / current)
    for _, path in releases:
        if path not in keep_paths:
            shutil.rmtree(path, ignore_errors=True)


def _activate_staged(root: Path, staging: Path, tag: str) -> None:
    target = root / RELEASES_DIR / tag
    if target.exists():
        shutil.rmtree(staging, ignore_errors=True)
    else:
        os.replace(staging, target)
    validate_release_dir(target, tag)
    _copy_launchers(target, root)
    set_current(root, tag)
    prune_releases(root)


def update(root: Path) -> str:
    root = root.resolve()
    _ensure_not_running(root)
    current = read_current(root)
    release = latest_release()
    latest = _tag(str(release.get("tag_name") or ""))
    if _semver(latest) <= _semver(current):
        print(f"Ladies Explorer {current} ist bereits aktuell.")
        return current

    asset = _asset_for_release(release)
    url = str(asset.get("browser_download_url") or "")
    if not url.startswith("https://"):
        raise PortableError("Release-Asset besitzt keine gültige Download-URL")

    print(f"Update verfügbar: {current} → {latest}")
    notes = str(release.get("body") or "").strip()
    if notes:
        print("\n" + notes + "\n")

    state = root / STATE_DIR
    downloads = state / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    archive = downloads / str(asset["name"])
    staging = root / RELEASES_DIR / f".staging-{latest}"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        _download(url, archive)
        verify_asset_digest(archive, asset)
        safe_extract(archive, staging)
        validate_release_dir(staging, latest)
        _activate_staged(root, staging, latest)
    finally:
        try:
            archive.unlink()
        except OSError:
            pass
        shutil.rmtree(staging, ignore_errors=True)
    print(f"✓ {latest} installiert. Persistente Daten wurden nicht verändert.")
    return latest


def rollback(root: Path) -> str:
    root = root.resolve()
    _ensure_not_running(root)
    current = read_current(root)
    candidates: list[tuple[tuple[int, int, int], str]] = []
    for path in (root / RELEASES_DIR).iterdir():
        if not path.is_dir() or path.name == current or path.name.startswith("."):
            continue
        try:
            candidates.append((_semver(path.name), _tag(path.name)))
        except PortableError:
            continue
    if not candidates:
        raise PortableError("Keine vorherige Release-Version für Rollback vorhanden")
    candidates.sort(reverse=True)
    target = candidates[0][1]
    db_schema = _db_schema_version(root)
    target_schema = _release_schema(root, target)
    if db_schema > target_schema:
        raise PortableError(
            f"Rollback auf {target} blockiert: Datenbank-Schema {db_schema} ist neuer als Release-Schema {target_schema}."
        )
    set_current(root, target)
    _copy_launchers(root / RELEASES_DIR / target, root)
    print(f"✓ {target} ist wieder aktiv.")
    return target


def status(root: Path) -> dict[str, Any]:
    root = root.resolve()
    current = read_current(root)
    db_path, data_dir, log_path = _data_paths(root)
    result = {
        "root": str(root),
        "current": current,
        "db": str(db_path),
        "data_dir": str(data_dir),
        "log": str(log_path),
        "running_pid": active_pid(root),
        "db_schema_version": _db_schema_version(root),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ladies Explorer Portable Release Manager")
    sub = parser.add_subparsers(dest="command", required=True)

    install_cmd = sub.add_parser("install", help="Portable Installation anlegen")
    install_cmd.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    install_cmd.add_argument("--root", type=Path)
    install_cmd.add_argument("--legacy-root", type=Path)

    for name in ("start", "update", "rollback", "status"):
        command = sub.add_parser(name)
        command.add_argument("--root", type=Path)
        if name == "start":
            command.add_argument("args", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            source = args.source.resolve()
            root = (args.root or (source.parent / "LadiesExplorer")).resolve()
            tag = install(source, root, args.legacy_root)
            print(f"✓ Ladies Explorer {tag} installiert: {root}")
            return 0
        root = (args.root or infer_install_root()).resolve()
        if args.command == "start":
            return start(root, args.args)
        if args.command == "update":
            update(root)
            return 0
        if args.command == "rollback":
            rollback(root)
            return 0
        if args.command == "status":
            status(root)
            return 0
    except PortableError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
