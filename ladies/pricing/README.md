# Price Intelligence

Local, review-first extraction of structured price lists from WhatsApp chats.

Pipeline:

`price signal -> context window -> optional PP-OCRv6 Tiny -> deterministic rules -> proposal -> user review -> immutable PriceList revision`

The core feature has no model dependency. Optional OCR can be enabled by installing:

```bash
pip install paddleocr onnxruntime
```

PP-OCRv6 Tiny is loaded lazily and only for locally archived incoming chat images. FunctionGemma 270M is optional and disabled by default; the runtime adapter only activates when a local GGUF plus `llama-cli` are explicitly configured.

No chat text or images are sent to a cloud service by this module.
