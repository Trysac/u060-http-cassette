"""Behavior tests for the public API of http_cassette."""

from __future__ import annotations

import pytest

import http_cassette


def test_normalize_trims_and_lowercases() -> None:
    assert http_cassette.normalize("  Hello World ") == "hello world"


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_normalize_rejects_blank_input(value: str) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        http_cassette.normalize(value)


def test_version_is_semver() -> None:
    parts = http_cassette.__version__.split(".")
    assert len(parts) == 3
    assert all(part.isdigit() for part in parts)
