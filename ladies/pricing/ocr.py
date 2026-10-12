from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("ladies_explorer.pricing.ocr")


@dataclass(frozen=True)
class OcrResult:
    text: str
    confidence: float | None
    engine: str
    engine_version: str


class OcrEngine:
    name = "none"
    version = "0"

    def available(self) -> bool:
        return False

    def extract(self, image_path: Path) -> OcrResult:
        raise RuntimeError("OCR ist nicht verfügbar")

    def status(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version, "available": self.available()}


class PaddleOcrV6TinyEngine(OcrEngine):
    """Optional PP-OCRv6 Tiny adapter using PaddleOCR's ONNX Runtime backend."""

    name = "PP-OCRv6-tiny"
    version = "6"

    def __init__(self) -> None:
        self._pipeline = None
        self._error: str | None = None
        try:
            import paddleocr  # noqa: F401
            import onnxruntime  # noqa: F401
        except Exception as exc:
            self._error = str(exc)

    def available(self) -> bool:
        return self._error is None

    def _load(self):
        if self._pipeline is not None:
            return self._pipeline
        if not self.available():
            raise RuntimeError("PP-OCRv6 Tiny ist nicht installiert. Installiere paddleocr und onnxruntime.")
        from paddleocr import PaddleOCR
        try:
            self._pipeline = PaddleOCR(
                text_detection_model_name="PP-OCRv6_tiny_det",
                text_recognition_model_name="PP-OCRv6_tiny_rec",
                device="cpu",
                enable_mkldnn=True,
            )
        except TypeError:
            self._pipeline = PaddleOCR(device="cpu")
        return self._pipeline

    def extract(self, image_path: Path) -> OcrResult:
        pipeline = self._load()
        texts: list[str] = []
        scores: list[float] = []
        try:
            results = pipeline.predict(str(image_path))
            for result in results:
                data = getattr(result, "json", None)
                if callable(data):
                    data = data()
                if not isinstance(data, dict):
                    data = getattr(result, "res", None) or {}
                if "res" in data and isinstance(data["res"], dict):
                    data = data["res"]
                rec_texts = data.get("rec_texts") or data.get("texts") or []
                rec_scores = data.get("rec_scores") or data.get("scores") or []
                texts.extend(str(x) for x in rec_texts if str(x).strip())
                scores.extend(float(x) for x in rec_scores if x is not None)
        except AttributeError:
            results = pipeline.ocr(str(image_path))
            for group in results or []:
                for row in group or []:
                    if len(row) >= 2 and row[1]:
                        texts.append(str(row[1][0]))
                        try:
                            scores.append(float(row[1][1]))
                        except Exception:
                            pass
        text = "\n".join(texts).strip()
        confidence = sum(scores) / len(scores) if scores else None
        return OcrResult(text=text, confidence=confidence, engine=self.name, engine_version=self.version)
