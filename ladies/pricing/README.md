# Price Intelligence

Local, review-first extraction of structured price lists from WhatsApp chats.

## Pipeline

`token message detector -> context window -> optional PP-OCRv6 Tiny -> FunctionGemma 270M -> schema/evidence validation -> user review -> immutable PriceList revision`

There are exactly two interpretation stages:

1. `PriceMessageDetector` performs a lightweight, no-training, high-recall classification. It only decides whether an incoming message is likely price-related; it does not extract or assign prices.
2. FunctionGemma 270M performs all semantic extraction. `llama-cli` constrains generation to the pricing JSON schema, including the supported duration values and canonical service keys from the search filter catalog.

FunctionGemma is required for creating price proposals. Configure a local GGUF with `LADIES_FUNCTIONGEMMA_MODEL` and `llama-cli` with `LADIES_LLAMA_CLI` (or place `llama-cli` on `PATH`). The default model path is `data/models/functiongemma/functiongemma-270m-it-q8_0.gguf`.

Optional image OCR can be enabled by installing:

```bash
pip install paddleocr onnxruntime
```

PP-OCRv6 Tiny is loaded lazily and only for locally archived incoming chat images. Chat text and images are not sent to a cloud service by this module.

Accepted price revisions may reference the concrete Ladies listing via the existing `PriceList.listing_id`. The review UI proposes the listing associated with the chat endpoint and lets the user choose from the person's known listing locations.
