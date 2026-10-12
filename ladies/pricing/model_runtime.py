from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


class FunctionGemmaRuntime:
    """Optional FunctionGemma 270M adapter.

    Semantic inference stays opt-in. The core rules path works without a model.
    """

    name = "FunctionGemma-270M"
    version = "1"

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.model_path = Path(os.environ.get(
            "LADIES_FUNCTIONGEMMA_MODEL",
            self.data_dir / "models" / "functiongemma" / "functiongemma-270m-it-q8_0.gguf",
        ))
        self.binary = os.environ.get("LADIES_LLAMA_CLI") or shutil.which("llama-cli")

    def available(self) -> bool:
        return bool(self.binary and self.model_path.exists())

    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "available": self.available(),
            "model_present": self.model_path.exists(),
            "runtime_present": bool(self.binary),
        }

    def extract(self, prompt: str) -> dict[str, Any]:
        if not self.available():
            raise RuntimeError("FunctionGemma-Runtime ist nicht installiert")
        instruction = (
            "Ordne ausschließlich die im Text vorhandenen Preis-/Dauer-Tokens zu. "
            "Erfinde keine Zahlen. Antworte ausschließlich als JSON-Objekt mit den "
            "Schlüsseln offers, service_rules, conditions, uncertainties.\n\n" + prompt
        )
        proc = subprocess.run(
            [self.binary, "-m", str(self.model_path), "-p", instruction, "-n", "256", "--temp", "0"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or "FunctionGemma fehlgeschlagen")[-1000:])
        start, end = proc.stdout.find("{"), proc.stdout.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError("FunctionGemma lieferte kein JSON")
        return json.loads(proc.stdout[start:end+1])
