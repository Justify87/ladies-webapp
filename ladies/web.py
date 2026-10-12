from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from flask import Flask, g, jsonify, render_template, request, send_from_directory

from .database import Database
from .domain import SearchProfile
from .media import ArchiveManager
from .parser import ADVERT_ID_RE, parse_profile_html
from .repository import Repository
from .source import BatchRateLimiter, FixedRateLimiter, build_session, fetch_search_configured, http_get, resolve_location
from .utils import category_from_url, clean_text, safe_media_url, safe_profile_url

LOGGER = logging.getLogger("ladies_explorer")


def create_app(*, db_path: str | Path, data_dir: str | Path, default_city: str = "leipzig", default_radius: int = 20, profile_delay: float = 2.0) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    database = Database(db_path)
    archive = ArchiveManager(data_dir)
    repo = Repository(database, archive)
    http = build_session()
    limiter = FixedRateLimiter(profile_delay)
    status_limiter = BatchRateLimiter(batch_size=3, pause_seconds=0.8)
    LOGGER.info("Web app initialized db=%s data_dir=%s default_city=%s default_radius=%s", Path(db_path).resolve(), Path(data_dir).resolve(), default_city, default_radius)

    @app.before_request
    def log_request_start():
        g.request_started = time.monotonic()
        g.request_id = uuid.uuid4().hex[:10]
        LOGGER.debug("REQ id=%s method=%s path=%s query=%s", g.request_id, request.method, request.path, request.query_string.decode("utf-8", "replace"))

    @app.after_request
    def log_request_end(response):
        elapsed_ms = (time.monotonic() - getattr(g, "request_started", time.monotonic())) * 1000
        level = logging.INFO if response.status_code < 400 else logging.WARNING
        LOGGER.log(level, "RESP id=%s method=%s path=%s status=%s elapsed_ms=%.1f", getattr(g, "request_id", "-"), request.method, request.path, response.status_code, elapsed_ms)
        response.headers.setdefault("X-Request-ID", getattr(g, "request_id", "-"))
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
        return jsonify(repo.dashboard())

    @app.get("/api/history/relistings")
    def api_history_relistings():
        try:
            limit = int(request.args.get("limit") or 100)
        except ValueError:
            limit = 100
        return jsonify(items=repo.history_relistings(limit))

    @app.get("/api/history/changes")
    def api_history_changes():
        try:
            limit = int(request.args.get("limit") or 100)
        except ValueError:
            limit = 100
        return jsonify(items=repo.history_changes(limit))

    @app.get("/api/recent-searches")
    def api_recent_searches():
        try:
            limit = int(request.args.get("limit") or 8)
        except ValueError:
            limit = 8
        return jsonify(items=repo.recent_searches(limit))

    @app.get("/api/storage")
    def api_storage():
        return jsonify(repo.storage_info())

    @app.get("/api/archive/media")
    def api_archive_media():
        kind = clean_text(str(request.args.get("kind") or "")).upper() or None
        if kind not in {None, "IMAGE", "VIDEO"}:
            return jsonify(error="kind muss IMAGE oder VIDEO sein"), 400
        try:
            limit = int(request.args.get("limit") or 80)
        except ValueError:
            limit = 80
        return jsonify(items=repo.archive_items(kind=kind, limit=limit))

    @app.delete("/api/visual-analysis")
    def api_clear_visual_analysis():
        return jsonify(deleted=repo.clear_visual_analyses())

    @app.route("/api/settings", methods=["GET", "POST"])
    def api_settings():
        if request.method == "POST":
            payload = request.get_json(silent=True) or {}
            key = str(payload.get("key") or "")
            if not key:
                return jsonify(error="key fehlt"), 400
            try:
                return jsonify(settings=repo.set_setting(key, payload.get("value")))
            except ValueError as exc:
                return jsonify(error=str(exc)), 400
        return jsonify(settings=repo.settings())

    @app.post("/api/search")
    def api_search():
        payload = request.get_json(silent=True) or {}
        city_raw = clean_text(str(payload.get("city") or default_city))
        radius = max(1, min(200, int(payload.get("radius") or default_radius)))
        category = clean_text(str(payload.get("category") or ""))
        age_min = max(18, min(60, int(payload.get("age_min") or 18)))
        age_max = max(age_min, min(60, int(payload.get("age_max") or 60)))
        height_min = max(140, min(200, int(payload.get("height_min") or 140)))
        height_max = max(height_min, min(200, int(payload.get("height_max") or 200)))
        selected = payload.get("selected") or {}
        try:
            LOGGER.info("Search start city=%s radius=%s category=%s age=%s-%s filters=%s", city_raw, radius, category or "ALL", age_min, age_max, sorted(selected))
            city_slug, city_display = resolve_location(http, city_raw)
            run_id = repo.create_search_run(
                city=city_display,
                city_slug=city_slug,
                radius_km=radius,
                filters={
                    "category": category, "age_min": age_min, "age_max": age_max,
                    "height_min": height_min, "height_max": height_max, "selected": selected,
                },
            )
            profiles, meta = fetch_search_configured(
                session=http,
                city_slug=city_slug,
                city_display=city_display,
                radius_km=radius,
                category=category,
                age_min=age_min,
                age_max=age_max,
                height_min=height_min,
                height_max=height_max,
                selected=selected,
            )
            result = repo.finish_search_run(run_id, profiles, meta)
            LOGGER.info("Search complete run_id=%s results=%s city=%s radius=%s", run_id, len(result), city_display, radius)
            return jsonify(search_run_id=run_id, results=result, meta=meta)
        except (ValueError, RuntimeError) as exc:
            LOGGER.warning("Search rejected: %s", exc)
            return jsonify(error=str(exc)), 400
        except Exception as exc:
            LOGGER.exception("Search failed")
            return jsonify(error=str(exc)), 502

    @app.get("/api/profile/<advert_id>")
    def api_profile(advert_id: str):
        if not advert_id.isdigit():
            return jsonify(error="Ungültige Anzeigen-ID"), 400
        target = repo.listing_target(advert_id)
        if not target:
            return jsonify(error="Anzeige ist lokal nicht bekannt. Zuerst über die Suche laden."), 404
        profile_url = safe_profile_url(target.get("current_url") or "")
        if not profile_url:
            return jsonify(error="Keine sichere Ladies-Profil-URL gespeichert."), 404
        fallback = SearchProfile(
            advert_id=advert_id,
            name="",
            city="",
            category=target.get("category") or category_from_url(profile_url),
            profile_url=profile_url,
            thumbnail_url=None,
        )
        try:
            LOGGER.info("Profile load start advert_id=%s person_id=%s", advert_id, target.get("person_id"))
            limiter.wait()
            response = http_get(http, profile_url, context=f"profile-{advert_id}", expected_statuses=(200, 404, 410))
            if response.status_code in {404, 410}:
                repo.update_listing_status(advert_id, status="UNAVAILABLE", http_status=response.status_code)
                return jsonify(error="Anzeige ist nicht mehr verfügbar.", status="UNAVAILABLE"), 404
            response.raise_for_status()
            match = ADVERT_ID_RE.search(response.text)
            returned_id = match.group(1) if match else None
            if returned_id != advert_id:
                repo.update_listing_status(advert_id, status="UNKNOWN", http_status=response.status_code, details={"returned_id": returned_id, "final_url": response.url})
                return jsonify(error="Die Profilantwort war nicht eindeutig.", returned_id=returned_id), 409
            profile = parse_profile_html(response.text, response.url, fallback)
            payload = repo.ingest_profile(external_listing_id=advert_id, profile=profile, raw_html=response.text, http_session=http)
            LOGGER.info("Profile load complete advert_id=%s person_id=%s media=%s", advert_id, payload.get("identity", {}).get("person_id"), len(payload.get("profile", {}).get("media", [])))
            return jsonify(**payload)
        except Exception as exc:
            LOGGER.exception("Profile load failed advert_id=%s", advert_id)
            return jsonify(error=str(exc)), 502

    @app.get("/api/snapshot/<int:snapshot_id>")
    def api_snapshot(snapshot_id: int):
        try:
            return jsonify(repo.snapshot_detail(snapshot_id))
        except KeyError:
            return jsonify(error="Snapshot nicht gefunden"), 404

    @app.get("/api/listing/<advert_id>")
    def api_listing(advert_id: str):
        try:
            return jsonify(repo.listing_detail(advert_id))
        except KeyError:
            return jsonify(error="Anzeige nicht gefunden"), 404

    @app.get("/api/person/<int:person_id>")
    def api_person(person_id: int):
        try:
            return jsonify(repo.person_detail(person_id))
        except KeyError:
            return jsonify(error="Person nicht gefunden"), 404

    @app.post("/api/person/<int:person_id>/state")
    def api_person_state(person_id: int):
        try:
            return jsonify(user_state=repo.set_user_state(person_id, request.get_json(silent=True) or {}))
        except KeyError:
            return jsonify(error="Person nicht gefunden"), 404
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @app.post("/api/person/<int:person_id>/prices")
    def api_person_prices(person_id: int):
        try:
            return jsonify(price_list=repo.add_price_list(person_id, request.get_json(silent=True) or {}))
        except KeyError:
            return jsonify(error="Person nicht gefunden"), 404
        except (ValueError, TypeError) as exc:
            return jsonify(error=str(exc)), 400

    @app.post("/api/person/<int:person_id>/encounters")
    def api_person_encounters(person_id: int):
        try:
            return jsonify(encounter=repo.add_encounter(person_id, request.get_json(silent=True) or {}))
        except KeyError:
            return jsonify(error="Person nicht gefunden"), 404
        except (ValueError, TypeError) as exc:
            return jsonify(error=str(exc)), 400

    @app.put("/api/person/<int:person_id>/encounters/<int:encounter_id>")
    def api_person_encounter_update(person_id: int, encounter_id: int):
        try:
            return jsonify(encounter=repo.update_encounter(person_id, encounter_id, request.get_json(silent=True) or {}))
        except KeyError:
            return jsonify(error="Treffen nicht gefunden"), 404
        except (ValueError, TypeError) as exc:
            return jsonify(error=str(exc)), 400

    @app.get("/api/lists/<tab>")
    def api_lists(tab: str):
        return jsonify(items=repo.lists(tab))

    @app.get("/api/status-targets")
    def api_status_targets():
        return jsonify(items=repo.status_targets())

    @app.post("/api/profile-status/<advert_id>")
    def api_profile_status(advert_id: str):
        target = repo.listing_target(advert_id)
        if not target:
            return jsonify(error="Anzeige nicht gefunden"), 404
        url = safe_profile_url(target.get("current_url") or "")
        if not url:
            return jsonify(error="Keine sichere Profil-URL"), 404
        try:
            LOGGER.info("Status check start advert_id=%s", advert_id)
            status_limiter.wait()
            response = http_get(http, url, context=f"status-{advert_id}", expected_statuses=(200, 404, 410))
            if response.status_code in {404, 410}:
                return jsonify(repo.update_listing_status(advert_id, status="UNAVAILABLE", http_status=response.status_code))
            response.raise_for_status()
            match = ADVERT_ID_RE.search(response.text)
            returned_id = match.group(1) if match else None
            if returned_id == advert_id:
                return jsonify(repo.update_listing_status(advert_id, status="ACTIVE", http_status=200))
            return jsonify(repo.update_listing_status(advert_id, status="UNKNOWN", http_status=200, details={"returned_id": returned_id}))
        except Exception as exc:
            LOGGER.exception("Status check failed %s", advert_id)
            return jsonify(error=str(exc)), 502

    @app.post("/api/identity/merge")
    def api_identity_merge():
        data = request.get_json(silent=True) or {}
        try:
            source_person_id = int(data["source_person_id"])
            target_person_id = int(data["target_person_id"])
            return jsonify(repo.merge_persons(source_person_id, target_person_id))
        except KeyError:
            return jsonify(error="source_person_id/target_person_id fehlt"), 400
        except Exception as exc:
            LOGGER.exception("Manual merge failed")
            return jsonify(error=str(exc)), 400

    @app.post("/api/listing-id/<int:listing_id>/split")
    def api_listing_split(listing_id: int):
        try:
            return jsonify(repo.split_listing(listing_id))
        except KeyError:
            return jsonify(error="Listing nicht gefunden"), 404

    @app.post("/api/media/<int:media_asset_id>/visual-analysis")
    def api_visual_analysis(media_asset_id: int):
        try:
            return jsonify(result=repo.save_visual_analysis(media_asset_id, request.get_json(silent=True) or {}))
        except KeyError:
            return jsonify(error="Media asset nicht gefunden"), 404

    @app.get("/api/media-proxy")
    def api_media_proxy():
        url = safe_media_url(str(request.args.get("url") or ""))
        if not url:
            return jsonify(error="Ungültige Media-URL"), 400
        try:
            response = http.get(url, headers={"Accept": "image/*,*/*", "Referer": "https://www.ladies.de/"}, timeout=30)
            response.raise_for_status()
            headers = {"Cache-Control": "private, max-age=3600"}
            if response.headers.get("Content-Type"):
                headers["Content-Type"] = response.headers["Content-Type"]
            return response.content, 200, headers
        except Exception as exc:
            LOGGER.exception("Media proxy failed")
            return jsonify(error=str(exc)), 502

    @app.post("/api/client-log")
    def api_client_log():
        data = request.get_json(silent=True) or {}
        level = str(data.get("level") or "info").lower()
        message = clean_text(str(data.get("message") or "client"))[:2000]
        fn = getattr(LOGGER, level, LOGGER.info) if level in {"debug","info","warning","error"} else LOGGER.info
        fn("CLIENT scope=%s message=%s extra=%s", data.get("scope"), message, data.get("extra"))
        return jsonify(ok=True)

    return app
