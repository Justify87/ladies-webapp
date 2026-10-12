from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKER = (ROOT / "ladies" / "messaging" / "whatsapp_worker" / "worker.mjs").read_text(encoding="utf-8")
SERVICE = (ROOT / "ladies" / "messaging" / "service_reviewfix.py").read_text(encoding="utf-8")
WEB = (ROOT / "ladies" / "messaging" / "web_reviewfix.py").read_text(encoding="utf-8")
LAUNCHER = (ROOT / "ladies_explorer.py").read_text(encoding="utf-8")


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
    assert "SpooledTemporaryFile" in SERVICE
    assert "view_once_consumed_at.is_(None)" in SERVICE
    assert "source.unlink(missing_ok=True)" in SERVICE
    assert "application/octet-stream" in SERVICE
    assert "libopus" in SERVICE
    assert "audio/ogg; codecs=opus" in SERVICE


def test_whatsapp_archive_is_not_served_directly():
    assert 'request.path.startswith("/archive/whatsapp/")' in WEB
    assert "Content-Security-Policy" in WEB
    assert "X-Content-Type-Options" in WEB


def test_launcher_detects_bundled_ffmpeg_dependency():
    assert '"imageio-ffmpeg": ("imageio_ffmpeg", (0, 6), (1, 0))' in LAUNCHER
