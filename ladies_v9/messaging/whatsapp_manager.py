from __future__ import annotations
import atexit, json, logging, os, queue, shutil, subprocess, threading, time, uuid
from pathlib import Path
from typing import Any, Callable

LOGGER = logging.getLogger("ladies_explorer.messaging.worker")

class BaileysWorkerManager:
    def __init__(self, worker_dir: Path, auth_dir: Path, event_handler: Callable[[dict[str, Any]], None]):
        self.worker_dir=Path(worker_dir); self.auth_dir=Path(auth_dir); self.event_handler=event_handler
        self.proc: subprocess.Popen[str] | None=None; self._lock=threading.RLock(); self._pending={}; self._stopping=False
        self.state="NOT_CONFIGURED"; self.detail=None; self.qr=None; self.account=None; self.last_connected_at=None
        atexit.register(self.stop)
    def status(self): return {"state":self.state,"detail":self.detail,"qr_available":bool(self.qr),"account":self.account,"last_connected_at":self.last_connected_at}
    def _set_state(self,state,detail=None): self.state=state; self.detail=detail; self.event_handler({"type":"connection","state":state,"detail":detail})
    def dependency_status(self):
        node=shutil.which("node")
        if not node: return False,"Node.js 20+ fehlt"
        try:
            major=int(subprocess.check_output([node,"-p","process.versions.node.split('.')[0]"],text=True,timeout=5).strip())
            if major<20:return False,"Node.js 20+ erforderlich"
        except Exception:return False,"Node.js-Version konnte nicht geprüft werden"
        return True,None
    def bootstrap(self):
        ok,detail=self.dependency_status()
        if not ok: self._set_state("DEPENDENCY_MISSING",detail); return False
        mods=self.worker_dir/"node_modules"/"@whiskeysockets"/"baileys"
        if mods.exists(): return True
        npm=shutil.which("npm")
        if not npm:self._set_state("DEPENDENCY_MISSING","npm fehlt");return False
        try:
            LOGGER.info("Installing WhatsApp worker dependencies")
            lock=self.worker_dir/"package-lock.json"
            command=[npm,"ci","--omit=dev"] if lock.exists() else [npm,"install","--omit=dev","--no-audit","--no-fund"]
            subprocess.run(command,cwd=self.worker_dir,check=True,timeout=180,stdout=subprocess.DEVNULL)
            return True
        except Exception as exc:
            LOGGER.error("WhatsApp worker bootstrap failed: %s",exc);self._set_state("UNAVAILABLE","WhatsApp-Komponente konnte nicht installiert werden");return False
    def start(self):
        with self._lock:
            if self.proc and self.proc.poll() is None:return
            if not self.bootstrap():return
            self.auth_dir.mkdir(parents=True,exist_ok=True); self._stopping=False; self._set_state("CONNECTING")
            env=os.environ.copy();env["LADIES_WHATSAPP_AUTH_DIR"]=str(self.auth_dir)
            self.proc=subprocess.Popen([shutil.which("node") or "node","worker.mjs"],cwd=self.worker_dir,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1,env=env)
            threading.Thread(target=self._read_stdout,daemon=True,name="wa-json").start();threading.Thread(target=self._read_stderr,daemon=True,name="wa-log").start();threading.Thread(target=self._watch,daemon=True,name="wa-watch").start()
    def _read_stdout(self):
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            try: evt=json.loads(line)
            except Exception: LOGGER.warning("Ignoring invalid worker JSON");continue
            self._handle(evt)
    def _read_stderr(self):
        assert self.proc and self.proc.stderr
        for line in self.proc.stderr: LOGGER.info("baileys: %s",line.rstrip()[:1200])
    def _watch(self):
        assert self.proc; code=self.proc.wait()
        if self._stopping:return
        self._set_state("ERROR",f"WhatsApp-Worker beendet (Code {code})")
    def _handle(self,evt):
        typ=evt.get("type")
        if typ=="qr": self.qr=evt.get("value");self._set_state("QR_REQUIRED")
        elif typ=="connection":
            st=str(evt.get("state") or "").upper(); self.state=st; self.detail=evt.get("detail")
            if st=="CONNECTED":self.qr=None;self.account=evt.get("account");self.last_connected_at=time.time()
        rid=evt.get("id")
        if rid and rid in self._pending:self._pending[rid].put(evt)
        self.event_handler(evt)
    def command(self,typ,timeout=15,**payload):
        self.start()
        if not self.proc or self.proc.poll() is not None: raise RuntimeError(self.detail or "WhatsApp-Worker nicht verfügbar")
        rid=uuid.uuid4().hex; q=queue.Queue(maxsize=1);self._pending[rid]=q
        try:
            raw=json.dumps({"id":rid,"type":typ,**payload},ensure_ascii=False)
            assert self.proc.stdin;self.proc.stdin.write(raw+"\n");self.proc.stdin.flush()
            evt=q.get(timeout=timeout)
            if evt.get("ok") is False:raise RuntimeError(evt.get("message") or "Worker-Fehler")
            return evt
        finally:self._pending.pop(rid,None)
    def stop(self):
        with self._lock:
            if not self.proc:return
            self._stopping=True
            try:self.command("shutdown",timeout=2)
            except Exception:pass
            try:self.proc.terminate();self.proc.wait(timeout=3)
            except Exception:
                try:self.proc.kill()
                except Exception:pass
            self.proc=None
