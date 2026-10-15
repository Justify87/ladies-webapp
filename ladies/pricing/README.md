# Price Intelligence

Local, review-first extraction of structured price lists from WhatsApp chats.

## Pipeline

`token message detector -> context window -> optional PP-OCRv6 Tiny -> FunctionGemma 270M -> schema/evidence validation -> user review -> immutable PriceList revision`

There are exactly two interpretation stages:

1. `PriceMessageDetector` performs a lightweight, no-training, high-recall classification. It only decides whether an incoming message is likely price-related; it does not extract or assign prices.
2. FunctionGemma 270M performs all semantic extraction. `llama-cli` constrains generation to the pricing JSON schema, including the supported duration values and canonical service keys from the search filter catalog.

FunctionGemma is required for creating price proposals. Configure a local GGUF with `LADIES_FUNCTIONGEMMA_MODEL` and `llama-cli` with `LADIES_LLAMA_CLI` (or place `llama-cli` on `PATH`). The default model path is `data/models/functiongemma/functiongemma-270m-it-q8_0.gguf`.

The validator does not perform a second semantic extraction. It checks that every FunctionGemma evidence excerpt belongs to an allowed chat message and that emitted prices, durations, service labels and surcharges are grounded in the cited evidence. Ambiguous interpretations stay visible as `uncertainties` for user review.

Optional image OCR can be enabled by installing:

```bash
pip install paddleocr onnxruntime
```

PP-OCRv6 Tiny is loaded lazily and only for locally archived incoming chat images. Chat text and images are not sent to a cloud service by this module.

## Locations

Accepted revisions use `PriceList.listing_id` as their pricing scope. Baselines, partial merges, stale checks and manual revisions are resolved independently per person and listing; prices from two locations therefore do not overwrite or extend one another.

The chat endpoint's source listing is used only as a suggestion. The user can select another known listing or explicitly choose no location. There is no fallback that silently assigns the person's most recent listing when the chat has no listing evidence.

`PriceExtractionLocation` records the suggested/selected scope for the review flow. `PriceListLocationSnapshot` stores an immutable copy of the accepted listing location so historical price revisions keep the address/location that applied when they were saved, even if the listing later changes.
