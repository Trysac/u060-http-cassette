# Explicit transport wrapper with redaction applied before matching and storage

Status: accepted (generated, pending human review)

## Context

Tests that call real HTTP APIs are slow and flaky, and tools that patch HTTP libraries globally (such as vcrpy) store credentials unless carefully configured. The library must be safe to commit by default, stdlib only, and independent of any HTTP client.

## Decision

Provide an explicit Transport protocol and a CassetteTransport wrapper instead of monkeypatching. A Redactor runs on every request and response before it is matched, stored or served, so recordings and match keys only ever contain redacted forms. Header and query names compare case-insensitively and exactly; JSON and form body fields are redacted, and JSON bodies that cannot be parsed or are nested deeper than 64 levels are replaced entirely (fail closed). Bodies are re-serialised only when a field changed so unchanged bytes stay exact. Matching interactions are consumed once each in recorded order. The file is UTF-8 JSON with sorted keys and is written atomically through a temp file and os.replace. Size limits (max_bytes, max_interactions, URL length) raise the typed CassetteError.

## Consequences

Callers must adapt their HTTP client to the Transport protocol (a small urllib adapter is shown in the README). Secrets in unlisted headers, parameters or fields, free-text bodies, other content types, path segments and URL fragments are not defended and must be listed by the user or avoided. Replay serves the redacted response, so code under test must not depend on redacted values. CassetteTransport rewrites the whole cassette after each recorded interaction, which is simple and crash-safe but O(n) per save.
