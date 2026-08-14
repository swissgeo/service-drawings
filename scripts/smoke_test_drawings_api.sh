#!/usr/bin/env bash
# Smoke test for the service-drawings API
# Usage:    bash scripts/smoke_test_drawings_api.sh [-v]
#   -v      Verbose: show uvicorn server output
# Requires: moto server running (make start-moto), curl, zip

set -euo pipefail

VERBOSE=false
while getopts "v" opt; do
    case "$opt" in
        v) VERBOSE=true ;;
        *) echo "Usage: $0 [-v]"; exit 1 ;;
    esac
done

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

PASS=0
FAIL=0
BASE_URL="${BASE_URL:-http://localhost:8000}"
TMPDIR="$(mktemp -d)"
SERVER_PID=""
SERVER_LOG="${TMPDIR}/server.log"

cleanup() {
    if [[ -n "${SERVER_PID:-}" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
        echo -e "\n${YELLOW}Stopping dev server (pid=$SERVER_PID)...${NC}"
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    rm -rf "$TMPDIR"
}
trap cleanup EXIT

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------
pass() {
    echo -e "  ${GREEN}PASS${NC} $1"
    PASS=$((PASS + 1))
}

fail() {
    echo -e "  ${RED}FAIL${NC} $1"
    FAIL=$((FAIL + 1))
}

assert_status() {
    local desc="$1" expected="$2" actual="$3"
    if [[ "$actual" -eq "$expected" ]]; then
        pass "$desc (HTTP $actual)"
    else
        fail "$desc — expected HTTP $expected, got HTTP $actual"
    fi
}

assert_json_field() {
    local desc="$1" json="$2" field="$3" expected="$4"
    local actual
    actual="$(echo "$json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('$field',''))" 2>/dev/null || true)"
    if [[ "$actual" == "$expected" ]]; then
        pass "$desc"
    else
        fail "$desc — expected '$expected', got '$actual'"
    fi
}

assert_content_type() {
    local desc="$1" url="$2" expected="$3"
    local actual
    actual="$(curl -s -o /dev/null -w '%{content_type}' "$url")"
    if [[ "$actual" == "$expected" ]]; then
        pass "$desc"
    else
        fail "$desc — expected '$expected', got '$actual'"
    fi
}

# -------------------------------------------------------------------
# Load environment
# -------------------------------------------------------------------
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ -f .env ]]; then
    echo -e "${YELLOW}Loading .env${NC}"
    set -a; source .env; set +a
else
    echo -e "${YELLOW}Loading .env.default${NC}"
    set -a; source .env.default; set +a
fi

# -------------------------------------------------------------------
# Prepare test files
# -------------------------------------------------------------------
echo -e "\n${YELLOW}Preparing test files...${NC}"

cat > "$TMPDIR/doc.kml" <<'KML'
<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Smoke Test Drawing</name>
    <Placemark>
      <name>Test Point</name>
      <Point><coordinates>7.0,46.0,0</coordinates></Point>
    </Placemark>
  </Document>
</kml>
KML
(cd "$TMPDIR" && zip -q valid.kmz doc.kml)

echo "not a valid ZIP file" > "$TMPDIR/invalid.kmz"
dd if=/dev/zero of="$TMPDIR/oversized.kmz" bs=1M count=6 2>/dev/null

# -------------------------------------------------------------------
# Start dev server
# -------------------------------------------------------------------
echo -e "\n${YELLOW}Starting dev server on ${BASE_URL}...${NC}"
if $VERBOSE; then
    uv run fastapi dev --port "${BASE_URL##*:}" &
else
    uv run fastapi dev --port "${BASE_URL##*:}" &>"$SERVER_LOG" &
fi
SERVER_PID=$!

echo -n "  Waiting for server"
for _ in $(seq 1 30); do
    if curl -s -o /dev/null "$BASE_URL/checker" 2>/dev/null; then
        echo " ready"
        break
    fi
    sleep 0.5
    echo -n "."
done

if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo -e "\n  ${RED}Server failed to start${NC}"
    if ! $VERBOSE; then
        echo -e "  ${YELLOW}Last server log lines:${NC}"
        tail -n 20 "$SERVER_LOG" 2>/dev/null || true
    fi
    exit 1
fi

# ===================================================================
# TESTS
# ===================================================================

# --- Health endpoints ---
echo -e "\n${BOLD}=== Health Endpoints ===${NC}"

HTTP=$(curl -s -o /dev/null -w '%{http_code}' "$BASE_URL/checker")
assert_status "GET /checker" 200 "$HTTP"

RESP=$(curl -s "$BASE_URL/checker/ready")
HTTP=$(curl -s -o /dev/null -w '%{http_code}' "$BASE_URL/checker/ready")
assert_status "GET /checker/ready" 200 "$HTTP"
assert_json_field "  body.success=true" "$RESP" "success" "True"
assert_json_field "  body.message=OK" "$RESP" "message" "OK"

# --- POST /api/wps/v1/drawings ---
echo -e "\n${BOLD}=== POST /api/wps/v1/drawings ===${NC}"

sha256_of() {
    sha256sum -b "$1" | awk '{print $1}'
}

RESP=$(curl -s -w '\n%{http_code}' \
    -F "file=@$TMPDIR/valid.kmz" \
    -F "sha256=$(sha256_of "$TMPDIR/valid.kmz")" \
    "$BASE_URL/api/wps/v1/drawings")
BODY="$(echo "$RESP" | sed '$d')"
HTTP="$(echo "$RESP" | tail -n 1)"
assert_status "Upload valid KMZ" 201 "$HTTP"

DRAWING_ID="$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null || true)"
ADMIN_ID="$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['admin_id'])" 2>/dev/null || true)"
S3_URL="$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['s3_url'])" 2>/dev/null || true)"

if [[ -n "$DRAWING_ID" ]]; then
    pass "  Response contains id: $DRAWING_ID"
else
    fail "  Response missing id field"
fi

if [[ -n "$ADMIN_ID" ]]; then
    pass "  Response contains admin_id"
else
    fail "  Response missing admin_id"
fi

if [[ "$S3_URL" == "$BASE_URL/api/wps/v1/drawings/$DRAWING_ID" ]]; then
    pass "  s3_url points to GET endpoint on same domain"
else
    fail "  s3_url format unexpected: $S3_URL"
fi

HTTP=$(curl -s -o /dev/null -w '%{http_code}' \
    -F "file=@$TMPDIR/invalid.kmz" \
    -F "sha256=$(sha256_of "$TMPDIR/invalid.kmz")" \
    "$BASE_URL/api/wps/v1/drawings")
assert_status "Reject invalid file (not ZIP)" 400 "$HTTP"

HTTP=$(curl -s -o /dev/null -w '%{http_code}' \
    -F "file=@$TMPDIR/oversized.kmz" \
    -F "sha256=$(sha256_of "$TMPDIR/oversized.kmz")" \
    "$BASE_URL/api/wps/v1/drawings")
assert_status "Reject oversized file (>5 MB)" 413 "$HTTP"

# This is caught by the MaxBodySizeMiddleware, NOT by validate_kmz().
# We send a raw HTTP request with Content-Length: 11 MB but a tiny body.
# The middleware rejects before the body is read; validate_kmz() never runs.
HTTP=$(python3 -c "
import socket
host = 'localhost'
port = ${BASE_URL##*:}
req = (
    'POST /api/wps/v1/drawings HTTP/1.1\r\n'
    f'Host: {host}:{port}\r\n'
    'Content-Type: application/vnd.google-earth.kmz\r\n'
    'Content-Length: 11000000\r\n'
    '\r\n'
    'x'
).encode()
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(5)
s.connect((host, port))
s.sendall(req)
resp = s.recv(4096)
s.close()
print(resp.split(b'\r\n')[0].split()[1].decode())
" 2>/dev/null || echo "000")
assert_status "Reject body >10 MB (middleware, not validation)" 413 "$HTTP"

# --- GET /api/wps/v1/drawings/{id} ---
echo -e "\n${BOLD}=== GET /api/wps/v1/drawings/{id} ===${NC}"

HTTP=$(curl -s -w '%{http_code}' -o "$TMPDIR/downloaded.kmz" \
    "$BASE_URL/api/wps/v1/drawings/$DRAWING_ID")
assert_status "Download existing drawing" 200 "${HTTP##*$'\n'}"

assert_content_type \
    "  Content-Type header" \
    "$BASE_URL/api/wps/v1/drawings/$DRAWING_ID" \
    "application/vnd.google-earth.kmz"

if cmp -s "$TMPDIR/valid.kmz" "$TMPDIR/downloaded.kmz"; then
    pass "  Downloaded content matches original"
else
    fail "  Downloaded content does NOT match original"
fi

HTTP=$(curl -s -o /dev/null -w '%{http_code}' \
    "$BASE_URL/api/wps/v1/drawings/00000000-0000-0000-0000-000000000000")
assert_status "Non-existent drawing → 404" 404 "$HTTP"

# --- PUT /api/wps/v1/drawings/{id} ---
echo -e "\n${BOLD}=== PUT /api/wps/v1/drawings/{id} ===${NC}"

HTTP=$(curl -s -o /dev/null -w '%{http_code}' -X PUT \
    "$BASE_URL/api/wps/v1/drawings/$DRAWING_ID")
assert_status "PUT returns 501 Not Implemented" 501 "$HTTP"

# --- OpenAPI spec ---
echo -e "\n${BOLD}=== OpenAPI Spec ===${NC}"

HTTP=$(curl -s -o /dev/null -w '%{http_code}' "$BASE_URL/openapi.json")
assert_status "GET /openapi.json" 200 "$HTTP"

RESP=$(curl -s "$BASE_URL/openapi.json")
if echo "$RESP" | python3 -c "import sys,json; paths=json.load(sys.stdin)['paths']; assert '/api/wps/v1/drawings' in paths" 2>/dev/null; then
    pass "  /api/wps/v1/drawings in public spec"
else
    fail "  /api/wps/v1/drawings NOT in public spec"
fi

if echo "$RESP" | python3 -c "import sys,json; paths=json.load(sys.stdin)['paths']; assert '/checker' not in paths" 2>/dev/null; then
    pass "  /checker NOT in public spec (Internal tag)"
else
    fail "  /checker incorrectly exposed in public spec"
fi

# ===================================================================
# Summary
# ===================================================================
echo -e "\n${BOLD}========================================${NC}"
echo -e "${BOLD}  Results: ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC}"
echo -e "${BOLD}========================================${NC}"

if [[ $FAIL -gt 0 ]]; then
    exit 1
fi
