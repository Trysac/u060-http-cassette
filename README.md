# http-cassette

[![PyPI version](https://img.shields.io/pypi/v/trysac-http-cassette)](https://pypi.org/project/trysac-http-cassette/)
[![License: MIT](https://img.shields.io/pypi/l/trysac-http-cassette)](LICENSE)
[![CI](https://github.com/Trysac/u060-http-cassette/actions/workflows/ci.yml/badge.svg)](https://github.com/Trysac/u060-http-cassette/actions/workflows/ci.yml)

Record and replay HTTP interactions through an injectable transport, redacting credentials in headers, URLs, JSON and form bodies before disk

## Problem

Tests that hit real HTTP APIs are slow, flaky and need credentials in CI, while fixtures recorded by
monkeypatching tools often leak tokens into the repository. http-cassette is an explicit transport
wrapper that records once, replays deterministically and redacts by default, so integration tests are
safe to commit. Unlike vcrpy, which patches HTTP libraries globally and stores secrets unless
configured, it redacts out of the box and patches nothing.

Replay serves the redacted response (documented behaviour): redacted values are what you get back.

### Threat model

Defended: listed headers, query parameter names (exact, case-insensitive, percent-decoded), URL
userinfo, and listed JSON and form fields in requests and responses.

Not defended: secrets in unlisted fields, free-text bodies, response bodies of other content types,
and path segments. See [security](docs/SECURITY.md).

## Install

```sh
pip install trysac-http-cassette
# or
uv add trysac-http-cassette
```

Requires Python 3.10 or newer. No runtime dependencies.

## Usage

```python
import urllib.error
import urllib.request
from pathlib import Path

from http_cassette import (
    Cassette,
    CassetteTransport,
    RecordedRequest,
    RecordedResponse,
    Redactor,
)


class UrllibTransport:
    """A minimal network adapter built on urllib."""

    def send(self, request: RecordedRequest) -> RecordedResponse:
        req = urllib.request.Request(
            request.url,
            data=request.body or None,
            headers=dict(request.headers),
            method=request.method,
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return RecordedResponse(resp.status, tuple(resp.getheaders()), resp.read())
        except urllib.error.HTTPError as err:
            return RecordedResponse(err.code, tuple(err.headers.items()), err.read())


cassette = Cassette(
    Path("fixtures/api.json"),
    mode="once",  # replay if the file exists, record otherwise
    redactor=Redactor(),
)
transport = CassetteTransport(cassette, UrllibTransport())

request = RecordedRequest(
    "GET",
    "https://api.example.com/items?page=1",
    (("Authorization", "Bearer secret"),),
    b"",
)
response = transport.send(request)  # recorded first run, replayed afterwards
print(response.status)
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md) and [decisions](docs/decisions/)
- [Testing](docs/TESTING.md) and [security](docs/SECURITY.md)
- [Project record](docs/PROJECT.md) and [stack](docs/STACK.md)
- [Changelog](CHANGELOG.md) and [contributing guide](CONTRIBUTING.md)

## License

[MIT](LICENSE) (c) 2026 Miguel Gomez
