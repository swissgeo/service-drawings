from collections.abc import Generator
from contextlib import suppress

import boto3
from moto.server import ThreadedMotoServer
from mypy_boto3_s3 import S3Client

from fastapi import FastAPI
from fastapi.testclient import TestClient

import pytest

from app.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def aws_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock AWS credentials for testing."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.setenv("AWS_REGION", "eu-central-1")


@pytest.fixture
def moto_server() -> Generator[str]:
    """Fixture to run a mocked AWS server for testing."""
    # Note: pass `port=0` to get a random free port.
    server = ThreadedMotoServer(port=0)
    server.start()
    host, port = server.get_host_and_port()
    yield f"http://{host}:{port}"
    server.stop()


@pytest.fixture
def settings(moto_server: str) -> Settings:
    """Fixture to provide application settings for testing, overriding the AWS endpoint URL."""
    return Settings(
        # Pydantic will automatically load any .env or .env.default file, so for testing to avoid
        # any different test result between CI and local environment (in which .env file can differ)
        # we make sure pydantic doesn't load the environment file with `_env_file=None`
        _env_file=None,  # ty:ignore[unknown-argument]
        cors_origins=["http://test.com", "https://hello.com"],
        cors_origin_regex=r"http://localhost:\d+",
        aws_endpoint_url=moto_server,
        aws_s3_bucket_name="test-bucket",
        aws_s3_endpoint_url=moto_server,
        aws_cloudfront_domain="test.cloudfront.net",
        root_path="",
        otel_sdk_disabled=True,
        publish_openapi_spec=True,
    )


@pytest.fixture
def s3_client(settings: Settings) -> S3Client:
    """Fixture to provide an S3 client configured to connect to the mocked AWS server."""
    return boto3.client(
        "s3", endpoint_url=settings.aws_s3_endpoint_url, region_name="eu-central-1"
    )


@pytest.fixture(autouse=True)
def setup_s3(settings: Settings, s3_client: S3Client) -> None:
    """Fixture to set up the S3 bucket before each test."""
    with suppress(s3_client.exceptions.BucketAlreadyOwnedByYou):
        s3_client.create_bucket(
            Bucket=settings.aws_s3_bucket_name,
            CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
        )


@pytest.fixture(autouse=True)
def mock_settings(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    """Fixture to mock the application settings for testing.

    This fixture is required for the initial application startup settings.
    Setttings dependency injection mocking is done in the `client` fixture with the settings
    fixture.
    """
    monkeypatch.setattr("app.settings.get_settings", lambda: settings)


@pytest.fixture
def app() -> FastAPI:
    """Fixture to provide the FastAPI application instance for testing.

    This is important to use it in order to have all the mocking, especially the settings mocking,
    in place before the application is initialized.
    """
    # Do the import here to ensure that the application is initialized after the settings
    # are mocked.
    from app.main import app as fastapi_app  # noqa: PLC0415

    return fastapi_app


@pytest.fixture
def client(app: FastAPI, settings: Settings) -> Generator[TestClient]:
    """Fixture to provide a TestClient for the FastAPI application.

    Settings dependency injection is mocked via dependency_overrides.
    """

    def get_settings_override() -> Settings:
        return settings

    with TestClient(app) as client:
        app.dependency_overrides[get_settings] = get_settings_override
        yield client
