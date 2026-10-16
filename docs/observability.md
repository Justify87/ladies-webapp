# Observability

Observability is optional and disabled by default. The integration is intentionally limited to traces, trace/log correlation and on-demand request profiling.

## OpenTelemetry tracing

Enable tracing with:

```text
LADIES_OTEL_ENABLED=1
```

The app exports traces with OTLP over HTTP and uses the standard OpenTelemetry exporter settings. For a local OTLP backend such as Jaeger:

```text
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://127.0.0.1:4318/v1/traces
```

Automatic instrumentation is limited to Flask requests and SQLAlchemy on the shared application engine. The existing application logs receive trace/span IDs for correlation, but OpenTelemetry log export is not enabled.

Outbound HTTP is traced only at the application-owned network boundaries (Ladies source access, map geocoding and media archive access). These manual client spans keep method, a normalized low-cardinality operation name, scheme, host, status and safe error type only. They do not export URL paths/query strings, headers, bodies or exception messages/stack traces.

`BackgroundTasks` captures the current trace context explicitly when a job is submitted and creates one `background.task` child span with only task type and queue wait time. OpenTelemetry does not globally patch `threading` or `ThreadPoolExecutor`, so long-lived WhatsApp and scheduler threads cannot inherit an unrelated request trace.

Two local process boundaries are traced explicitly: `messaging.command` for Python-to-Baileys IPC and `pricing.model` for the local FunctionGemma/llama-cli execution. No WhatsApp JIDs, phone numbers, message text, prompts or payloads are added to these spans.

Static/archive traffic, the messaging SSE stream and client-log endpoint are excluded from Flask request tracing. Request/response bodies, cookies and authorization data are not captured. Inbound request URL attributes are normalized without query strings.

For this single local service a Collector is not required. Jaeger can accept OTLP directly. A Collector can still be placed in front later without changing application code.

## Pyinstrument

Enable request profiling only while investigating a performance problem:

```text
LADIES_PYINSTRUMENT=1
```

Profiles are written without changing the normal API response. The default directory is `logs/profiles/` next to the configured local data directory. By default only requests taking at least 100 ms are retained, Pyinstrument uses a 1 ms sampling interval and at most 50 HTML reports are kept.

Optional settings:

```text
LADIES_PYINSTRUMENT_DIR=logs/profiles
LADIES_PYINSTRUMENT_MIN_MS=100
LADIES_PYINSTRUMENT_INTERVAL=0.001
LADIES_PYINSTRUMENT_MAX_FILES=50
```

Profile generation is fail-open: an unavailable/unwritable report directory or renderer failure is logged but never changes the API response. Only API requests are profiled; the messaging SSE stream and client-log endpoint are excluded. If OpenTelemetry is active, the profile filename includes the current trace ID prefix so the HTML profile can be matched to the corresponding trace.
