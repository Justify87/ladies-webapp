from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI = (ROOT / "ladies" / "static" / "messaging.js").read_text(encoding="utf-8")
WORKER = (ROOT / "ladies" / "messaging" / "whatsapp_worker" / "worker.mjs").read_text(encoding="utf-8")
WEB = (ROOT / "ladies" / "messaging" / "web_complete.py").read_text(encoding="utf-8")


def test_enter_sends_shift_enter_keeps_newline_and_focus_is_restored():
    assert "e.key==='Enter'&&!e.shiftKey&&!e.isComposing" in UI
    assert "requestSubmit()" in UI
    assert "focus({preventScroll:true})" in UI
    assert "finally{focusInput()" in UI


def test_reply_reaction_edit_delete_controls_are_present():
    assert "reply_to_message_id" in UI
    assert "/reaction" in UI
    assert "method:'PATCH'" in UI
    assert "method:'DELETE'" in UI
    assert "data-message-menu" in UI


def test_media_voice_and_view_once_ui_are_present():
    assert "FormData" in UI
    assert "MediaRecorder" in UI
    assert "getUserMedia({audio:true})" in UI
    assert "media_type" in UI
    assert "view_once" in UI
    assert "/view-once" in UI


def test_worker_uses_native_baileys_primitives():
    assert "sock.readMessages(keys)" in WORKER
    assert "react: { text:" in WORKER
    assert "edit: key" in WORKER
    assert "delete: key" in WORKER
    assert "viewOnce" in WORKER
    assert "quoted" in WORKER
    assert "extractMessageContent" in WORKER
    assert "downloadMediaMessage" in WORKER
    assert "messages.reaction" in WORKER


def test_http_surface_has_media_and_message_actions():
    assert "/media" in WEB
    assert "/reaction" in WEB
    assert "@app.patch" in WEB
    assert "@app.delete" in WEB
    assert "/view-once" in WEB
