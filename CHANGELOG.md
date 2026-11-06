# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Add `http_cassette`: record and replay HTTP interactions through an injectable transport (`Cassette`, `CassetteTransport`, `Redactor`), redacting credentials in headers, URLs, JSON and form bodies before they reach disk.
- Initial project structure from the factory utility template.
