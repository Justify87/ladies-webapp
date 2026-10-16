from pathlib import Path

worker = Path('ladies/messaging/whatsapp_worker/worker_v2.mjs')
text = worker.read_text(encoding='utf-8')
old = "  if (context?.pendingReceived && historySet?.isLatest) emitSyncReady(current)\n  if (context?.pendingReceived && historyStatus?.status === 'paused') emitSyncReady(current)\n"
new = "  if (context?.pendingReceived && historySet) emitSyncReady(current)\n"
if text.count(old) != 1:
    raise RuntimeError(f'worker READY terminal block mismatch: {text.count(old)}')
worker.write_text(text.replace(old, new, 1), encoding='utf-8')

test = Path('tools/check_whatsapp_reconnect_sync.py')
text = test.read_text(encoding='utf-8')
old = "    require(\"historySet?.isLatest\" in batch, \"latest history batch can close first-sync catch-up\")\n"
new = (
    "    require(\"context?.pendingReceived && historySet\" in batch, \"a flushed history set can close first-sync catch-up\")\n"
    "    require(\"historyStatus?.status === 'paused') emitSyncReady\" not in batch, \"history pause alone cannot release READY\")\n"
)
if text.count(old) != 1:
    raise RuntimeError(f'test READY assertion mismatch: {text.count(old)}')
test.write_text(text.replace(old, new, 1), encoding='utf-8')
