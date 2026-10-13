from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import urllib.error
import urllib.request
import zipfile
from contextlib import closing, contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

REPOSITORY = "Justify87/ladies-webapp"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
ASSET_PREFIX = "ladies-explorer-"
ASSET_SUFFIX = ".zip"
CURRENT_FILE = "current"
RELEASES_DIR = "releases"
DATA_DIR = "data"
STATE_DIR = ".state"
PID_FILE = "app.pid"
LIFECYCLE_LOCK_FILE = "lifecycle.lock"
MAX_RELEASES = 2
SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.I)


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
    required = [
        "ladies_explorer.py",
        "pyproject.toml",
        "uv.lock",
        "ladies",
        "tools/launcher_manager.py",
        "tools/launcher_core.py",
        "tools/launcher_manager.html",
    ]
    missing = [item for item in required if not (path / item).exists()]
    if missing:
        raise PortableError("Release ist unvollständig: " + ", ".join(missing))
    data = _manifest(path)
    if int(data.get("format_version") or 0) != 1:
        raise PortableError("Nicht unterstütztes Release-Manifestformat")
    app_version = str(data.get("app_version") or "")
    manifest_tag = str(data.get("tag") or "")
    tag = _tag(manifest_tag)
    if _tag(app_version) != tag:
        raise PortableError("App-Version und Tag im Release-Manifest widersprechen sich")
    if expected_tag and tag != _tag(expected_tag):
        raise PortableError(f"Manifest-Version {tag} passt nicht zu {expected_tag}")
    if str(data.get("entrypoint") or "") != "ladies_explorer.py":
        raise PortableError("Release-Entrypoint ist ungültig")
    if not str(data.get("commit") or "").strip():
        raise PortableError("Release-Commit fehlt im Manifest")
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
            normalized = info.filename.replace("\\", "/")
            member = PurePosixPath(normalized)
            first = member.parts[0] if member.parts else ""
            if (
                not info.filename
                or member.is_absolute()
                or ".." in member.parts
                or ":" in first
                or _is_symlink(info)
            ):
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


def _lifecycle_lock_path(root: Path) -> Path:
    return root / STATE_DIR / LIFECYCLE_LOCK_FILE


def _windows_process_running(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    ERROR_ACCESS_DENIED = 5

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    get_exit_code = kernel32.GetExitCodeProcess
    get_exit_code.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    get_exit_code.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    handle = open_process(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        exit_code = wintypes.DWORD()
        if not get_exit_code(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == STILL_ACTIVE
    finally:
        close_handle(handle)


def _process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_running(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="ascii").splitlines()[0].strip())
    except (OSError, ValueError, IndexError):
        return None


def active_pid(root: Path) -> int | None:
    path = _pid_path(root)
    pid = _read_pid(path)
    if pid is None:
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


@contextmanager
def lifecycle_lock(root: Path, operation: str) -> Iterator[None]:
    root = root.resolve()
    state = root / STATE_DIR
    state.mkdir(parents=True, exist_ok=True)
    path = _lifecycle_lock_path(root)
    payload = f"{os.getpid()}\n{operation}\n"

    for _attempt in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            owner = _read_pid(path)
            if owner is not None and _process_running(owner):
                raise PortableError(f"Eine andere Ladies-Explorer-Aktion läuft bereits (PID {owner}).")
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise PortableError("Veralteter Lifecycle-Lock konnte nicht entfernt werden") from exc
            continue
        try:
            with os.fdopen(fd, "w", encoding="ascii") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                yield
            finally:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            return
        except Exception:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            raise
    raise PortableError("Lifecycle-Lock konnte nicht übernommen werden")


def _data_paths(root: Path) -> tuple[Path, Path, Path]:
    data_root = root / DATA_DIR
    return data_root / "ladies.sqlite3", data_root / "ladies_data", data_root / "logs" / "ladies_explorer.log"


def _copy_tree(source: Path, destination: Path) -> None:
    source = source.resolve()
    destination = destination.resolve()
    patterns = [
        ".git", ".github", ".venv", ".runtime", ".state", "node_modules", "__pycache__",
        "tests", "dist", "releases", "data", "logs", "ladies.sqlite3", "ladies.sqlite3-wal",
        "ladies.sqlite3-shm", "ladies_data",
    ]
    try:
        relative_destination = destination.relative_to(source)
    except ValueError:
        pass
    else:
        if relative_destination.parts:
            patterns.append(relative_destination.parts[0])
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(*patterns))


def _copy_if_exists(source: Path, destination: Path) -> None:
    if not source.exists() or destination.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        shutil.copy2(source, destination)


def _portable_legacy_pid(legacy_root: Path) -> int | None:
    for candidate in (legacy_root / STATE_DIR / PID_FILE, legacy_root / PID_FILE):
        pid = _read_pid(candidate)
        if pid is not None and _process_running(pid):
            return pid
    return None


def _ensure_legacy_stopped(legacy_root: Path, confirmed_stopped: bool) -> None:
    pid = _portable_legacy_pid(legacy_root)
    if pid is not None:
        raise PortableError(f"Die alte Ladies-Explorer-Installation läuft noch (PID {pid}). Bitte zuerst beenden.")
    if not confirmed_stopped:
        raise PortableError(
            "Legacy-Migration benötigt die Bestätigung, dass die alte App beendet ist. "
            "Interaktiv bestätigen oder --legacy-stopped verwenden."
        )


def _backup_sqlite(source: Path, destination: Path) -> bool:
    if not source.is_file() or destination.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        source_uri = f"file:{source.as_posix()}?mode=ro"
        with closing(sqlite3.connect(source_uri, uri=True, timeout=30)) as source_db, closing(
            sqlite3.connect(str(destination))
        ) as target_db:
            source_db.backup(target_db)
    except sqlite3.Error as exc:
        try:
            destination.unlink()
        except OSError:
            pass
        raise PortableError(f"Legacy-Datenbank konnte nicht konsistent gesichert werden: {exc}") from exc
    return True


def migrate_legacy_data(legacy_root: Path, install_root: Path, *, confirmed_stopped: bool = False) -> list[str]:
    legacy_root = legacy_root.resolve()
    _ensure_legacy_stopped(legacy_root, confirmed_stopped)
    data_root = install_root / DATA_DIR
    data_root.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []

    if _backup_sqlite(legacy_root / "ladies.sqlite3", data_root / "ladies.sqlite3"):
        copied.append("ladies.sqlite3")

    for source, destination in (
        (legacy_root / "ladies_data", data_root / "ladies_data"),
        (legacy_root / "logs", data_root / "logs"),
    ):
        before = destination.exists()
        _copy_if_exists(source, destination)
        if source.exists() and not before and destination.exists():
            copied.append(source.name)
    return copied


def detect_legacy_root(source: Path, install_root: Path) -> Path | None:
    candidates: list[Path] = []
    try:
        siblings = list(source.resolve().parent.iterdir())
    except OSError:
        return None
    source_resolved = source.resolve()
    install_resolved = install_root.resolve()
    for candidate in siblings:
        if not candidate.is_dir():
            continue
        resolved = candidate.resolve()
        if resolved in {source_resolved, install_resolved}:
            continue
        if (candidate / "ladies.sqlite3").is_file() or (candidate / "ladies_data").is_dir():
            candidates.append(candidate)
    return candidates[0] if len(candidates) == 1 else None


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


def install(
    source: Path,
    root: Path,
    legacy_root: Path | None = None,
    *,
    legacy_stopped: bool = False,
) -> str:
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
    target_created = False
    activated = False
    try:
        _copy_tree(source, staging)
        validate_release_dir(staging, tag)
        os.replace(staging, target)
        target_created = True
        _copy_launchers(target, root)
        if legacy_root:
            migrate_legacy_data(legacy_root, root, confirmed_stopped=legacy_stopped)
        set_current(root, tag)
        activated = True
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        if target_created and not activated:
            shutil.rmtree(target, ignore_errors=True)
        raise
    return tag


def _db_schema_version(root: Path) -> int:
    db_path, _, _ = _data_paths(root)
    if not db_path.exists():
        return 0
    try:
        with closing(sqlite3.connect(str(db_path))) as connection:
            row = connection.execute("PRAGMA user_version").fetchone()
            return int((row or [0])[0] or 0)
    except sqlite3.Error as exc:
        raise PortableError(f"Datenbank-Schema konnte nicht gelesen werden: {exc}") from exc


def prune_releases(
    root: Path,
    keep: int = MAX_RELEASES,
    *,
    preserve: set[str] | None = None,
) -> None:
    current = read_current(root)
    releases: list[tuple[tuple[int, int, int], Path]] = []
    for path in (root / RELEASES_DIR).iterdir():
        if path.is_dir() and not path.name.startswith("."):
            try:
                key = _semver(path.name)
            except PortableError:
                continue
            releases.append((key, path))
    releases.sort(reverse=True)

    preserve_tags = {_tag(tag) for tag in (preserve or set())}
    preserve_tags.add(current)
    keep_paths = {
        root / RELEASES_DIR / tag
        for tag in preserve_tags
        if (root / RELEASES_DIR / tag).is_dir()
    }
    limit = max(1, keep)
    for _, path in releases:
        if len(keep_paths) >= limit:
            break
        keep_paths.add(path)
    for _, path in releases:
        if path not in keep_paths:
            shutil.rmtree(path, ignore_errors=True)


def _activate_staged(root: Path, staging: Path, tag: str, *, previous: str) -> None:
    target = root / RELEASES_DIR / tag
    if target.exists():
        shutil.rmtree(target)
    os.replace(staging, target)
    validate_release_dir(target, tag)
    _copy_launchers(target, root)
    set_current(root, tag)
    prune_releases(root, preserve={tag, previous})


def update_release(root: Path) -> str:
    root = root.resolve()
    with lifecycle_lock(root, "update"):
        _ensure_not_running(root)
        current = read_current(root)
        release = latest_release()
        latest = _tag(str(release.get("tag_name") or ""))
        if _semver(latest) <= _semver(current):
            return current

        asset = _asset_for_release(release)
        url = str(asset.get("browser_download_url") or "")
        if not url.startswith("https://"):
            raise PortableError("Release-Asset besitzt keine gültige Download-URL")

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
            manifest = validate_release_dir(staging, latest)
            target_commit = str(release.get("target_commitish") or "")
            if SHA_RE.fullmatch(target_commit) and str(manifest.get("commit") or "").lower() != target_commit.lower():
                raise PortableError("Release-Manifest gehört nicht zum veröffentlichten Release-Commit")
            _activate_staged(root, staging, latest, previous=current)
        finally:
            try:
                archive.unlink()
            except OSError:
                pass
            shutil.rmtree(staging, ignore_errors=True)
        return latest


def restore_release(root: Path, tag: str) -> None:
    """Restore the selected installed release including root launchers."""
    target = root / RELEASES_DIR / _tag(tag)
    validate_release_dir(target, tag)
    set_current(root, tag)
    _copy_launchers(target, root)


def _confirm_legacy_stopped(legacy_root: Path) -> bool:
    pid = _portable_legacy_pid(legacy_root)
    if pid is not None:
        raise PortableError(f"Die alte Ladies-Explorer-Installation läuft noch (PID {pid}). Bitte zuerst beenden.")
    if not os.isatty(0):
        raise PortableError(
            "Legacy-Migration ist nicht interaktiv. Alte App beenden und mit --legacy-stopped ausdrücklich bestätigen."
        )
    answer = input(
        "Die alte Ladies-Explorer-App muss vollständig beendet sein. Ist sie beendet? [j/N] "
    ).strip().lower()
    if answer not in {"j", "ja", "y", "yes"}:
        raise PortableError("Legacy-Migration abgebrochen")
    return True
