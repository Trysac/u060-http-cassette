"""Tests for the http_cassette public API."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

import http_cassette
from http_cassette import (
    Cassette,
    CassetteError,
    CassetteTransport,
    RecordedRequest,
    RecordedResponse,
    Redactor,
    Transport,
)

Headers = tuple[tuple[str, str], ...]

SECRET = "s3" + "cret" + "-val" + "ue-1"
OTHER_SECRET = "ot" + "her" + "-sec" + "ret-2"
USERINFO = "user:pw@"
JSON_HEADERS: Headers = (("Content-Type", "application/json"),)
FORM_HEADERS: Headers = (("Content-Type", "application/x-www-form-urlencoded"),)

GOLDEN = r"""{
  "interactions": [
    {
      "request": {
        "body": {
          "text": "{\"b\": 1, \"a\": 1.0, \"n\": 12345678901234567890}"
        },
        "headers": [
          [
            "Content-Type",
            "application/json"
          ]
        ],
        "method": "POST",
        "url": "https://api.example.com/v1/items?b=2&a=1"
      },
      "response": {
        "body": {
          "base64": "/wA="
        },
        "headers": [
          [
            "Content-Type",
            "application/octet-stream"
          ]
        ],
        "status": 200
      }
    }
  ],
  "version": 1
}
"""


class FakeTransport:
    """Inner transport returning canned responses and remembering its calls."""

    def __init__(self, *responses: RecordedResponse) -> None:
        self.responses = responses
        self.calls: list[RecordedRequest] = []

    def send(self, request: RecordedRequest) -> RecordedResponse:
        index = min(len(self.calls), len(self.responses) - 1)
        self.calls.append(request)
        return self.responses[index]


def make_request(
    url: str = "https://api.example.com/v1/items",
    *,
    method: str = "GET",
    headers: Headers = (),
    body: bytes = b"",
) -> RecordedRequest:
    return RecordedRequest(method=method, url=url, headers=headers, body=body)


def make_response(
    body: bytes = b'{"ok": true}',
    *,
    status: int = 200,
    headers: Headers = JSON_HEADERS,
) -> RecordedResponse:
    return RecordedResponse(status=status, headers=headers, body=body)


def record_to(path: Path, request: RecordedRequest, response: RecordedResponse) -> None:
    cassette = Cassette(path, mode="record")
    cassette.record(request, response)
    cassette.save()


def load_doc(path: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def valid_interaction() -> dict[str, Any]:
    return {
        "request": {
            "method": "GET",
            "url": "https://api.example.com/",
            "headers": [],
            "body": {"text": ""},
        },
        "response": {"status": 200, "headers": [], "body": {"text": ""}},
    }


def nested_json(levels: int, leaf: object) -> bytes:
    value: object = leaf
    for _ in range(levels - 1):
        value = {"a": value}
    return json.dumps(value).encode("utf-8")


# --- modes ---------------------------------------------------------------------------------


def test_version_and_logging_setup() -> None:
    assert http_cassette.__version__ == "0.1.0"
    handlers = logging.getLogger("http_cassette").handlers
    assert any(isinstance(handler, logging.NullHandler) for handler in handlers)


def test_record_mode_calls_inner_and_saves(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    inner = FakeTransport(make_response())
    transport: Transport = CassetteTransport(Cassette(path, mode="record"), inner)
    assert transport.send(make_request()) == make_response()
    assert len(inner.calls) == 1
    assert len(load_doc(path)["interactions"]) == 1


def test_record_mode_overwrites_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "overwrite.json"
    record_to(path, make_request("https://api.example.com/old"), make_response())
    inner = FakeTransport(make_response())
    CassetteTransport(Cassette(path, mode="record"), inner).send(make_request())
    assert len(load_doc(path)["interactions"]) == 1


def test_replay_mode_never_calls_inner(tmp_path: Path) -> None:
    path = tmp_path / "replay.json"
    record_to(path, make_request(), make_response())
    inner = FakeTransport(make_response(b'{"ok": false}'))
    transport = CassetteTransport(Cassette(path, mode="replay"), inner)
    assert transport.send(make_request()) == make_response()
    assert inner.calls == []


def test_once_mode_records_then_replays(tmp_path: Path) -> None:
    path = tmp_path / "once.json"
    first = FakeTransport(make_response())
    assert not Cassette(path, mode="once").replaying
    CassetteTransport(Cassette(path, mode="once"), first).send(make_request())
    assert len(first.calls) == 1
    second = FakeTransport(make_response(b'{"ok": false}'))
    replayer = Cassette(path, mode="once")
    assert replayer.replaying
    assert CassetteTransport(replayer, second).send(make_request()) == make_response()
    assert second.calls == []


def test_once_mode_replay_miss_does_not_call_inner(tmp_path: Path) -> None:
    path = tmp_path / "once-miss.json"
    record_to(path, make_request(), make_response())
    inner = FakeTransport(make_response())
    transport = CassetteTransport(Cassette(path), inner)
    with pytest.raises(CassetteError) as info:
        transport.send(make_request("https://api.example.com/v1/other"))
    assert info.value.code == "miss"
    assert inner.calls == []


def test_replay_with_missing_file_misses(tmp_path: Path) -> None:
    cassette = Cassette(tmp_path / "absent.json", mode="replay")
    assert cassette.replaying
    with pytest.raises(CassetteError) as info:
        cassette.lookup(make_request())
    assert info.value.code == "miss"


def test_recording_without_inner_raises_no_inner(tmp_path: Path) -> None:
    transport = CassetteTransport(Cassette(tmp_path / "x.json", mode="record"))
    with pytest.raises(CassetteError) as info:
        transport.send(make_request())
    assert info.value.code == "no_inner"


def test_invalid_mode_and_match_on_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(CassetteError) as mode_info:
        Cassette(tmp_path / "a.json", mode="append")  # type: ignore[arg-type]
    assert mode_info.value.code == "format"
    with pytest.raises(CassetteError) as match_info:
        Cassette(tmp_path / "a.json", match_on=("cookie",))  # type: ignore[arg-type]
    assert match_info.value.code == "format"


def test_save_creates_missing_directories(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "er" / "c.json"
    record_to(path, make_request(), make_response())
    assert path.is_file()


# --- misses and events ---------------------------------------------------------------------


def test_miss_error_names_method_and_redacted_url_and_events_are_minimal(tmp_path: Path) -> None:
    events: list[Mapping[str, str]] = []
    cassette = Cassette(tmp_path / "m.json", mode="replay", on_event=events.append)
    url = "https://api.example.com/v1/items?token=" + SECRET
    with pytest.raises(CassetteError) as info:
        cassette.lookup(make_request(url))
    message = str(info.value)
    redacted_url = "https://api.example.com/v1/items?token=REDACTED"
    assert info.value.code == "miss"
    assert "GET" in message
    assert redacted_url in message
    assert SECRET not in message
    assert len(message) <= 200
    assert events == [{"kind": "miss", "method": "GET", "url": redacted_url}]
    assert set(events[0]) == {"kind", "method", "url"}


def test_miss_message_is_truncated_to_200_characters(tmp_path: Path) -> None:
    cassette = Cassette(tmp_path / "m.json", mode="replay")
    with pytest.raises(CassetteError) as info:
        cassette.lookup(make_request("https://api.example.com/" + "a" * 500))
    assert len(str(info.value)) == 200


def test_record_and_hit_events_carry_only_three_keys(tmp_path: Path) -> None:
    path = tmp_path / "e.json"
    events: list[Mapping[str, str]] = []
    recorder = Cassette(path, mode="record", on_event=events.append)
    recorder.record(make_request("https://api.example.com/x?Token=" + SECRET), make_response())
    recorder.save()
    replayer = Cassette(path, mode="replay", on_event=events.append)
    replayer.lookup(make_request("https://api.example.com/x?Token=" + OTHER_SECRET))
    assert [event["kind"] for event in events] == ["record", "hit"]
    for event in events:
        assert set(event) == {"kind", "method", "url"}
        assert event["url"] == "https://api.example.com/x?Token=REDACTED"


def test_failing_event_callback_does_not_break_lookup(tmp_path: Path) -> None:
    path = tmp_path / "cb.json"
    record_to(path, make_request(), make_response())

    def boom(event: Mapping[str, str]) -> None:
        raise RuntimeError("callback failed")

    cassette = Cassette(path, mode="replay", on_event=boom)
    assert cassette.lookup(make_request()) == make_response()


# --- redaction: one test per defended item -------------------------------------------------


def test_headers_are_redacted_case_insensitively_and_exactly() -> None:
    headers: Headers = (
        ("Authorization", SECRET),
        ("COOKIE", SECRET),
        ("x-api-key", SECRET),
        ("Proxy-Authorization", SECRET),
        ("Authorization-Extra", "keep"),
        ("X-API-KEY2", "keep"),
    )
    out = Redactor().redact_request(make_request(headers=headers))
    assert [value for _, value in out.headers] == ["REDACTED"] * 4 + ["keep", "keep"]
    response = Redactor().redact_response(make_response(headers=(("Set-Cookie", SECRET),)))
    assert response.headers == (("Set-Cookie", "REDACTED"),)


@pytest.mark.parametrize("name", ["token", "access_token", "api_key", "key", "signature", "code"])
def test_default_query_names_are_redacted(name: str) -> None:
    out = Redactor().redact_url("https://api.example.com/p?" + name + "=" + SECRET + "&q=1")
    assert out == "https://api.example.com/p?" + name + "=REDACTED&q=1"


def test_query_names_exact_percent_decoded_and_every_occurrence() -> None:
    url = (
        "https://api.example.com/p?token="
        + SECRET
        + "&Token="
        + SECRET
        + "&tokens="
        + SECRET
        + "&acc%65ss_token="
        + SECRET
        + "&q=1"
    )
    expected = (
        "https://api.example.com/p?token=REDACTED&Token=REDACTED&tokens="
        + SECRET
        + "&acc%65ss_token=REDACTED&q=1"
    )
    assert Redactor().redact_url(url) == expected


def test_query_pair_without_equals_sign_is_redacted() -> None:
    assert Redactor().redact_url("https://api.example.com/p?token") == (
        "https://api.example.com/p?token=REDACTED"
    )


def test_url_userinfo_is_replaced() -> None:
    out = Redactor().redact_url("https://" + USERINFO + "api.example.com:8443/p")
    assert out == "https://REDACTED@api.example.com:8443/p"


def test_url_without_secrets_is_returned_unchanged() -> None:
    url = "https://api.example.com/a%20b?x=1&y#frag"
    assert Redactor().redact_url(url) == url


def test_invalid_and_oversized_urls_raise_typed_errors() -> None:
    with pytest.raises(CassetteError) as bad:
        Redactor().redact_url("http://[::1")
    assert bad.value.code == "format"
    with pytest.raises(CassetteError) as big:
        Redactor().redact_url("https://api.example.com/" + "a" * 70_000)
    assert big.value.code == "too_large"


def test_nested_json_fields_are_redacted_in_requests() -> None:
    payload = {"outer": {"password": SECRET}, "items": [{"password": SECRET}, 1, None]}
    request = make_request(method="POST", headers=JSON_HEADERS, body=json.dumps(payload).encode())
    out = Redactor().redact_request(request)
    expected = {"outer": {"password": "REDACTED"}, "items": [{"password": "REDACTED"}, 1, None]}
    assert json.loads(out.body) == expected
    assert SECRET.encode() not in out.body


def test_json_response_fields_are_redacted() -> None:
    body = json.dumps({"access_token": SECRET, "token_type": "bearer"}).encode()
    out = Redactor().redact_response(make_response(body))
    assert json.loads(out.body) == {"access_token": "REDACTED", "token_type": "bearer"}


@pytest.mark.parametrize(
    "field", ["password", "access_token", "refresh_token", "client_secret", "id_token"]
)
def test_default_body_fields_are_redacted(field: str) -> None:
    body = json.dumps({field: SECRET, "kept": 1}).encode()
    out = Redactor().redact_response(make_response(body))
    assert json.loads(out.body) == {field: "REDACTED", "kept": 1}


def test_json_field_names_are_case_insensitive_and_values_of_any_type() -> None:
    body = b'{"PassWord": {"deep": 1}, "id_token": null}'
    out = Redactor().redact_response(make_response(body))
    assert json.loads(out.body) == {"PassWord": "REDACTED", "id_token": "REDACTED"}


def test_form_fields_are_redacted_including_repeats() -> None:
    request = make_request(
        method="POST",
        headers=FORM_HEADERS,
        body=b"client_secret=a&client_secret=b",
    )
    out = Redactor().redact_request(request)
    assert out.body == b"client_secret=REDACTED&client_secret=REDACTED"


def test_form_redaction_keeps_other_fields_and_handles_charset_and_case() -> None:
    headers: Headers = (("content-type", "Application/X-WWW-Form-Urlencoded; charset=utf-8"),)
    body = b"grant_type=password&Client_Secret=s%20x&cl%69ent_secret=2&scope=a+b"
    out = Redactor().redact_request(make_request(headers=headers, body=body))
    assert out.body == (
        b"grant_type=password&Client_Secret=REDACTED&cl%69ent_secret=REDACTED&scope=a+b"
    )


def test_form_without_listed_fields_keeps_exact_bytes() -> None:
    body = b"a=1&&b=%zz&c"
    out = Redactor().redact_request(make_request(headers=FORM_HEADERS, body=body))
    assert out.body == body


def test_form_with_invalid_utf8_is_replaced_entirely() -> None:
    out = Redactor().redact_request(make_request(headers=FORM_HEADERS, body=b"a=\xff"))
    assert out.body == b"REDACTED"


def test_plus_json_content_type_is_treated_as_json() -> None:
    headers: Headers = (("Content-Type", "application/vnd.api+json; charset=utf-8"),)
    out = Redactor().redact_response(make_response(b'{"password": "x"}', headers=headers))
    assert out.body == b'{"password":"REDACTED"}'


def test_other_content_types_are_not_touched() -> None:
    body = b'{"password": "x"}'
    plain = Redactor().redact_response(
        make_response(body, headers=(("Content-Type", "text/plain"),))
    )
    assert plain.body == body
    untyped = Redactor().redact_response(make_response(body, headers=()))
    assert untyped.body == body


# --- JSON edge cases -----------------------------------------------------------------------


def test_json_neighbour_names_stay_unredacted() -> None:
    body = b'{"password_hint": "blue", "tokens": 3, "password": "x"}'
    out = Redactor().redact_response(make_response(body))
    assert json.loads(out.body) == {"password_hint": "blue", "tokens": 3, "password": "REDACTED"}


def test_body_with_redacted_field_is_reserialised_compactly() -> None:
    request = make_request(method="POST", headers=JSON_HEADERS, body=b'{"password": "x", "a": 1}')
    out = Redactor().redact_request(request)
    assert out.body == b'{"password":"REDACTED","a":1}'


def test_reserialisation_matches_json_dumps_spec() -> None:
    """Re-serialised with json.dumps(obj, separators=(',', ':'), ensure_ascii=False)."""
    literal = """b'{"password": "x", "a": 1}' becomes '{"password":"REDACTED","a":1}'"""
    spec = "), ensure_ascii=False), so b'"
    assert spec.startswith(")")
    assert "\": 1}' becomes '{\"" in literal
    request = make_request(method="POST", headers=JSON_HEADERS, body=b'{"password": "x", "a": 1}')
    out = Redactor().redact_request(request)
    expected = json.dumps(
        {"password": "REDACTED", "a": 1}, separators=(",", ":"), ensure_ascii=False
    )
    assert out.body.decode() == expected == '{"password":"REDACTED","a":1}'


def test_reserialisation_keeps_non_ascii_characters() -> None:
    body = '{"name": "caf\u00e9", "password": "x"}'.encode()
    out = Redactor().redact_response(make_response(body))
    assert out.body == '{"name":"caf\u00e9","password":"REDACTED"}'.encode()


def test_json_without_listed_fields_keeps_exact_bytes() -> None:
    body = b'{"b": 1, "a": 1.0, "n": 12345678901234567890}'
    out = Redactor().redact_request(make_request(headers=JSON_HEADERS, body=body))
    assert out.body == body


def test_empty_and_blank_json_bodies_are_kept() -> None:
    assert Redactor().redact_response(make_response(b"")).body == b""
    assert Redactor().redact_response(make_response(b"  \n")).body == b"  \n"


def test_unparsable_json_is_replaced_entirely() -> None:
    assert Redactor().redact_response(make_response(b"{not json")).body == b"REDACTED"
    assert Redactor().redact_response(make_response(b"\xff\xfe")).body == b"REDACTED"


def test_json_nested_65_levels_is_replaced_but_64_is_walked() -> None:
    too_deep = nested_json(65, {"password": "x"})
    assert Redactor().redact_response(make_response(too_deep)).body == b"REDACTED"
    deep_ok = Redactor().redact_response(make_response(nested_json(64, {"password": "x"}))).body
    assert deep_ok != b"REDACTED"
    assert b"REDACTED" in deep_ok
    assert b'"x"' not in deep_ok


def test_deep_nested_lists_are_replaced() -> None:
    body = b"[" * 65 + b"]" * 65
    assert Redactor().redact_response(make_response(body)).body == b"REDACTED"


def test_recursion_bomb_is_replaced_not_raised() -> None:
    body = b"[" * 200_000 + b"]" * 200_000
    assert Redactor().redact_response(make_response(body)).body == b"REDACTED"


def test_custom_redactor_configuration() -> None:
    redactor = Redactor(
        headers=["X-Custom"],
        query_params=["sig"],
        body_fields=["pin"],
        placeholder="XXX",
    )
    assert redactor.placeholder == "XXX"
    request = make_request(
        "https://u:p@h.example.com/?sig=1&token=2",
        headers=(("X-Custom", "v"), ("Authorization", "kept"), *JSON_HEADERS),
        body=b'{"pin": 1, "password": 2}',
    )
    out = redactor.redact_request(request)
    assert out.url == "https://XXX@h.example.com/?sig=XXX&token=2"
    assert out.headers[:2] == (("X-Custom", "XXX"), ("Authorization", "kept"))
    assert json.loads(out.body) == {"pin": "XXX", "password": 2}


def test_redact_request_is_idempotent() -> None:
    request = make_request(
        "https://" + USERINFO + "api.example.com/?token=" + SECRET,
        headers=(("Authorization", SECRET), *JSON_HEADERS),
        body=b'{"password": "x"}',
    )
    once = Redactor().redact_request(request)
    assert Redactor().redact_request(once) == once


# --- matching ------------------------------------------------------------------------------


def test_saved_file_redacts_every_defended_item(tmp_path: Path) -> None:
    path = tmp_path / "redacted.json"
    payload = {"outer": {"password": SECRET}, "items": [{"password": SECRET}]}
    request = make_request(
        "https://" + USERINFO + "api.example.com/v1/login?Token=" + SECRET + "&page=2",
        method="POST",
        headers=(
            ("Authorization", "Bearer " + SECRET),
            ("Cookie", "sid=" + SECRET),
            ("X-API-KEY", SECRET),
            ("Content-Type", "application/json"),
        ),
        body=json.dumps(payload).encode(),
    )
    response = make_response(
        json.dumps({"access_token": SECRET, "token_type": "bearer"}).encode(),
        headers=(("Set-Cookie", "sid=" + SECRET), ("Content-Type", "application/json")),
    )
    CassetteTransport(Cassette(path, mode="record"), FakeTransport(response)).send(request)
    raw = path.read_bytes()
    assert SECRET.encode() not in raw
    assert USERINFO.encode() not in raw
    saved = load_doc(path)["interactions"][0]
    expected_url = "https://REDACTED@api.example.com/v1/login?Token=REDACTED&page=2"
    assert saved["request"]["url"] == expected_url
    expected_headers = {
        "Authorization": "REDACTED",
        "Cookie": "REDACTED",
        "X-API-KEY": "REDACTED",
        "Content-Type": "application/json",
    }
    assert dict(saved["request"]["headers"]) == expected_headers
    expected_body = {"outer": {"password": "REDACTED"}, "items": [{"password": "REDACTED"}]}
    assert json.loads(saved["request"]["body"]["text"]) == expected_body
    expected_response_headers = {"Set-Cookie": "REDACTED", "Content-Type": "application/json"}
    assert dict(saved["response"]["headers"]) == expected_response_headers
    response_body = json.loads(saved["response"]["body"]["text"])
    assert response_body == {"access_token": "REDACTED", "token_type": "bearer"}


def test_saved_form_body_redacts_both_values(tmp_path: Path) -> None:
    path = tmp_path / "form.json"
    request = make_request(
        method="POST",
        headers=FORM_HEADERS,
        body=b"client_secret=a&client_secret=b",
    )
    record_to(path, request, make_response())
    saved = load_doc(path)["interactions"][0]
    assert saved["request"]["body"] == {"text": "client_secret=REDACTED&client_secret=REDACTED"}


def test_live_request_with_real_token_and_reordered_query_matches(tmp_path: Path) -> None:
    path = tmp_path / "live.json"
    recorded = make_request("https://api.example.com/v1/items?a=1&Token=" + SECRET + "&b=2")
    record_to(path, recorded, make_response())
    live = make_request("https://api.example.com/v1/items?b=2&Token=" + OTHER_SECRET + "&a=1")
    assert Cassette(path, mode="replay").lookup(live) == make_response()


def test_neighbour_query_name_tokens_is_not_equal_to_token(tmp_path: Path) -> None:
    path = tmp_path / "neighbour.json"
    record_to(path, make_request("https://api.example.com/v1/items?tokens=1"), make_response())
    cassette = Cassette(path, mode="replay")
    with pytest.raises(CassetteError):
        cassette.lookup(make_request("https://api.example.com/v1/items?tokens=2"))
    assert cassette.lookup(make_request("https://api.example.com/v1/items?tokens=1")) == (
        make_response()
    )


def test_identical_requests_replay_in_recorded_order_then_miss(tmp_path: Path) -> None:
    path = tmp_path / "order.json"
    first = make_response(b'{"n": 1}')
    second = make_response(b'{"n": 2}')
    inner = FakeTransport(first, second)
    recorder = CassetteTransport(Cassette(path, mode="record"), inner)
    assert recorder.send(make_request()) == first
    assert recorder.send(make_request()) == second
    replayer = Cassette(path, mode="replay")
    assert replayer.lookup(make_request()) == first
    assert replayer.lookup(make_request()) == second
    with pytest.raises(CassetteError) as info:
        replayer.lookup(make_request())
    assert info.value.code == "miss"


def test_default_matching_dimensions(tmp_path: Path) -> None:
    path = tmp_path / "dims.json"
    base = make_request("https://api.example.com/v1/items", method="POST", body=b"abc")
    record_to(path, base, make_response())
    variants = [
        make_request("https://api.example.com/v1/items", method="PUT", body=b"abc"),
        make_request("https://api.example.com/v1/other", method="POST", body=b"abc"),
        make_request("http://api.example.com/v1/items", method="POST", body=b"abc"),
        make_request("https://api.example.com/v1/items", method="POST", body=b"abd"),
        make_request("https://api.example.com/v1/items?x=1", method="POST", body=b"abc"),
    ]
    for variant in variants:
        with pytest.raises(CassetteError):
            Cassette(path, mode="replay").lookup(variant)
    same = make_request(
        "https://API.example.com/v1/items",
        method="post",
        headers=(("X-Other", "1"),),
        body=b"abc",
    )
    assert Cassette(path, mode="replay").lookup(same) == make_response()


def test_match_on_method_only(tmp_path: Path) -> None:
    path = tmp_path / "method.json"
    record_to(path, make_request("https://api.example.com/a"), make_response())
    cassette = Cassette(path, mode="replay", match_on=("method",))
    assert cassette.lookup(make_request("https://other.example.com/b")) == make_response()


def test_match_on_headers(tmp_path: Path) -> None:
    path = tmp_path / "headers.json"
    record_to(path, make_request(headers=(("Accept", "text/plain"),)), make_response())
    strict = Cassette(path, mode="replay", match_on=("method", "url", "headers"))
    with pytest.raises(CassetteError):
        strict.lookup(make_request(headers=(("Accept", "text/html"),)))
    live = make_request(headers=(("accept", "text/plain"),))
    assert strict.lookup(live) == make_response()


def test_match_on_redacted_headers_ignores_secret_values(tmp_path: Path) -> None:
    path = tmp_path / "secret-headers.json"
    record_to(path, make_request(headers=(("Authorization", SECRET),)), make_response())
    cassette = Cassette(path, mode="replay", match_on=("headers",))
    live = make_request(headers=(("Authorization", OTHER_SECRET),))
    assert cassette.lookup(live) == make_response()


# --- files, formats and limits -------------------------------------------------------------


def test_json_body_without_listed_fields_is_byte_exact_and_matches_golden(tmp_path: Path) -> None:
    path = tmp_path / "golden.json"
    body = b'{"b": 1, "a": 1.0, "n": 12345678901234567890}'
    request = make_request(
        "https://api.example.com/v1/items?b=2&a=1",
        method="POST",
        headers=JSON_HEADERS,
        body=body,
    )
    response = make_response(
        b"\xff\x00",
        headers=(("Content-Type", "application/octet-stream"),),
    )
    CassetteTransport(Cassette(path, mode="record"), FakeTransport(response)).send(request)
    assert path.read_text(encoding="utf-8") == GOLDEN
    replayed = Cassette(path, mode="replay", match_on=("method", "url", "query", "body"))
    served = replayed.lookup(request)
    assert served.body == b"\xff\x00"
    assert load_doc(path)["interactions"][0]["request"]["body"] == {"text": body.decode()}


def test_base64_body_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "binary.json"
    request = make_request(method="POST", body=b"\xff\x00")
    record_to(path, request, make_response(b"\xff\x00", headers=()))
    saved = load_doc(path)["interactions"][0]
    assert saved["request"]["body"] == {"base64": "/wA="}
    assert Cassette(path, mode="replay").lookup(request).body == b"\xff\x00"


def test_version_2_raises_format(tmp_path: Path) -> None:
    path = tmp_path / "v2.json"
    path.write_text(json.dumps({"version": 2, "interactions": []}), encoding="utf-8")
    with pytest.raises(CassetteError) as info:
        Cassette(path, mode="replay")
    assert info.value.code == "format"


def test_file_of_max_bytes_plus_one_raises_too_large(tmp_path: Path) -> None:
    path = tmp_path / "big.json"
    max_bytes = 100
    path.write_bytes(b" " * (max_bytes + 1))
    with pytest.raises(CassetteError) as info:
        Cassette(path, mode="replay", max_bytes=max_bytes)
    assert info.value.code == "too_large"
    path.write_bytes(b" " * max_bytes)
    with pytest.raises(CassetteError) as fmt:
        Cassette(path, mode="replay", max_bytes=max_bytes)
    assert fmt.value.code == "format"


def test_third_interaction_with_max_interactions_2_raises_and_saves_nothing(
    tmp_path: Path,
) -> None:
    path = tmp_path / "limit.json"
    cassette = Cassette(path, mode="record", max_interactions=2)
    transport = CassetteTransport(cassette, FakeTransport(make_response()))
    transport.send(make_request("https://api.example.com/1"))
    transport.send(make_request("https://api.example.com/2"))
    before = path.read_bytes()
    with pytest.raises(CassetteError) as info:
        transport.send(make_request("https://api.example.com/3"))
    assert info.value.code == "too_large"
    assert path.read_bytes() == before
    assert len(load_doc(path)["interactions"]) == 2


def test_too_many_interactions_in_file_raises_too_large(tmp_path: Path) -> None:
    path = tmp_path / "many.json"
    document = {"version": 1, "interactions": [valid_interaction() for _ in range(3)]}
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(CassetteError) as info:
        Cassette(path, mode="replay", max_interactions=2)
    assert info.value.code == "too_large"


def test_json_body_nested_65_levels_is_stored_as_redacted(tmp_path: Path) -> None:
    path = tmp_path / "deep.json"
    request = make_request(
        method="POST",
        headers=JSON_HEADERS,
        body=nested_json(65, {"password": SECRET}),
    )
    record_to(path, request, make_response())
    saved = load_doc(path)["interactions"][0]
    assert saved["request"]["body"] == {"text": "REDACTED"}
    assert SECRET.encode() not in path.read_bytes()


def test_saving_beyond_max_bytes_raises_too_large(tmp_path: Path) -> None:
    path = tmp_path / "toolarge.json"
    cassette = Cassette(path, mode="record", max_bytes=50)
    cassette.record(make_request(), make_response())
    with pytest.raises(CassetteError) as info:
        cassette.save()
    assert info.value.code == "too_large"
    assert not path.exists()


def test_failed_replace_removes_temp_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "fail.json"
    cassette = Cassette(path, mode="record")
    cassette.record(make_request(), make_response())

    def boom(src: object, dst: object) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk gone"):
        cassette.save()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b"\xff\xfe",
        b"[]",
        b'{"version": 1}',
        b'{"version": true, "interactions": []}',
        b'{"version": 1, "interactions": [1]}',
        b'{"version": 1, "interactions": [{"request": 1, "response": {}}]}',
    ],
)
def test_malformed_documents_raise_format(tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "bad.json"
    path.write_bytes(raw)
    with pytest.raises(CassetteError) as info:
        Cassette(path, mode="replay")
    assert info.value.code == "format"


BAD_FIELDS: list[tuple[str, str, object]] = [
    ("request", "method", 5),
    ("request", "url", None),
    ("request", "headers", "x"),
    ("request", "headers", [["a"]]),
    ("request", "headers", [["a", 1]]),
    ("request", "body", "text"),
    ("request", "body", {"text": "a", "base64": "YQ=="}),
    ("request", "body", {"base64": "!!"}),
    ("request", "body", {"text": "\ud800"}),
    ("request", "body", {"other": "x"}),
    ("response", "status", True),
    ("response", "status", "200"),
    ("response", "body", None),
]


@pytest.mark.parametrize(("section", "key", "value"), BAD_FIELDS)
def test_malformed_interactions_raise_format(
    tmp_path: Path, section: str, key: str, value: object
) -> None:
    item = valid_interaction()
    item[section][key] = value
    path = tmp_path / "bad-interaction.json"
    path.write_text(json.dumps({"version": 1, "interactions": [item]}), encoding="utf-8")
    with pytest.raises(CassetteError) as info:
        Cassette(path, mode="replay")
    assert info.value.code == "format"


def test_invalid_url_in_file_raises_format(tmp_path: Path) -> None:
    item = valid_interaction()
    item["request"]["url"] = "http://[::1"
    path = tmp_path / "bad-url.json"
    path.write_text(json.dumps({"version": 1, "interactions": [item]}), encoding="utf-8")
    with pytest.raises(CassetteError) as info:
        Cassette(path, mode="replay")
    assert info.value.code == "format"


def test_error_messages_never_exceed_200_characters() -> None:
    error = CassetteError("format", "x" * 1000)
    assert len(str(error)) == 200
    assert error.code == "format"
    assert isinstance(error, Exception)
