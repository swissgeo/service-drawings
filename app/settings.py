"""Application configuration via pydantic-settings.

Defines the Settings model loaded from environment variables and .env files.
Covers AWS S3, CloudFront, CORS, OpenTelemetry, and logging configuration.
Provides a cached singleton get_settings() dependency for FastAPI injection.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic_settings import BaseSettings, SettingsConfigDict

from fastapi import Depends
from pydantic import field_validator


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env files.

    Covers AWS, CORS, OpenTelemetry, and logging configuration.
    """

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.default"),
        env_file_encoding="utf-8",
        enable_decoding=False,
        extra="ignore",
    )

    # NOTE: The following settings are automatically read from environment variables (environment
    # variable uses CONSTANT_CASE) and are parsed using json syntax.

    root_path: str = ""

    # OpenAPI settings
    publish_openapi_spec: bool = False

    # CORS settings
    cors_origins: list[str] = []
    cors_origin_regex: str | None = None
    cors_method: list[str] = ["GET", "POST"]
    cors_headers: list[str] = ["*"]
    cors_max_age: int = 600

    # AWS Settings
    aws_endpoint_url: str | None = None
    aws_s3_bucket_name: str
    aws_s3_endpoint_url: str | None = None
    aws_cloudfront_domain: str
    max_kmz_size_bytes: int = 5_242_880  # 5 MB default

    # OTEL configuration
    otel_sdk_disabled: bool = False
    # Instrumentation
    otel_enable_boto: bool = True
    otel_enable_fastapi: bool = True
    # OTLP exporter
    otel_enable_otlp_exporter: bool = True
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_exporter_otlp_headers: str = ""
    otel_exporter_otlp_insecure: bool = False
    # Metrics
    otel_enable_metrics: bool = False

    # Logging
    # When using the fastapi dev server, we can configure logging inside our application for better
    # user experience. Otherwise logging is configured by uvicorn
    logging_enable_dev_server_logging: bool = False
    logging_config_file: Path | None = None
    # Overwrite the handlers logging level from the one in the logging configuration
    logging_handlers_level: str | None = None

    # In order to support dotenv file with string list directly loaded by pydantic-settings or
    # by docker run --env-file, we MUST set the list as comma separated string in the .env file
    # or environment variable, e.g. CORS_ORIGINS=test.com,localhost and then use a field validator
    # to parse it into a list of strings. Otherwise either pydantic-settings or docker will not
    # parse the list
    # correctly because each system handle quoting differently:
    # - docker would require => CORS_ORIGINS=["*"] (with quotes) to parse it as a list,
    # - pydantic-settings would require => CORS_ORIGINS='["*"]'
    @field_validator(
        "cors_origins",
        "cors_method",
        "cors_headers",
        mode="before",
    )
    @classmethod
    def parse_list(cls, v: str | list[str]) -> list[str]:
        """Parse a comma-separated string or list into a list of strings."""
        if isinstance(v, list):
            return v
        return v.split(",")


# Settings are wrapped in an lru_cache to ensure a single, lazily-initialized instance
# per process. This avoids re-parsing environment variables on every call, improves
# performance, and ensures consistent configuration across the application while still
# working cleanly with FastAPI dependency injection.
@lru_cache
def get_settings() -> Settings:  # pragma: no cover
    """Return the cached singleton Settings instance."""
    return Settings()  # ty: ignore[missing-argument] for production we don't pass parameter we use environment variable


SettingsDep = Annotated[
    Settings,
    Depends(get_settings),
]
