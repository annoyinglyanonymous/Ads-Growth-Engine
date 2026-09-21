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

    #: postgres. The IMPORT credential, and the third url here for a reason
    #: worth stating: meta_ads/ writes public.meta_*, and neither role above
    #: can. ads_owner holds SELECT in public and nothing more -- deliberately,
    #: per 006 -- so wiring the importer through it would mean widening that
    #: grant, which is the one thing that would let a read verb write.
    #:
    #: Three urls is not three credentials too many. It is one per job:
    #:   ads_database_url_ro  read ads.*          intel/, ui.py, ask.py
    #:   ads_database_url     write ads.*         ads_migrate.py, intel/record.py
    #:   database_url         write public.meta_* meta_ads/ only
    database_url: str = ""

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

    #: The Meta System User token, ads_read only. A System User token and not a
    #: personal one: a personal token expires at 60 days and the failure is
    #: SILENT -- the pull simply stops working and nothing says why.
    #:
    #: TWO THINGS NEED IT, and this comment used to say one.
    #:
    #:     python -m meta_ads --pull    the importer. Without a token there is
    #:                                  no data in public.meta_* at all.
    #:     python -m intel live         what is running right now.
    #:
    #: The second was the only caller while the importer lived in
    #: growth-engine. It moved here in 404a045 and this comment did not, so it
    #: read "used by exactly one verb" and called the token OPTIONAL -- which
    #: was true of the install and is not true of the data. Nothing imports
    #: without it.
    #:
    #: Still optional in the narrow sense that the app starts and thirteen read
    #: verbs work, because they read Postgres. `intel status` reports the
    #: absence rather than refusing to run. But an install without this token
    #: is an install that will never have a number in it.
    #:
    #: ONE COPY, since the importer moved here. This used to be duplicated in
    #: growth-engine's .env because both halves called Meta; now only this one
    #: does, and there is a single place to rotate it.
    meta_access_token: str | None = None

    ads_host: str = "127.0.0.1"
    ads_port: int = 8001

    #: Meta restates attributed conversions for several days after the fact;
    #: meta_ads/pull.py re-reads the last 3 days for exactly this reason. Any
    #: window reaching inside this horizon is not settled, and every read verb
    #: says so rather than letting a partial day read as a decline.
    restatement_days: int = 3

    # ---- the Meta import (meta_ads/) -------------------------------------
    # Moved here from growth-engine along with the importer. The boundary is
    # no longer "this repo reads, that one writes" -- it is "everything Meta
    # is here, copy production is there".

    #: Which Graph API version every request is pinned to.
    #:
    #: Pinned rather than left unversioned because the symptom of a sunset
    #: version is a field quietly missing from a response, not an error. Every
    #: meta_pulls row stores the version it ran under, so when a column goes
    #: empty across the board the first question is answerable.
    #:
    #: VERIFIED CURRENT ON 2026-09-15: v26.0 shipped 2026-07-29. Meta ships
    #: roughly every five months and supports a version for about two years,
    #: so this is a value to re-check rather than trust indefinitely.
    meta_api_version: str = "v26.0"

    #: Split from the version so a test, or a proxy, can point the client
    #: somewhere else without rewriting paths.
    meta_graph_base: str = "https://graph.facebook.com"

    #: Seconds to wait on one Graph request. Generous, and for the opposite
    #: reason to a web timeout: nobody is watching a pull. An insights page
    #: covering thirty days of a large account genuinely takes tens of
    #: seconds, and a timeout firing mid-import costs rate-limit budget.
    meta_timeout: float = 30.0

    #: Where log.get() puts its rotating handler. The importer logs; the read
    #: verbs do not.
    log_file: str = "logs/engine.log"

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
