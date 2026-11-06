"""Record and replay HTTP interactions, redacting credentials before they reach disk.

``Cassette`` stores interactions in a JSON file, ``CassetteTransport`` wraps any object with a
``send(request) -> response`` method, and ``Redactor`` removes credentials from requests and
responses before anything is matched, stored or served. Replay serves the *redacted* response.

Threat model (see ``docs/SECURITY.md``):

Defended: header values named in ``Redactor(headers=...)``; query parameter values named in
``query_params`` (names compared case-insensitively and exactly, after percent-decoding, every
occurrence); URL userinfo (``user:pass@``); body fields named in ``body_fields`` in JSON bodies
(``application/json`` or ``+json``, nested up to 64 levels) and in
``application/x-www-form-urlencoded`` bodies, for requests and responses. A JSON body that is
unparsable or nested deeper than 64 levels is replaced entirely (fail closed).

Not defended: secrets in unlisted headers, parameters or fields; free-text bodies; response
bodies of other content types; secrets in URL path segments or fragments.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import json
import logging
import os
import tempfile
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import parse_qsl, quote, quote_plus, urlsplit, urlunsplit

__all__ = [
    "Cassette",
    "CassetteError",
    "CassetteTransport",
    "RecordedRequest",
    "RecordedResponse",
    "Redactor",
    "Transport",
    "__version__",
]

__version__ = "0.1.0"

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

Headers = tuple[tuple[str, str], ...]
ErrorCode = Literal["miss", "format", "too_large", "no_inner"]
Mode = Literal["record", "replay", "once"]
MatchField = Literal["method", "url", "query", "body", "headers"]

MAX_JSON_DEPTH = 64
MAX_URL_LENGTH = 65_536
MAX_MESSAGE_LENGTH = 200

DEFAULT_HEADERS: tuple[str, ...] = (
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
)
DEFAULT_QUERY_PARAMS: tuple[str, ...] = (
    "token",
    "access_token",
    "api_key",
    "key",
    "signature",
    "code",
)
DEFAULT_BODY_FIELDS: tuple[str, ...] = (
    "password",
    "access_token",
    "refresh_token",
    "client_secret",
    "id_token",
)

_MODES = frozenset({"record", "replay", "once"})
_MATCH_FIELDS = frozenset({"method", "url", "query", "body", "headers"})


class CassetteError(Exception):
    """Base error of the library; ``code`` is a stable machine-readable identifier.

    Codes: ``miss`` (no recorded interaction), ``format`` (invalid cassette or input),
    ``too_large`` (a size limit was exceeded) and ``no_inner`` (recording without a transport).
    Messages are truncated to 200 characters and never contain unredacted URLs.
    """

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message[:MAX_MESSAGE_LENGTH])
        self.code: ErrorCode = code


@dataclass(frozen=True)
class RecordedRequest:
    """An HTTP request as seen by a transport."""

    method: str
    url: str
    headers: Headers
    body: bytes


@dataclass(frozen=True)
class RecordedResponse:
    """An HTTP response as returned by a transport."""

    status: int
    headers: Headers
    body: bytes


class Transport(Protocol):
    """Anything that can send a request and return a response."""

    def send(self, request: RecordedRequest) -> RecordedResponse:
        """Send ``request`` and return the response."""
        ...


class _TooDeepError(Exception):
    """Raised internally when a JSON body is nested deeper than ``MAX_JSON_DEPTH``."""


def _query_name(part: str) -> str:
    """Return the percent-decoded name of one ``name=value`` pair (``""`` if there is none)."""
    pairs = parse_qsl(part, keep_blank_values=True)
    return pairs[0][0] if pairs else ""


def _media_type(headers: Headers) -> str:
    for name, value in headers:
        if name.lower() == "content-type":
            return value.split(";", 1)[0].strip().lower()
    return ""


class Redactor:
    """Remove credentials from requests and responses.

    Header and query names compare case-insensitively and exactly; body field names in JSON and
    form bodies compare case-insensitively and exactly. A body is re-serialised only when a field
    was redacted, otherwise its bytes are kept exactly.
    """

    def __init__(
        self,
        headers: Iterable[str] = DEFAULT_HEADERS,
        query_params: Iterable[str] = DEFAULT_QUERY_PARAMS,
        body_fields: Iterable[str] = DEFAULT_BODY_FIELDS,
        placeholder: str = "REDACTED",
    ) -> None:
        self._headers = frozenset(name.lower() for name in headers)
        self._query_params = frozenset(name.lower() for name in query_params)
        self._body_fields = frozenset(name.lower() for name in body_fields)
        self._placeholder = placeholder
        self._placeholder_bytes = placeholder.encode("utf-8")
        self._url_placeholder = quote(placeholder, safe="")
        self._form_placeholder = quote_plus(placeholder)

    @property
    def placeholder(self) -> str:
        """The text that replaces every redacted value."""
        return self._placeholder

    def redact_url(self, url: str) -> str:
        """Return ``url`` with userinfo and listed query parameter values replaced.

        Raises:
            CassetteError: ``too_large`` for URLs over 65,536 characters, ``format`` if the URL
                cannot be parsed.
        """
        if len(url) > MAX_URL_LENGTH:
            raise CassetteError("too_large", "URL exceeds the size limit")
        try:
            parts = urlsplit(url)
        except ValueError as exc:
            raise CassetteError("format", "invalid URL") from exc
        netloc = parts.netloc
        changed = False
        if "@" in netloc:
            netloc = f"{self._url_placeholder}@{netloc.rpartition('@')[2]}"
            changed = True
        pieces: list[str] = []
        for part in parts.query.split("&"):
            if _query_name(part).lower() in self._query_params:
                pieces.append(f"{part.partition('=')[0]}={self._url_placeholder}")
                changed = True
            else:
                pieces.append(part)
        if not changed:
            return url
        return urlunsplit((parts.scheme, netloc, parts.path, "&".join(pieces), parts.fragment))

    def redact_request(self, request: RecordedRequest) -> RecordedRequest:
        """Return a copy of ``request`` with headers, URL and body redacted."""
        return RecordedRequest(
            method=request.method,
            url=self.redact_url(request.url),
            headers=self._redact_headers(request.headers),
            body=self._redact_body(request.headers, request.body),
        )

    def redact_response(self, response: RecordedResponse) -> RecordedResponse:
        """Return a copy of ``response`` with headers and body redacted."""
        return RecordedResponse(
            status=response.status,
            headers=self._redact_headers(response.headers),
            body=self._redact_body(response.headers, response.body),
        )

    def _redact_headers(self, headers: Headers) -> Headers:
        return tuple(
            (name, self._placeholder if name.lower() in self._headers else value)
            for name, value in headers
        )

    def _redact_body(self, headers: Headers, body: bytes) -> bytes:
        if not body:
            return body
        media = _media_type(headers)
        if media == "application/json" or media.endswith("+json"):
            return self._redact_json(body)
        if media == "application/x-www-form-urlencoded":
            return self._redact_form(body)
        return body

    def _redact_json(self, body: bytes) -> bytes:
        try:
            text = body.decode("utf-8")
            if not text.strip():
                return body
            redacted, changed = self._walk(json.loads(text), 1)
            if not changed:
                return body
            dumped = json.dumps(redacted, separators=(",", ":"), ensure_ascii=False)
            return dumped.encode("utf-8")
        except (ValueError, RecursionError, _TooDeepError):
            return self._placeholder_bytes

    def _walk(self, value: Any, depth: int) -> tuple[Any, bool]:
        if isinstance(value, dict):
            return self._walk_dict(value, depth)
        if isinstance(value, list):
            return self._walk_list(value, depth)
        return value, False

    def _walk_dict(self, value: dict[Any, Any], depth: int) -> tuple[Any, bool]:
        if depth > MAX_JSON_DEPTH:
            raise _TooDeepError
        out: dict[Any, Any] = {}
        changed = False
        for key, item in value.items():
            if key.lower() in self._body_fields:
                out[key] = self._placeholder
                changed = True
            else:
                new_item, item_changed = self._walk(item, depth + 1)
                out[key] = new_item
                changed = changed or item_changed
        return out, changed

    def _walk_list(self, value: list[Any], depth: int) -> tuple[Any, bool]:
        if depth > MAX_JSON_DEPTH:
            raise _TooDeepError
        items: list[Any] = []
        changed = False
        for item in value:
            new_item, item_changed = self._walk(item, depth + 1)
            items.append(new_item)
            changed = changed or item_changed
        return items, changed

    def _redact_form(self, body: bytes) -> bytes:
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return self._placeholder_bytes
        pieces: list[str] = []
        changed = False
        for part in text.split("&"):
            if _query_name(part).lower() in self._body_fields:
                pieces.append(f"{part.partition('=')[0]}={self._form_placeholder}")
                changed = True
            else:
                pieces.append(part)
        if not changed:
            return body
        return "&".join(pieces).encode("utf-8")


@dataclass(frozen=True)
class _Entry:
    request: RecordedRequest
    response: RecordedResponse
    key: tuple[object, ...]


def _bad(what: str) -> CassetteError:
    return CassetteError("format", f"invalid cassette: {what}")


def _parse_headers(value: object) -> Headers:
    if not isinstance(value, list):
        raise _bad("headers")
    out: list[tuple[str, str]] = []
    for item in value:
        if not (
            isinstance(item, list)
            and len(item) == 2
            and isinstance(item[0], str)
            and isinstance(item[1], str)
        ):
            raise _bad("header")
        out.append((item[0], item[1]))
    return tuple(out)


def _parse_body(value: object) -> bytes:
    if not isinstance(value, dict) or len(value) != 1:
        raise _bad("body")
    text = value.get("text")
    if isinstance(text, str):
        try:
            return text.encode("utf-8")
        except UnicodeEncodeError:
            raise _bad("text body") from None
    encoded = value.get("base64")
    if isinstance(encoded, str):
        try:
            return base64.b64decode(encoded, validate=True)
        except binascii.Error:
            raise _bad("base64 body") from None
    raise _bad("body")


def _parse_interaction(item: object) -> tuple[RecordedRequest, RecordedResponse]:
    if not isinstance(item, dict):
        raise _bad("interaction")
    req = item.get("request")
    resp = item.get("response")
    if not isinstance(req, dict) or not isinstance(resp, dict):
        raise _bad("interaction")
    method = req.get("method")
    url = req.get("url")
    status = resp.get("status")
    if (
        not isinstance(method, str)
        or not isinstance(url, str)
        or not isinstance(status, int)
        or isinstance(status, bool)
    ):
        raise _bad("interaction fields")
    request = RecordedRequest(
        method=method,
        url=url,
        headers=_parse_headers(req.get("headers")),
        body=_parse_body(req.get("body")),
    )
    response = RecordedResponse(
        status=status,
        headers=_parse_headers(resp.get("headers")),
        body=_parse_body(resp.get("body")),
    )
    return request, response


def _encode_body(body: bytes) -> dict[str, str]:
    try:
        return {"text": body.decode("utf-8")}
    except UnicodeDecodeError:
        return {"base64": base64.b64encode(body).decode("ascii")}


def _entry_to_json(entry: _Entry) -> dict[str, Any]:
    return {
        "request": {
            "method": entry.request.method,
            "url": entry.request.url,
            "headers": [list(pair) for pair in entry.request.headers],
            "body": _encode_body(entry.request.body),
        },
        "response": {
            "status": entry.response.status,
            "headers": [list(pair) for pair in entry.response.headers],
            "body": _encode_body(entry.response.body),
        },
    }


class Cassette:
    """A file of recorded interactions that are matched on their redacted form.

    Modes: ``record`` starts empty and overwrites the file on save; ``replay`` loads the file (a
    missing file means no interactions); ``once`` replays when the file exists and records
    otherwise. Matching interactions are served once each, in recorded order.
    """

    def __init__(
        self,
        path: Path,
        *,
        mode: Mode = "once",
        match_on: Sequence[MatchField] = ("method", "url", "query", "body"),
        redactor: Redactor | None = None,
        max_bytes: int = 52_428_800,
        max_interactions: int = 10_000,
        on_event: Callable[[Mapping[str, str]], None] | None = None,
    ) -> None:
        if mode not in _MODES:
            raise CassetteError("format", "invalid mode")
        if any(field not in _MATCH_FIELDS for field in match_on):
            raise CassetteError("format", "invalid match_on field")
        self._path = Path(path)
        self._match_on: tuple[MatchField, ...] = tuple(match_on)
        self._redactor = redactor if redactor is not None else Redactor()
        self._max_bytes = max_bytes
        self._max_interactions = max_interactions
        self._on_event = on_event
        self._entries: list[_Entry] = []
        self._used: list[bool] = []
        self._lock = threading.Lock()
        exists = self._path.is_file()
        self._replaying = mode == "replay" or (mode == "once" and exists)
        if self._replaying and exists:
            self._load()

    @property
    def replaying(self) -> bool:
        """True when requests are served from the cassette instead of being recorded."""
        return self._replaying

    def lookup(self, request: RecordedRequest) -> RecordedResponse:
        """Return the next unused recorded response matching ``request``.

        Raises:
            CassetteError: ``miss`` when no unused interaction matches.
        """
        redacted = self._redactor.redact_request(request)
        key = self._match_key(redacted)
        found: RecordedResponse | None = None
        with self._lock:
            for index, entry in enumerate(self._entries):
                if not self._used[index] and entry.key == key:
                    self._used[index] = True
                    found = entry.response
                    break
        if found is None:
            self._emit("miss", redacted)
            raise CassetteError(
                "miss", f"no recorded interaction for {redacted.method} {redacted.url}"
            )
        self._emit("hit", redacted)
        return found

    def record(self, request: RecordedRequest, response: RecordedResponse) -> None:
        """Redact and append an interaction in memory; call ``save`` to persist it.

        Raises:
            CassetteError: ``too_large`` when ``max_interactions`` is already reached.
        """
        redacted_request = self._redactor.redact_request(request)
        redacted_response = self._redactor.redact_response(response)
        key = self._match_key(redacted_request)
        with self._lock:
            if len(self._entries) >= self._max_interactions:
                raise CassetteError("too_large", "cassette interaction limit reached")
            self._entries.append(_Entry(redacted_request, redacted_response, key))
            self._used.append(False)
        self._emit("record", redacted_request)

    def save(self) -> None:
        """Write the cassette atomically (temp file in the same directory, then ``os.replace``).

        Raises:
            CassetteError: ``too_large`` when the serialised file exceeds ``max_bytes``.
        """
        with self._lock:
            document = {
                "version": 1,
                "interactions": [_entry_to_json(entry) for entry in self._entries],
            }
        data = (json.dumps(document, sort_keys=True, indent=2) + "\n").encode("utf-8")
        if len(data) > self._max_bytes:
            raise CassetteError("too_large", "cassette exceeds max_bytes")
        directory = self._path.parent
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=self._path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp_name, self._path)
        except BaseException:
            with contextlib.suppress(OSError):
                Path(tmp_name).unlink()
            raise

    def _load(self) -> None:
        with self._path.open("rb") as handle:
            raw = handle.read(self._max_bytes + 1)
        if len(raw) > self._max_bytes:
            raise CassetteError("too_large", "cassette file exceeds max_bytes")
        try:
            document = json.loads(raw.decode("utf-8"))
        except (ValueError, RecursionError):
            raise _bad("not valid JSON") from None
        if not isinstance(document, dict):
            raise _bad("root must be an object")
        version = document.get("version")
        if type(version) is not int or version != 1:
            raise CassetteError("format", "unsupported cassette version")
        items = document.get("interactions")
        if not isinstance(items, list):
            raise _bad("interactions must be a list")
        if len(items) > self._max_interactions:
            raise CassetteError("too_large", "cassette has too many interactions")
        for item in items:
            request, response = _parse_interaction(item)
            self._entries.append(_Entry(request, response, self._match_key(request)))
            self._used.append(False)

    def _match_key(self, request: RecordedRequest) -> tuple[object, ...]:
        try:
            parts = urlsplit(request.url)
        except ValueError:
            raise _bad("invalid URL") from None
        key: list[object] = []
        for field in self._match_on:
            if field == "method":
                key.append(request.method.upper())
            elif field == "url":
                host = parts.netloc.rpartition("@")[2].lower()
                key.append((parts.scheme.lower(), host, parts.path))
            elif field == "query":
                key.append(tuple(sorted(parse_qsl(parts.query, keep_blank_values=True))))
            elif field == "body":
                key.append(hashlib.sha256(request.body).hexdigest())
            else:
                key.append(tuple(sorted((name.lower(), value) for name, value in request.headers)))
        return tuple(key)

    def _emit(self, kind: str, request: RecordedRequest) -> None:
        logger.debug("cassette event: %s", kind)
        if self._on_event is None:
            return
        payload = {
            "kind": kind,
            "method": request.method[:MAX_MESSAGE_LENGTH],
            "url": request.url[:MAX_MESSAGE_LENGTH],
        }
        try:
            self._on_event(payload)
        except Exception:
            logger.warning("on_event callback raised an exception; ignored")


class CassetteTransport:
    """A ``Transport`` that replays from a ``Cassette`` or records through an inner transport.

    When recording, the live (unredacted) response is returned to the caller and the redacted
    interaction is saved immediately. When replaying, the redacted recorded response is served.
    """

    def __init__(self, cassette: Cassette, inner: Transport | None = None) -> None:
        self._cassette = cassette
        self._inner = inner

    def send(self, request: RecordedRequest) -> RecordedResponse:
        """Serve ``request`` from the cassette or record it through the inner transport.

        Raises:
            CassetteError: ``miss`` on replay misses, ``no_inner`` when recording without an
                inner transport, ``too_large`` when a limit is exceeded.
        """
        if self._cassette.replaying:
            return self._cassette.lookup(request)
        if self._inner is None:
            raise CassetteError("no_inner", "recording requires an inner transport")
        response = self._inner.send(request)
        self._cassette.record(request, response)
        self._cassette.save()
        return response
