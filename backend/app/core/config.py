"""Application settings, loaded from environment variables (and `.env` locally).

Purpose: Give every part of the API one validated, typed view of its configuration (database,
Supabase Auth, CORS, optimizer and AI limits) and refuse to start with an unsafe setup.
How it fits: `get_settings()` is read by `app.main` and stored on `app.state.settings`;
`app.core.db`, `app.core.security` and the job runners read their limits from it; layer: core.
Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

Environment = Literal["development", "test", "staging", "production"]
DatabaseSSL = Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"]


class Settings(BaseSettings):
    """Typed, validated API configuration read from the environment (and `.env` locally)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Environment = "development"
    log_level: str = "INFO"
    port: int = 8000

    # Postgres (Supabase Supavisor pooler). Session pooler (5432) recommended;
    # transaction pooler (6543) also works because statement caching is disabled.
    database_url: SecretStr | None = None
    database_ssl: DatabaseSSL = "prefer"
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=5, ge=0)
    db_pool_timeout_s: float = Field(default=10.0, gt=0)
    db_pool_recycle_s: int = Field(default=1800, gt=0)
    db_statement_timeout_ms: int = Field(default=15_000, ge=0)

    # Supabase Auth
    supabase_url: str | None = None
    supabase_jwt_secret: SecretStr | None = None
    jwt_leeway_s: int = Field(default=10, ge=0, le=120)
    jwks_cache_lifespan_s: int = Field(default=600, gt=0)

    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    enable_docs: bool | None = None

    # Optimizer (OR-Tools CP-SAT, runs in-process off the event loop).
    optimizer_time_limit_s: float = Field(default=20.0, gt=0, le=300)
    optimizer_workers: int = Field(default=4, ge=1, le=32)
    # Queued/running runs older than this are treated as orphaned (server restarted).
    optimizer_stale_after_s: int = Field(default=180, ge=30)

    # AI intake (Claude). Without ANTHROPIC_API_KEY, intake uses the rule-based extractor.
    anthropic_api_key: SecretStr | None = None
    intake_model: str = "claude-opus-5-5"
    # Queued/running runs older than this are treated as orphaned (server restarted).
    intake_stale_after_s: int = Field(default=180, ge=30)
    # WBS generate/validate and the assistant. Without a key, WBS generation falls back
    # to a template, validation runs only the deterministic checks, and the assistant is 503.
    ai_model: str = "claude-sonnet-5-5"
    # Queued/running AI jobs older than this are treated as orphaned (server restarted).
    ai_job_stale_after_s: int = Field(default=600, ge=60)
    # Per-request limit for the synchronous assistant call.
    assistant_timeout_s: float = Field(default=60.0, gt=0, le=300)

    # Injected by Railway at build/deploy time.
    railway_git_commit_sha: str | None = None

    # ------------------------------------------------------------------ validators
    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Split a comma-separated `CORS_ORIGINS` string into origins without trailing slashes."""
        if isinstance(value, str):
            return [origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("cors_origins")
    @classmethod
    def _no_wildcard(cls, value: list[str]) -> list[str]:
        """Reject a `*` origin: with credentials allowed every origin must be explicit."""
        if "*" in value:
            raise ValueError("CORS_ORIGINS must list explicit origins; '*' is not allowed")
        return value

    @field_validator("anthropic_api_key", mode="before")
    @classmethod
    def _blank_key_is_unset(cls, value: object) -> object:
        """Treat an empty `ANTHROPIC_API_KEY` as no key at all."""
        # `ANTHROPIC_API_KEY=` (as in .env.example) means "no key": every AI feature must
        # take its fallback rather than call Claude with an empty key.
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("supabase_url")
    @classmethod
    def _strip_slash(cls, value: str | None) -> str | None:
        """Drop a trailing slash from the Supabase URL so derived URLs are well-formed."""
        return value.rstrip("/") if value else value

    @field_validator("log_level")
    @classmethod
    def _upper_level(cls, value: str) -> str:
        """Normalise the log level name to upper case (`info` -> `INFO`)."""
        return value.upper()

    @model_validator(mode="after")
    def _stale_after_exceeds_solve(self) -> Settings:
        """Require the stale-run timeout to exceed twice the solver time limit.

        Otherwise a slow but healthy run could be marked orphaned while it is still solving.
        """
        # A run takes at most ~2x the time limit (solve + infeasibility explanation).
        if self.optimizer_stale_after_s <= 2 * self.optimizer_time_limit_s:
            raise ValueError("OPTIMIZER_STALE_AFTER_S must exceed 2 x OPTIMIZER_TIME_LIMIT_S")
        return self

    @model_validator(mode="after")
    def _require_in_production(self) -> Settings:
        """Fail start-up in production when DATABASE_URL or SUPABASE_URL is missing."""
        if self.environment == "production":
            missing = [
                name
                for name, setting_value in (
                    ("DATABASE_URL", self.database_url),
                    ("SUPABASE_URL", self.supabase_url),
                )
                if not setting_value
            ]
            if missing:
                raise ValueError(f"Missing required settings in production: {', '.join(missing)}")
        return self

    # ------------------------------------------------------------------ derived
    @property
    def version(self) -> str:
        """Return the deployed git commit (set by Railway) or `dev` locally."""
        return self.railway_git_commit_sha or "dev"

    @property
    def is_production(self) -> bool:
        """Tell whether this process runs in the production environment."""
        return self.environment == "production"

    @property
    def docs_enabled(self) -> bool:
        """Tell whether Swagger docs are served: the explicit setting, else not in production."""
        if self.enable_docs is not None:
            return self.enable_docs
        return not self.is_production

    @property
    def jwt_issuer(self) -> str | None:
        """Return the issuer Supabase puts in access tokens, or None without a Supabase URL."""
        return f"{self.supabase_url}/auth/v1" if self.supabase_url else None

    @property
    def jwks_url(self) -> str | None:
        """Return the URL of the project's JSON Web Key Set, or None without a Supabase URL."""
        return f"{self.supabase_url}/auth/v1/.well-known/jwks.json" if self.supabase_url else None

    def sqlalchemy_database_url(self) -> tuple[URL, DatabaseSSL] | None:
        """Return the asyncpg URL plus the effective SSL mode.

        Accepts the plain `postgresql://` URL Supabase shows in the dashboard.
        `sslmode` is stripped from the query (asyncpg doesn't understand it)
        and passed through as the `ssl` connect arg instead.
        """
        if self.database_url is None:
            return None
        url = make_url(self.database_url.get_secret_value())
        if url.drivername in (
            "postgres",
            "postgresql",
            "postgresql+psycopg",
            "postgresql+psycopg2",
        ):
            url = url.set(drivername="postgresql+asyncpg")
        ssl: DatabaseSSL = self.database_ssl
        query_ssl = url.query.get("sslmode") or url.query.get("ssl")
        if isinstance(query_ssl, str) and query_ssl in (
            "disable",
            "allow",
            "prefer",
            "require",
            "verify-ca",
            "verify-full",
        ):
            ssl = query_ssl  # type: ignore[assignment]
        url = url.difference_update_query(["sslmode", "ssl"])
        return url, ssl


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, read from the environment once and cached."""
    return Settings()
