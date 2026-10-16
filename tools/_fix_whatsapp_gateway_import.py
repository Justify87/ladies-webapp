from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding='utf-8')
    if text.count(old) != 1:
        raise RuntimeError(f'{path}: expected one match, got {text.count(old)}')
    p.write_text(text.replace(old, new, 1), encoding='utf-8')


replace_once(
    'ladies/messaging/gateway.py',
    'from .provider_inbox import DURABLE_EVENT_TYPES, DurableProviderInbox\n',
    '''try:\n    from .provider_inbox import DURABLE_EVENT_TYPES, DurableProviderInbox\nexcept ImportError:  # architecture checks execute this file directly via runpy\n    import importlib.util\n\n    _inbox_spec = importlib.util.spec_from_file_location(\n        "ladies_messaging_provider_inbox",\n        Path(__file__).with_name("provider_inbox.py"),\n    )\n    if _inbox_spec is None or _inbox_spec.loader is None:\n        raise\n    _inbox_module = importlib.util.module_from_spec(_inbox_spec)\n    _inbox_spec.loader.exec_module(_inbox_module)\n    DURABLE_EVENT_TYPES = _inbox_module.DURABLE_EVENT_TYPES\n    DurableProviderInbox = _inbox_module.DurableProviderInbox\n''',
)

# Add an explicit bounded-timeout regression using a fake worker process.
path = Path('tools/check_whatsapp_reconnect_sync.py')
text = path.read_text(encoding='utf-8')
needle = '''def check_service_propagates_persistence_failures() -> None:\n'''
insert = '''def check_catch_up_timeout_restarts() -> None:\n    namespace = gateway_namespace()\n    gateway_cls = namespace["WhatsAppGateway"]\n    globals_ = gateway_cls._watch_catch_up.__globals__\n    previous_timeout = globals_["CATCH_UP_TIMEOUT_SECONDS"]\n    previous_restarts = globals_["CATCH_UP_MAX_RESTARTS"]\n    globals_["CATCH_UP_TIMEOUT_SECONDS"] = 0.01\n    globals_["CATCH_UP_MAX_RESTARTS"] = 2\n\n    class Proc:\n        def __init__(self):\n            self.terminated = threading.Event()\n\n        def poll(self):\n            return None\n\n        def terminate(self):\n            self.terminated.set()\n\n    try:\n        with tempfile.TemporaryDirectory() as tmp:\n            gateway = gateway_cls(Path(tmp), Path(tmp) / "whatsapp" / "auth", lambda event: None)\n            proc = Proc()\n            gateway.proc = proc\n            gateway._desired_running = True\n            gateway._stopping = False\n            gateway._catch_up_active = True\n            gateway._transport_open = True\n            gateway._pending_sync_ready = False\n            gateway._catch_up_token = 7\n            gateway._arm_catch_up_watchdog(proc, 7)\n            wait_for(proc.terminated.is_set)\n            require(gateway._catch_up_restarts == 1, "missing READY causes a bounded reconnect attempt")\n            require(gateway.state == "RECONNECTING", "timeout never exposes an unverified CONNECTED state")\n    finally:\n        globals_["CATCH_UP_TIMEOUT_SECONDS"] = previous_timeout\n        globals_["CATCH_UP_MAX_RESTARTS"] = previous_restarts\n\n\n'''
if text.count(needle) != 1:
    raise RuntimeError('timeout insertion point mismatch')
text = text.replace(needle, insert + needle, 1)
text = text.replace(
    '    check_durable_catch_up()\n    check_service_propagates_persistence_failures()\n',
    '    check_durable_catch_up()\n    check_catch_up_timeout_restarts()\n    check_service_propagates_persistence_failures()\n',
    1,
)
path.write_text(text, encoding='utf-8')
