# http-cassette

[![PyPI version](https://img.shields.io/pypi/v/trysac-http-cassette)](https://pypi.org/project/trysac-http-cassette/)
[![License: MIT](https://img.shields.io/pypi/l/trysac-http-cassette)](LICENSE)
[![CI](https://github.com/Trysac/u060-http-cassette/actions/workflows/ci.yml/badge.svg)](https://github.com/Trysac/u060-http-cassette/actions/workflows/ci.yml)

Record and replay HTTP interactions through an injectable transport, redacting credentials in headers, URLs, JSON and form bodies before disk

## Problem

Describe in two or three sentences the concrete problem this utility solves, who hits it, and what
they do today without it. Replace this paragraph when the first real feature lands.

## Install

```sh
pip install trysac-http-cassette
# or
uv add trysac-http-cassette
```

Requires Python 3.10 or newer. No runtime dependencies.

## Usage

```python
import http_cassette

# normalize() trims surrounding whitespace and lower-cases the text.
print(http_cassette.normalize("  Hello World "))  # -> "hello world"

# Blank input is rejected explicitly instead of returning an empty string.
try:
    http_cassette.normalize("   ")
except ValueError as exc:
    print(f"rejected: {exc}")
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md) and [decisions](docs/decisions/)
- [Testing](docs/TESTING.md) and [security](docs/SECURITY.md)
- [Project record](docs/PROJECT.md) and [stack](docs/STACK.md)
- [Changelog](CHANGELOG.md) and [contributing guide](CONTRIBUTING.md)

## License

[MIT](LICENSE) (c) 2026 Miguel Gomez
