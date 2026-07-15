"""Smoke-test S3Service against local moto server.

Usage:
    make start-moto          # ensure moto is running
    uv run python3 scripts/test_s3_local.py
"""
import asyncio
import hashlib
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Fake credentials for local moto (same as conftest.py aws_credentials fixture)
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "eu-central-1")
os.environ.setdefault("AWS_REGION", "eu-central-1")

from app.exceptions import DrawingNotFoundError
from app.services.s3 import S3Service

ENDPOINT = os.environ.get("AWS_S3_ENDPOINT_URL", "http://localhost:5000")
BUCKET = os.environ.get("AWS_S3_BUCKET_NAME", "service-drawings-local")
TEST_FILE = "France.kmz"


async def main() -> None:
    svc = S3Service(bucket=BUCKET, endpoint_url=ENDPOINT)

    # Read test KMZ
    with open(TEST_FILE, "rb") as f:
        data = f.read()

    sha256 = hashlib.sha256(data).hexdigest()
    drawing_id = str(uuid.uuid4())
    key = f"drawings/{drawing_id}.kmz"

    print(f"Bucket:   {BUCKET}")
    print(f"Endpoint: {ENDPOINT}")
    print(f"File:     {TEST_FILE} ({len(data):,} bytes)")
    print(f"SHA-256:  {sha256}")
    print(f"Key:      {key}")
    print()

    # Upload
    print("1. upload_kml ...", end=" ", flush=True)
    await svc.upload_kml(key, data, "application/vnd.google-earth.kmz", sha256)
    print("OK")

    # Head (metadata)
    print("2. head_kml ...", end=" ", flush=True)
    meta = await svc.head_kml(key)
    assert meta["sha256"] == sha256, f"Metadata mismatch: {meta}"
    print(f"OK (metadata={meta})")

    # Get (streaming)
    print("3. get_kml ...", end=" ", flush=True)
    chunks = [chunk async for chunk in svc.get_kml(key)]
    downloaded = b"".join(chunks)
    assert downloaded == data, f"Content mismatch: {len(downloaded)} != {len(data)}"
    print(f"OK ({len(downloaded):,} bytes)")

    # Get non-existent
    print("4. get_kml (missing) ...", end=" ", flush=True)
    try:
        async for _ in svc.get_kml("drawings/nonexistent.kmz"):
            pass
        print("FAIL (expected DrawingNotFoundError)")
    except DrawingNotFoundError:
        print("OK (DrawingNotFoundError)")

    # Head non-existent
    print("5. head_kml (missing) ...", end=" ", flush=True)
    try:
        await svc.head_kml("drawings/nonexistent.kmz")
        print("FAIL (expected DrawingNotFoundError)")
    except DrawingNotFoundError:
        print("OK (DrawingNotFoundError)")

    print()
    print("All checks passed ✓")


if __name__ == "__main__":
    asyncio.run(main())
