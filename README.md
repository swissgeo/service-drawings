# Service Drawings

| Branch | Status | Coverage |
|--------|-----------|-----------|
| develop | ![Build Status](https://codebuild.eu-central-1.amazonaws.com/badges?uuid=eyJlbmNyeXB0ZWREYXRhIjoiV0d2bE9NelpZbFZYc0ZPMnpsWlRCNWFaWi9hSGZBZ3NxRFlPY1JYaWFlTUhSQWZ6alFaSTh6bERnRGxRZjdGbXNheW5jV015VmxXcGdWbWRpeE1KTmk0PSIsIml2UGFyYW1ldGVyU3BlYyI6Ik9aekxVcjBEakFuNFpkVzYiLCJtYXRlcmlhbFNldFNlcmlhbCI6MX0%3D&branch=develop) | [![codecov-main](https://codecov.io/gh/swissgeo/service-drawings/branch/main/graph/badge.svg)](https://codecov.io/gh/swissgeo/service-drawings) |
| main | ![Build Status](https://codebuild.eu-central-1.amazonaws.com/badges?uuid=eyJlbmNyeXB0ZWREYXRhIjoiV0d2bE9NelpZbFZYc0ZPMnpsWlRCNWFaWi9hSGZBZ3NxRFlPY1JYaWFlTUhSQWZ6alFaSTh6bERnRGxRZjdGbXNheW5jV015VmxXcGdWbWRpeE1KTmk0PSIsIml2UGFyYW1ldGVyU3BlYyI6Ik9aekxVcjBEakFuNFpkVzYiLCJtYXRlcmlhbFNldFNlcmlhbCI6MX0%3D&branch=main) | [![codecov-develop](https://codecov.io/gh/swissgeo/service-drawings/branch/develop/graph/badge.svg)](https://codecov.io/gh/swissgeo/service-drawings) |

## Description

**Service Drawings** is an S3-only file store for KMZ drawing files. It exposes a small REST API to upload, download, update, delete, and inspect drawings, storing each file under a UUID4 key (`drawings/{uuid}.kmz`) in an S3 bucket.

Built with **Python 3.14** / **FastAPI**, it is fully **async** (aioboto3) and uses **OpenTelemetry** for observability. There is **no database**, the S3 bucket is the single source of truth.

## Table of Contents

- [Service Drawings](#service-drawings)
  - [Description](#description)
  - [Table of Contents](#table-of-contents)
  - [Quick Start](#quick-start)
  - [API Endpoints](#api-endpoints)
    - [Upload a drawing](#upload-a-drawing)
    - [Download a drawing](#download-a-drawing)
    - [Update a drawing](#update-a-drawing)
    - [Delete a drawing](#delete-a-drawing)
    - [Check a drawing_id / admin_id pair](#check-a-drawing_id--admin_id-pair)
    - [Read a drawing's metadata](#read-a-drawings-metadata)
    - [Health checks (internal)](#health-checks-internal)
    - [OpenAPI documentation](#openapi-documentation)
    - [Error responses](#error-responses)
  - [Make Commands](#make-commands)
  - [Smoke Tests](#smoke-tests)
    - [`scripts/smoke_test_drawings_api.sh`](#scriptssmoke_test_drawings_apish)
    - [`scripts/test_s3_local.py`](#scriptstest_s3_localpy)

## Quick Start

```bash
make setup          # creates .env, installs dependencies, starts moto + OTEL, opens a shell
make serve          # starts the dev server on http://localhost:8000
```

> The first time, `make setup` also copies `.env.default` to `.env` and installs the pre-commit hooks. Local S3 is emulated by [moto](https://getmoto.org/) (`make start-moto`), so no real AWS credentials are required for development.

## API Endpoints

All endpoints are served under the `/api/wps/v1` prefix (SWISSGEO convention). The examples below assume a local server on `http://localhost:8000` and use `France.kmz` as a placeholder file.

### Upload a drawing

`POST /api/wps/v1/drawings`

Uploads a KMZ file. The request is multipart with two fields: the `file` and its `sha256` hex digest (computed on the file bytes before upload, used to verify content integrity).

```bash
SHA256=$(sha256sum -b France.kmz | awk '{print $1}')

curl -sS -X POST \
  -F "file=@France.kmz" \
  -F "sha256=$SHA256" \
  http://localhost:8000/api/wps/v1/drawings
```

**Response** — `201 Created`:

```json
{
  "id": "f0c4d7a2-...-uuid4",
  "admin_id": "b1a5c8e3-...-uuid4",
  "s3_url": "http://localhost:8000/api/wps/v1/drawings/f0c4d7a2-..."
}
```

The `admin_id` is the secret token required to update or delete the drawing. Keep it safe.

### Download a drawing

`GET /api/wps/v1/drawings/{drawing_id}`

Streams the KMZ file from S3 as an attachment download, under the filename recorded at the last upload.

```bash
curl -sS -o drawing.kmz \
  http://localhost:8000/api/wps/v1/drawings/f0c4d7a2-...
```

**Response** — `200 OK` with `Content-Type: application/vnd.google-earth.kmz` and a `Content-Disposition` naming the file as it was uploaded, so browsers save it under its original name:

```
Content-Disposition: attachment; filename="Zurich.kmz"; filename*=UTF-8''Z%C3%BCrich.kmz
```

The name is carried twice, per [RFC 6266](https://datatracker.ietf.org/doc/html/rfc6266): `filename*` holds the exact UTF-8 name and is what every current browser uses, while the bare `filename` is an ASCII-only fallback in which non-ASCII characters, quotes and control characters are replaced by `_`. Drawings uploaded before the service started recording filenames fall back to `{drawing_id}.kmz`.

Because the name comes from the *last* upload, and `PUT` skips the S3 write when the content is unchanged, re-uploading identical bytes under a new name leaves the download name as it was.

### Update a drawing

`PUT /api/wps/v1/drawings/{drawing_id}`

Overwrites an existing drawing at the same S3 key. Requires the `admin_id` returned at creation, otherwise the request is rejected with `403`. If the new content is identical to the stored one, the request succeeds without re-upload.

```bash
SHA256=$(sha256sum -b France.kmz | awk '{print $1}')

curl -sS -X PUT \
  -F "file=@France.kmz" \
  -F "sha256=$SHA256" \
  -F "admin_id=b1a5c8e3-..." \
  http://localhost:8000/api/wps/v1/drawings/f0c4d7a2-...
```

**Response** — `200 OK`:

```json
{
  "id": "f0c4d7a2-...",
  "admin_id": "b1a5c8e3-...",
  "s3_url": "http://localhost:8000/api/wps/v1/drawings/f0c4d7a2-...",
  "created_at": "2026-01-01T12:00:00+00:00",
  "modified_at": "2026-01-02T09:30:00+00:00"
}
```

### Delete a drawing

`DELETE /api/wps/v1/drawings/{drawing_id}`

Permanently deletes a drawing. Requires the `admin_id` as a form field. The deletion is irreversible.

```bash
curl -sS -X DELETE \
  -F "admin_id=b1a5c8e3-..." \
  http://localhost:8000/api/wps/v1/drawings/f0c4d7a2-...
```

**Response** — `204 No Content` (empty body).

### Check a drawing_id / admin_id pair

`PUT /api/wps/v1/drawings/{drawing_id}/is-valid`

Reports whether a `drawing_id` and `admin_id` combination identifies an existing drawing, without transferring the file. Useful for a client to confirm it still holds admin rights before offering an edit or delete action.

The response is always `200`. An unknown `drawing_id` and a wrong `admin_id` are reported identically (`is_valid: false`), so the endpoint cannot be used to discover which drawings exist. `PUT` is used rather than `GET` so the `admin_id` travels in the request body instead of the URL, keeping it out of access logs and browser history.

```bash
curl -sS -X PUT \
  -F "admin_id=b1a5c8e3-..." \
  http://localhost:8000/api/wps/v1/drawings/f0c4d7a2-.../is-valid
```

**Response** — `200 OK`:

```json
{
  "is_valid": true
}
```

### Read a drawing's metadata

`PUT /api/wps/v1/drawings/{drawing_id}/metadata`

Returns a drawing's stored metadata without downloading its content: the filename the client used at the last upload, plus the creation and last-update timestamps. Requires the `admin_id`, otherwise the request is rejected with `403`. As above, `PUT` keeps the `admin_id` out of the URL.

```bash
curl -sS -X PUT \
  -F "admin_id=b1a5c8e3-..." \
  http://localhost:8000/api/wps/v1/drawings/f0c4d7a2-.../metadata
```

**Response** — `200 OK`:

```json
{
  "id": "f0c4d7a2-...",
  "original_filename": "France.kmz",
  "created_at": "2026-01-01T12:00:00+00:00",
  "modified_at": "2026-01-02T09:30:00+00:00"
}
```

`original_filename` is the basename of what the client sent: any directory component is stripped, and a name that is purely path syntax falls back to `{drawing_id}.kmz`. Non-ASCII names are preserved (they are percent-encoded in S3 metadata, which must be ASCII, and decoded on read). It is `null` for drawings uploaded before the service started recording filenames.

Note that the unchanged-content short-circuit on `PUT /drawings/{drawing_id}` skips the S3 write entirely, so re-uploading identical bytes under a new name leaves `original_filename` unchanged.

### Health checks (internal)

`GET /checker` and `GET /checker/ready`

Liveness and readiness probes used by Kubernetes. These routes are tagged `Internal` and are **excluded from the public OpenAPI spec**.

```bash
curl -sS http://localhost:8000/checker
curl -sS http://localhost:8000/checker/ready
```

**Response** — `200 OK`:

```json
{
  "success": true,
  "message": "OK",
  "version": "v0.1.0-beta.1"
}
```

`version` is resolved at import time via `git describe --tags` (falling back to a short commit hash when no tag exists).

### OpenAPI documentation

Available when `PUBLISH_OPENAPI_SPEC=1` (set in `.env.default`):

- `GET /openapi.json` — public spec (drawings API only)
- `GET /internal/openapi.json` — internal spec (health routes only)
- `GET /internal/docs`, `GET /internal/redoc` — internal Swagger UI / ReDoc
- `GET /docs`, `GET /redoc` — public Swagger UI / ReDoc

### Error responses

Application errors follow a uniform `{"detail": "<message>"}` body. The `422` status is reserved for FastAPI request-validation errors (e.g. a missing form field) and uses FastAPI's standard validation body.

| Status | Meaning |
|--------|---------|
| `400` | Invalid KMZ file (not a valid zip) or SHA-256 digest mismatch |
| `403` | `admin_id` does not match the stored drawing metadata |
| `404` | Drawing not found |
| `413` | Uploaded body exceeds the maximum size (5 MB by default, configurable via `MAX_UPLOAD_SIZE_BYTES`) |
| `422` | Request validation error (e.g. missing `admin_id` form field) |
| `500` | Storage (S3) operation failed |

## Make Commands

The project is driven by `make` targets. Run `make help` to display them all.

| Target | Description |
|--------|-------------|
| `make setup` | Full local environment: creates `.env`, installs dependencies, installs pre-commit hooks, starts moto and OTEL, then drops into a shell with the virtualenv activated |
| `make sync` | Install dependencies from the lockfile (`uv sync --frozen`) |
| `make serve` | Start the dev server on port `8000` (override with `HTTP_PORT=8080`) |
| `make lint` | Run the linter and type checker (`ruff check` + `ty check`) |
| `make check` | Full CI gate locally: lint + typecheck + tests |
| `make test` | Run tests with branch coverage (terminal + HTML report) |
| `make test-ci` | Run tests with XML coverage report for Codecov |
| `make format` | Auto-format the code (`ruff format` + isort import sorting) |
| `make ci-check-format` | Format and fail if the working tree is dirty (CI) |
| `make start-moto` | Start the moto S3 emulator container and initialize the bucket |
| `make stop-moto` | Stop the moto container |
| `make start-otel` | Start the OpenTelemetry collector and Jaeger trace analyzer |
| `make stop-otel` | Stop the OTEL collector and Jaeger |
| `make dockerlogin` | Log in to the AWS ECR registry |
| `make dockerbuild` | Build the Docker image with git metadata as build args |
| `make dockerpush` | Build and push the image to the registry |
| `make dockerrun` | Build and run the image locally with the configured env file |
| `make git-info` | Print the version metadata baked into the build |
| `make help` | Display all available targets |

## Smoke Tests

Two standalone smoke tests live in `scripts/`. They validate the service without going through the pytest suite.

### `scripts/smoke_test_drawings_api.sh`

End-to-end API smoke test. It generates sample KMZ files, starts a dev server, and exercises the full HTTP surface of the API: health endpoints, upload (valid/invalid/oversized), download (content-type, `Content-Disposition` filename, byte-for-byte match), update (wrong `admin_id`, missing drawing, unchanged content short-circuit), `is-valid` (matching pair, wrong `admin_id`, unknown drawing), `metadata` (filename + timestamps, wrong `admin_id`, missing drawing), delete (including the `422` when `admin_id` is missing, and that both `is-valid` and `metadata` stop answering afterwards), and OpenAPI spec exposure.

```bash
make start-moto                                  # required: local S3 emulator
bash scripts/smoke_test_drawings_api.sh          # run it
bash scripts/smoke_test_drawings_api.sh -v       # verbose: show server output
```

**Requirements:** `moto` running, `curl`, `zip`, `python3`.
**Environment:** override the server URL with `BASE_URL` (e.g. `BASE_URL=http://localhost:9000 bash scripts/smoke_test_drawings_api.sh`). The script exits non-zero if any assertion fails.

### `scripts/test_s3_local.py`

Smoke test for the `S3Service` layer against the local moto server. It uploads a KMZ file, checks the stored metadata (SHA-256), streams it back and compares bytes, verifies `DrawingNotFoundError` on missing keys, and finally deletes the object.

```bash
make start-moto                                   # required: local S3 emulator
uv run python scripts/test_s3_local.py ./France.kmz
```

**Requirements:** `moto` running and a KMZ file path as argument (the sample `France.kmz` at the repository root works).
**Environment:** `AWS_S3_ENDPOINT_URL` (default `http://localhost:5000`) and `AWS_S3_BUCKET_NAME` (default `service-drawings-local`)
