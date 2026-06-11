import json
from collections.abc import Generator
from typing import Protocol
from unittest.mock import AsyncMock

import boto3
from moto.server import ThreadedMotoServer
from mypy_boto3_dynamodb import DynamoDBClient
from mypy_boto3_dynamodb.service_resource import Table

from fastapi import FastAPI
from fastapi.testclient import TestClient

import pytest

from app.settings import Settings, get_settings


class MockPutItemFactory(Protocol):
    def __call__(
        self,
        return_value: dict | None = None,
        client_error: dict | None = None,
        mock_client: AsyncMock | None = None,
    ) -> AsyncMock: ...


class MockGetItemFactory(Protocol):
    def __call__(
        self,
        return_value: dict,
        mock_client: AsyncMock | None = None,
    ) -> AsyncMock: ...


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
    """Fixture to provide application settings for testing, overriding the DynamoDB endpoint URL."""
    return Settings(
        # Pydantic will automatically load any .env or .env.default file, so for testing to avoid
        # any different test result between CI and local environment (in which .env file can differ)
        # we make sure pydantic doesn't load the environment file with `_env_file=None`
        _env_file=None,  # ty:ignore[unknown-argument]
        cors_origins=["http://test.com", "https://hello.com"],
        cors_origin_regex=r"http://localhost:\d+",
        aws_endpoint_url=moto_server,
        aws_dynamodb_table_name="test-table",
        aws_region="eu-central-1",
        root_path="",
        otel_sdk_disabled=True,
    )


@pytest.fixture
def db_client(settings: Settings) -> DynamoDBClient:
    """Fixture to provide a DynamoDB client configured to connect to the mocked AWS server."""
    return boto3.client(
        "dynamodb", endpoint_url=settings.aws_endpoint_url, region_name=settings.aws_region
    )


@pytest.fixture
def db_table(settings: Settings) -> Table:
    """
    Fixture to provide a DynamoDB Table resource connected to the mocked AWS server for testing.
    """
    dynamodb = boto3.resource(
        "dynamodb", endpoint_url=settings.aws_endpoint_url, region_name=settings.aws_region
    )
    return dynamodb.Table(settings.aws_dynamodb_table_name)


@pytest.fixture(autouse=True)
def setup_db(settings: Settings, db_client: DynamoDBClient) -> Generator[None]:
    """Fixture to set up the DynamoDB table before each test and tear it down afterward."""
    with open("dynamodb-local-config.json", encoding="utf-8") as fd:
        table_config = fd.read()
    table_config = table_config.replace(
        "${AWS_DYNAMODB_TABLE_NAME}", settings.aws_dynamodb_table_name
    )
    table_config = json.loads(table_config)

    db_client.create_table(**table_config)

    yield

    db_client.delete_table(TableName=settings.aws_dynamodb_table_name)


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
    """Fixture to provide a TestClient for the FastAPI application with settings dependency
    injection mocked.
    """

    def get_settings_override() -> Settings:
        return settings

    with TestClient(app) as client:
        app.dependency_overrides[get_settings] = get_settings_override
        yield client
