"""Settings, read from .env.

Mirrors growth-engine/config.py deliberately: same library, same field-naming
convention, same stance that an unconfigured optional feature degrades rather
than crashes. The one place it does NOT take that stance is the read-only
database url -- see `read_url`.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class NotConfigured(RuntimeError):
    """Raised when a required setting is missing, naming the .env key."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    #: ads_owner. Owns the `ads` schema, reads a named list of public.*,
    #: writes nothing in public. Used ONLY by ads_migrate.py and
    #: intel/record.py.
    #:
    #: Empty rather than required, so that importing this module never fails.
    #: `python -m intel --help` should work on a machine with no credentials at
    #: all -- a tool that cannot tell you what it does until you have configured
    #: it is a tool nobody configures. `write_url` is where the refusal lives.
    ads_database_url: str = ""

    #: ads_reader. The read surface: select on ads.* views, execute on ads.*
    #: functions, nothing in public.
    #:
    #: Deliberately has no default and no fallback to ads_database_url. The
    #: tempting version -- fall back, log a warning, carry on -- produces a
    #: process that is connected as the owner while every docstring in the
    #: repo says it is read-only. The warning scrolls past; the belief does
    #: not. So: empty is a refusal, and `read_url` is where that is spelled.
    ads_database_url_ro: str = ""

    #: Supavisor session mode (port 5432), matching growth-engine. Smaller
    #: than growth-engine's 5 because this process is a dashboard and a CLI,
    #: not the write path, and pooler connections are a shared budget across
    #: both repos.
    pool_min_size: int = 1
    pool_max_size: int = 3

    #: Retire connections before Supabase's pooler does it for us. Same
    #: reasoning as growth-engine/db.py: without this the pool lends out a
    #: socket the pooler already closed, and the resulting OperationalError
    #: surfaces on whatever query happened to run first.
    pool_max_idle: float = 240.0

    #: The Meta System User token, ads_read only -- the SAME token
    #: growth-engine imports with. Used by exactly one verb, `intel live`,
    #: which asks what is running right now and writes nothing down.
    #:
    #: OPTIONAL, unlike the read-only database url. Every other verb reads
    #: Postgres, so an install without this is not broken -- it simply cannot
    #: answer "right now", and `intel status` says so. Refusing to start over a
    #: token that thirteen of fourteen verbs do not touch would be theatre.
    #:
    #: Duplicated between the two repos rather than shared: a path that reaches
    #: into a sibling repo's .env is a coupling that breaks silently when
    #: somebody moves a folder. It is a read-only credential, so the cost of
    #: the duplicate is remembering to rotate both -- and `intel status`
    #: reports which side is configured, so a half-done rotation is visible.
    meta_access_token: str | None = None

    ads_host: str = "127.0.0.1"
    ads_port: int = 8001

    #: Meta restates attributed conversions for several days after the fact;
    #: meta_ads/pull.py re-reads the last 3 days for exactly this reason. Any
    #: window reaching inside this horizon is not settled, and every read verb
    #: says so rather than letting a partial day read as a decline.
    restatement_days: int = 3

    @property
    def write_url(self) -> str:
        """The ads_owner connection string, or a refusal naming the key."""
        url = (self.ads_database_url or "").strip()
        if not url:
            raise NotConfigured(
                "ADS_DATABASE_URL is not set. Only `intel record` and "
                "ads_migrate.py need it; every read verb uses "
                "ADS_DATABASE_URL_RO. See .env.example.")
        return url

    @property
    def read_url(self) -> str:
        """The read-only connection string, or a refusal naming the key."""
        url = (self.ads_database_url_ro or "").strip()
        if not url:
            raise NotConfigured(
                "ADS_DATABASE_URL_RO is not set.\n"
                "This repo reads as ads_reader and writes only through "
                "`intel record`. There is no fallback to ADS_DATABASE_URL on "
                "purpose: connecting as the owner while believing you are "
                "read-only is the failure this setting exists to prevent.\n"
                "See .env.example, and migrations/006_ads_roles.sql for how "
                "the role is created."
            )
        return url


settings = Settings()
