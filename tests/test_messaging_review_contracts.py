from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKER = (ROOT / "ladies" / "messaging" / "whatsapp_worker" / "worker.mjs").read_text(encoding="utf-8")
SERVICE = (ROOT / "ladies" / "messaging" / "service.py").read_text(encoding="utf-8")
MEDIA = (ROOT / "ladies" / "messaging" / "media_store.py").read_text(encoding="utf-8")
ROUTES = (ROOT / "ladies" / "messaging" / "routes.py").read_text(encoding="utf-8")
PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_worker_streams_incoming_media_and_has_private_view_once_store():
    assert "downloadMediaMessage(" in WORKER
    assert "'stream'" in WORKER
    assert "for await (const chunk of stream)" in WORKER
    assert "MAX_INCOMING_MEDIA" in WORKER
    assert "viewOnceDir" in WORKER
    assert "private_view_once" in WORKER


def test_worker_propagates_remote_edits_and_revokes():
    assert "WAMessageStubType" in WORKER
    assert "WAMessageStubType.REVOKE" in WORKER
    assert "message_content_update" in WORKER
    assert "action: 'EDIT'" in WORKER
    assert "action: 'DELETE'" in WORKER


def test_service_hardens_replies_view_once_documents_and_voice():
    assert "derselben WhatsApp-Nummer" in SERVICE
    assert "view_once_consumed_at.is_(None)" in SERVICE
    assert "application/octet-stream" in MEDIA
    assert "libopus" in MEDIA
    assert "audio/ogg; codecs=opus" in MEDIA
    assert "relative_to" in MEDIA


def test_whatsapp_archive_is_not_served_directly():
    assert 'request.path.startswith("/archive/whatsapp/")' in ROUTES
    assert "Content-Security-Policy" in ROUTES
    assert "X-Content-Type-Options" in ROUTES


def test_ffmpeg_dependency_is_declared_once_in_project_metadata():
    assert '"imageio-ffmpeg>=0.6,<1"' in PYPROJECT
