from __future__ import annotations

import logging
import tempfile
import time
import uuid
from pathlib import Path

from flask import Flask, Response, g, jsonify, render_template, request, send_from_directory, stream_with_context

from .core.repositories import RepositoryBundle
from .core.services import ProfileService, SearchService
from .database import Database
from .errors import ExternalServiceError, NotFoundError, ValidationError, install_error_handlers
from .logging_utils import redact_text, sanitize_log_value
from .media import ArchiveManager
from .parser import ADVERT_ID_RE
from .request_parsing import read_int
from .source import BatchRateLimiter, FixedRateLimiter, build_session, http_get
from .utils import clean_text, safe_media_url, safe_profile_url

LOGGER = logging.getLogger("ladies_explorer")
MEDIA_PROXY_LIMIT = 32 * 1024 * 1024


def create_app(
    *,
    db_path: str | Path,
    data_dir: str | Path,
    default_city: str = "leipzig",
    default_radius: int = 20,
    profile_delay: float = 2.0,
) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    install_error_handlers(app)
    database = Database(db_path)
    archive = ArchiveManager(data_dir)
    repos = RepositoryBundle.from_dependencies(database, archive)
    http = build_session()
    limiter = FixedRateLimiter(profile_delay)
    status_limiter = BatchRateLimiter(batch_size=3, pause_seconds=0.8)
    search_service = SearchService(repos.search, http, default_city=default_city, default_radius=default_radius)
    profile_service = ProfileService(
        repos.persons,
        http,
        limiter,
        classification_repository=repos.search,
    )
    app.extensions["ladies_database"] = database
    app.extensions["ladies_archive"] = archive
    app.extensions["ladies_repositories"] = repos
    app.extensions["ladies_http"] = http
    LOGGER.info(
        "Web app initialized db=%s data_dir=%s default_city=%s default_radius=%s",
        Path(db_path).resolve(),
        Path(data_dir).resolve(),
        default_city,
        default_radius,
    )

    @app.before_request
    def log_request_start():
        g.request_started = time.monotonic()
        g.request_id = uuid.uuid4().hex[:10]
        LOGGER.debug("REQ id=%s method=%s path=%s", g.request_id, request.method, request.path)

    @app.after_request
    def finalize_response(response):
        elapsed_ms = (time.monotonic() - getattr(g, "request_started", time.monotonic())) * 1000
        level = logging.INFO if response.status_code < 400 else logging.WARNING
        LOGGER.log(
            level,
            "RESP id=%s method=%s path=%s status=%s elapsed_ms=%.1f",
            getattr(g, "request_id", "-"), request.method, request.path, response.status_code, elapsed_ms,
        )
        response.headers.setdefault("X-Request-ID", getattr(g, "request_id", "-"))
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'self'")
        return response

    @app.teardown_request
    def log_request_teardown(exc):
        if exc is not None:
            LOGGER.exception("REQUEST exception id=%s path=%s", getattr(g, "request_id", "-"), request.path, exc_info=exc)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/archive/<path:relative_path>")
    def archived_file(relative_path: str):
        return send_from_directory(str(Path(data_dir).resolve()), relative_path)

    @app.get("/api/dashboard")
    def api_dashboard():
        return jsonify(repos.history.dashboard())

    @app.get("/api/history/relistings")
    def api_history_relistings():
        limit = read_int(request.args, "limit", default=100, minimum=1, maximum=500)
        return jsonify(items=repos.history.relistings(limit))

    @app.get("/api/history/changes")
    def api_history_changes():
        limit = read_int(request.args, "limit", default=100, minimum=1, maximum=500)
        return jsonify(items=repos.history.changes(limit))

    @app.get("/api/recent-searches")
    def api_recent_searches():
        limit = read_int(request.args, "limit", default=8, minimum=1, maximum=100)
        return jsonify(items=repos.search.recent_searches(limit))

    @app.get("/api/storage")
    def api_storage():
        return jsonify(repos.archive.storage_info())

    @app.get("/api/archive/media")
    def api_archive_media():
        kind = clean_text(str(request.args.get("kind") or "")).upper() or None
        if kind not in {None, "IMAGE", "VIDEO"}:
            raise ValidationError("kind muss IMAGE oder VIDEO sein")
        limit = read_int(request.args, "limit", default=80, minimum=1, maximum=500)
        return jsonify(items=repos.archive.items(kind=kind, limit=limit))

    @app.delete("/api/visual-analysis")
    def api_clear_visual_analysis():
        return jsonify(deleted=repos.archive.clear_visual_analyses())

    @app.route("/api/settings", methods=["GET", "POST"])
    def api_settings():
        if request.method == "POST":
            payload = request.get_json(silent=True) or {}
            key = str(payload.get("key") or "")
            if not key:
                raise ValidationError("key fehlt")
            try:
                return jsonify(settings=repos.settings.set(key, payload.get("value")))
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
        return jsonify(settings=repos.settings.get_all())

    @app.post("/api/search")
    def api_search():
        return jsonify(**search_service.execute(request.get_json(silent=True) or {}))

    @app.get("/api/profile/<advert_id>")
    def api_profile(advert_id: str):
        return jsonify(**profile_service.load(advert_id))

    @app.get("/api/profile-classification/<advert_id>")
    def api_profile_classification(advert_id: str):
        return jsonify(**profile_service.classify_business_profile(advert_id))

    @app.get("/api/snapshot/<int:snapshot_id>")
    def api_snapshot(snapshot_id: int):
        try:
            return jsonify(repos.history.snapshot_detail(snapshot_id))
        except KeyError as exc:
            raise NotFoundError("Snapshot nicht gefunden") from exc

    @app.get("/api/listing/<advert_id>")
    def api_listing(advert_id: str):
        try:
            return jsonify(repos.persons.listing_detail(advert_id))
        except KeyError as exc:
            raise NotFoundError("Anzeige nicht gefunden") from exc

    @app.get("/api/person/<int:person_id>")
    def api_person(person_id: int):
        try:
            return jsonify(repos.persons.person_detail(person_id))
        except KeyError as exc:
            raise NotFoundError("Person nicht gefunden") from exc

    @app.post("/api/person/<int:person_id>/state")
    def api_person_state(person_id: int):
        try:
            return jsonify(user_state=repos.persons.set_user_state(person_id, request.get_json(silent=True) or {}))
        except KeyError as exc:
            raise NotFoundError("Person nicht gefunden") from exc
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    @app.post("/api/person/<int:person_id>/prices")
    def api_person_prices(person_id: int):
        try:
            return jsonify(price_list=repos.persons.add_price_list(person_id, request.get_json(silent=True) or {}))
        except KeyError as exc:
            raise NotFoundError("Person nicht gefunden") from exc
        except (ValueError, TypeError) as exc:
            raise ValidationError(str(exc)) from exc

    @app.post("/api/person/<int:person_id>/encounters")
    def api_person_encounters(person_id: int):
        try:
            return jsonify(encounter=repos.persons.add_encounter(person_id, request.get_json(silent=True) or {}))
        except KeyError as exc:
            raise NotFoundError("Person nicht gefunden") from exc
        except (ValueError, TypeError) as exc:
            raise ValidationError(str(exc)) from exc

    @app.put("/api/person/<int:person_id>/encounters/<int:encounter_id>")
    def api_person_encounter_update(person_id: int, encounter_id: int):
        try:
            return jsonify(encounter=repos.persons.update_encounter(person_id, encounter_id, request.get_json(silent=True) or {}))
        except KeyError as exc:
            raise NotFoundError("Treffen nicht gefunden") from exc
        except (ValueError, TypeError) as exc:
            raise ValidationError(str(exc)) from exc

    @app.get("/api/lists/<tab>")
    def api_lists(tab: str):
        return jsonify(items=repos.persons.lists(tab))

    @app.get("/api/status-targets")
    def api_status_targets():
        return jsonify(repos.persons.status_targets())

    @app.post("/api/profile-status/<advert_id>")
    def api_profile_status(advert_id: str):
        target = repos.persons.listing_target(advert_id)
        if not target:
            raise NotFoundError("Anzeige nicht gefunden")
        url = safe_profile_url(target.get("current_url") or "")
        if not url:
            raise NotFoundError("Keine sichere Profil-URL")
        try:
            LOGGER.info("Status check start advert_id=%s", advert_id)
            status_limiter.wait()
            upstream = http_get(http, url, context=f"status-{advert_id}", expected_statuses=(200, 404, 410))
            if upstream.status_code in {404, 410}:
                return jsonify(repos.persons.update_listing_status(advert_id, status="UNAVAILABLE", http_status=upstream.status_code))
            upstream.raise_for_status()
            match = ADVERT_ID_RE.search(upstream.text)
            returned_id = match.group(1) if match else None
            if returned_id == advert_id:
                return jsonify(repos.persons.update_listing_status(advert_id, status="ACTIVE", http_status=200))
            return jsonify(repos.persons.update_listing_status(
                advert_id, status="UNKNOWN", http_status=200, details={"returned_id": returned_id}
            ))
        except Exception as exc:
            LOGGER.exception("Status check failed advert_id=%s", advert_id)
            raise ExternalServiceError("Profilstatus konnte nicht geprüft werden.") from exc

    @app.post("/api/identity/merge")
    def api_identity_merge():
        data = request.get_json(silent=True) or {}
        try:
            source_person_id = read_int(data, "source_person_id", minimum=1)
            target_person_id = read_int(data, "target_person_id", minimum=1)
            return jsonify(repos.persons.merge_persons(source_person_id, target_person_id))
        except ValidationError:
            raise
        except (ValueError, KeyError) as exc:
            LOGGER.warning("Manual merge rejected: %s", exc)
            raise ValidationError(str(exc) or "Personen konnten nicht zusammengeführt werden") from exc

    @app.post("/api/listing-id/<int:listing_id>/split")
    def api_listing_split(listing_id: int):
        try:
            return jsonify(repos.persons.split_listing(listing_id))
        except KeyError as exc:
            raise NotFoundError("Listing nicht gefunden") from exc

    @app.post("/api/media/<int:media_asset_id>/visual-analysis")
    def api_visual_analysis(media_asset_id: int):
        try:
            return jsonify(result=repos.archive.save_visual_analysis(media_asset_id, request.get_json(silent=True) or {}))
        except KeyError as exc:
            raise NotFoundError("Media asset nicht gefunden") from exc

    @app.get("/api/media-proxy")
    def api_media_proxy():
        url = safe_media_url(str(request.args.get("url") or ""))
        if not url:
            raise ValidationError("Ungültige Media-URL")
        spool = None
        try:
            upstream = http.get(
                url,
                headers={"Accept": "image/*,video/*,*/*", "Referer": "https://www.ladies.de/"},
                timeout=30,
                stream=True,
            )
            try:
                upstream.raise_for_status()
                declared_length = None
                raw_length = upstream.headers.get("Content-Length")
                if raw_length:
                    try:
                        declared_length = int(raw_length)
                    except (TypeError, ValueError):
                        declared_length = None
                if declared_length is not None and declared_length >= 0 and declared_length > MEDIA_PROXY_LIMIT:
                    return jsonify(error="Media-Datei ist zu groß"), 413

                content_type = str(upstream.headers.get("Content-Type") or "application/octet-stream").split(";", 1)[0].lower()
                if not (content_type.startswith("image/") or content_type.startswith("video/")):
                    raise ValidationError("Nicht unterstützter Media-Typ")

                spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
                total = 0
                for chunk in upstream.iter_content(chunk_size=128 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MEDIA_PROXY_LIMIT:
                        LOGGER.warning("Media proxy response exceeded limit")
                        spool.close()
                        spool = None
                        return jsonify(error="Media-Datei ist zu groß"), 413
                    spool.write(chunk)
            finally:
                upstream.close()

            spool.seek(0)

            def chunks():
                try:
                    while True:
                        chunk = spool.read(128 * 1024)
                        if not chunk:
                            break
                        yield chunk
                finally:
                    spool.close()

            response = Response(stream_with_context(chunks()), content_type=content_type)
            response.content_length = total
            response.headers["Cache-Control"] = "private, max-age=3600"
            return response
        except ValidationError:
            if spool is not None:
                spool.close()
            raise
        except Exception as exc:
            if spool is not None:
                spool.close()
            LOGGER.exception("Media proxy failed")
            raise ExternalServiceError("Media-Datei konnte nicht geladen werden.") from exc

    @app.post("/api/client-log")
    def api_client_log():
        data = request.get_json(silent=True) or {}
        if len(request.data or b"") > 32 * 1024:
            raise ValidationError("Client-Log ist zu groß")
        level = str(data.get("level") or "info").lower()
        message = redact_text(clean_text(str(data.get("message") or "client")), limit=1200)
        scope = redact_text(data.get("scope"), limit=120)
        extra = sanitize_log_value(data.get("extra"))
        fn = getattr(LOGGER, level, LOGGER.info) if level in {"debug", "info", "warning", "error"} else LOGGER.info
        fn("CLIENT scope=%s message=%s extra=%s", scope, message, extra)
        return jsonify(ok=True)

    return app
