# Project record

## Objective

Record and replay HTTP interactions through an injectable transport, redacting credentials in headers, URLs, JSON and form bodies before disk

## Scope

- In scope: the public API documented in [README.md](../README.md), typed and tested.
- Out of scope: anything that needs runtime dependencies or I/O without an accepted ADR.

## Timeline

| Date | Event |
|---|---|
| 2026 | Repository created from the factory utility template. |

## Effort metrics

Measured values only; a value that has not been measured stays "pending measurement".

| Metric | Value |
|---|---|
| Tokens (input / output) | 487385 / 74434 |
| Hours (human) | pending measurement |
| Cost (USD) | 0.6733 |

## Factory run u060 (measured from API usage)

| Phase | Model | Batch | Input | Output | Cache write | Cache read | Cost (USD) |
|---|---|---|---|---|---|---|---|
| generation | claude-sonnet-5-5 | yes | 2377 | 61221 | 0 | 4246 | 0.3089 |
| fix-1 | claude-sonnet-5-5 | no | 8 | 1116 | 6611 | 54775 | 0.0332 |
| fix-2 | claude-sonnet-5-5 | no | 8 | 1073 | 7117 | 55613 | 0.0341 |
| fix-3 | claude-sonnet-5-5 | no | 8 | 733 | 6564 | 55091 | 0.0293 |
| audit | claude-sonnet-5-5 | no | 14 | 4400 | 39873 | 156140 | 0.1593 |
| fix-4 | claude-sonnet-5-5 | no | 6 | 1496 | 7647 | 40272 | 0.0381 |
| docs | claude-haiku-5-5 | no | 118827 | 4395 | 0 | 0 | 0.0704 |

Total: $0.6733. Prices: Anthropic list prices.
