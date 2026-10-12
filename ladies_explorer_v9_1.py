#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import socket
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ladies_v9.web_v92 import create_app

VERSION = "9.2.0"
SCRIPT_DIR = Path(__file__).resolve().parent


def configure_logging(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ladies_explorer")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    if not logger.handlers:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s [%(threadName)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler = RotatingFileHandler(log_path, maxBytes=10_000_000, backupCount=7, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        console.setFormatter(formatter)
        logger.addHandler(console)

    def _uncaught(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            return sys.__excepthook__(exc_type, exc_value, exc_tb)
        logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))

    sys.excepthook = _uncaught
    logger.info("Logging initialized file=%s python=%s", log_path.resolve(), sys.version.split()[0])
    return logger


def local_ipv4_addresses() -> list[str]:
    out: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in out:
                out.append(ip)
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if not ip.startswith("127.") and ip not in out:
            out.insert(0, ip)
    except OSError:
        pass
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=f"Ladies Explorer V{VERSION}")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--city", default="leipzig")
    parser.add_argument("--radius", type=int, default=20)
    parser.add_argument("--profile-delay", type=float, default=2.0, help="Mindestabstand zwischen vollständigen Profilabrufen")
    parser.add_argument("--db", default=str(SCRIPT_DIR / "ladies_v9.sqlite3"))
    parser.add_argument("--data-dir", default=str(SCRIPT_DIR / "ladies_v9_data"))
    parser.add_argument("--log", default=str(SCRIPT_DIR / "logs" / "ladies_explorer_v9_1.log"), help="Debug-Logdatei")
    args = parser.parse_args()

    logger = configure_logging(Path(args.log))
    logger.info("Starting Ladies Explorer version=%s host=%s port=%s db=%s data_dir=%s", VERSION, args.host, args.port, Path(args.db).resolve(), Path(args.data_dir).resolve())
    app = create_app(
        db_path=args.db,
        data_dir=args.data_dir,
        default_city=args.city,
        default_radius=max(1, args.radius),
        profile_delay=max(1.0, args.profile_delay),
    )

    print(f"Ladies Explorer V{VERSION}")
    print(f"Datenbank: {Path(args.db).resolve()}")
    print(f"Archiv:    {Path(args.data_dir).resolve()}")
    print(f"Debug-Log: {Path(args.log).resolve()}")
    print(f"Lokal:     http://127.0.0.1:{args.port}")
    if args.host in {"0.0.0.0", "::"}:
        token = app.config.get("LAN_ACCESS_TOKEN")
        for ip in local_ipv4_addresses():
            if token:
                print(f"Heimnetz-Pairing: http://{ip}:{args.port}/pair/{token}")
            else:
                print(f"Heimnetz:  http://{ip}:{args.port}")
        print("Hinweis: Im Heimnetz zuerst einmalig die Pairing-URL auf dem Smartphone öffnen.")
    print("WhatsApp wird intern verwaltet; Node.js 20+ wird nur für die Chat-Funktion benötigt.")
    app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
