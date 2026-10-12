import sys
import types
import unittest
from unittest.mock import patch

from ladies_v9.pricing.ocr import PaddleOcrV6TinyEngine


class PricingOcrTests(unittest.TestCase):
    def test_tiny_ocr_explicitly_uses_onnxruntime(self):
        captured = {}
        paddle = types.ModuleType("paddleocr")
        onnx = types.ModuleType("onnxruntime")

        class DummyPaddleOCR:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        paddle.PaddleOCR = DummyPaddleOCR
        with patch.dict(sys.modules, {"paddleocr": paddle, "onnxruntime": onnx}):
            engine = PaddleOcrV6TinyEngine()
            self.assertTrue(engine.available())
            engine._load()

        self.assertEqual(captured["engine"], "onnxruntime")
        self.assertEqual(captured["text_detection_model_name"], "PP-OCRv6_tiny_det")
        self.assertEqual(captured["text_recognition_model_name"], "PP-OCRv6_tiny_rec")


if __name__ == "__main__":
    unittest.main()
