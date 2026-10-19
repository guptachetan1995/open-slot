import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")
LOOPBACK_BINDS = ("127.0.0.1", "localhost", "::1", "[::1]")
DEFAULT_PORT = 8760
MAX_CALLS_DEFAULT = 16
MAX_CALLS_RAISED = 40

_HOST = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")
_KEY = re.compile(r"^[\x21-\x7e]{1,512}$")


class ConfigError(Exception):
    def __init__(self, setting, problem):
        super().__init__(f"{setting}: {problem}")
        self.setting = setting


@dataclass(frozen=True)
class Config:
    key: str | None = field(repr=False)
    provider: str
    max_calls: int
    agent_calls_per_day: int
    daily_live_calls: int
    cache_ttl_s: int
    workspace_ttl_s: int
    bind: str
    port: int
    extra_hosts: tuple
    trust_proxy: bool

    @property
    def key_status(self):
        return "set" if self.key else "missing"

    @property
    def allowed_hosts(self):
        return LOOPBACK_HOSTS + self.extra_hosts


def _read_env_file(path, required):
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if required:
            raise ConfigError("OPEN_SLOT_ENV_FILE", "the file does not exist") from None
        return {}
    except (OSError, UnicodeDecodeError):
        raise ConfigError("OPEN_SLOT_ENV_FILE", "the file cannot be read") from None
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name] = value
    return values


def _int(sources, name, default, low, high):
    raw = _get(sources, name)
    if raw is None:
        return default
    if not re.fullmatch(r"[0-9]{1,9}", raw) or not low <= int(raw) <= high:
        raise ConfigError(name, f"must be a whole number from {low} to {high}")
    return int(raw)


def _get(sources, name):
    for source in sources:
        value = source.get(name, "").strip()
        if value:
            return value
    return None


def _hosts(raw, setting):
    hosts = []
    for item in (raw or "").split(","):
        item = item.strip().lower()
        if not item:
            continue
        if not _HOST.match(item):
            raise ConfigError(setting, "values must be host names, with no scheme, port or path")
        hosts.append(item)
    return hosts


def load(environ=None, cwd=None, serving=False):
    """Layers, per setting: the file named by OPEN_SLOT_ENV_FILE, else ./.env, then the process
    environment. An empty value counts as unset, so the environment fills what a file leaves empty."""
    environ = os.environ if environ is None else environ
    cwd = Path.cwd() if cwd is None else Path(cwd)
    pointed = environ.get("OPEN_SLOT_ENV_FILE", "").strip()
    if pointed:
        files = _read_env_file(Path(pointed), required=True)
    else:
        files = _read_env_file(cwd / ".env", required=False)
    sources = (files, environ)

    key = _get(sources, "QLOO_API_KEY")
    if key is not None and not _KEY.match(key):
        raise ConfigError("QLOO_API_KEY", "must be one run of printable characters with no spaces")

    provider = _get(sources, "OPEN_SLOT_PROVIDER")
    if provider is None:
        provider = "live" if key else "fake"
    elif provider not in ("live", "fake"):
        raise ConfigError("OPEN_SLOT_PROVIDER", "must be live or fake")

    trust = _get(sources, "OPEN_SLOT_TRUST_PROXY")
    if trust not in (None, "0", "1"):
        raise ConfigError("OPEN_SLOT_TRUST_PROXY", "must be 1 or 0")

    port = _int(sources, "PORT", None, 1, 65535)
    if port is None:
        port = _int(sources, "OPEN_SLOT_PORT", DEFAULT_PORT, 1, 65535)

    extra = _hosts(_get(sources, "OPEN_SLOT_ALLOWED_HOSTS"), "OPEN_SLOT_ALLOWED_HOSTS")
    extra += _hosts(_get(sources, "RENDER_EXTERNAL_HOSTNAME"), "RENDER_EXTERNAL_HOSTNAME")

    config = Config(
        key=key,
        provider=provider,
        max_calls=_int(sources, "OPEN_SLOT_MAX_CALLS", MAX_CALLS_DEFAULT, 1, MAX_CALLS_RAISED),
        agent_calls_per_day=_int(sources, "OPEN_SLOT_AGENT_CALLS_PER_DAY", 48, 0, 1_000_000),
        daily_live_calls=_int(sources, "OPEN_SLOT_DAILY_LIVE_CALLS", 600, 0, 1_000_000),
        cache_ttl_s=_int(sources, "OPEN_SLOT_CACHE_TTL_S", 86400, 0, 31_536_000),
        workspace_ttl_s=_int(sources, "OPEN_SLOT_WORKSPACE_TTL_S", 7200, 60, 31_536_000),
        bind=_get(sources, "OPEN_SLOT_BIND") or "127.0.0.1",
        port=port,
        extra_hosts=tuple(dict.fromkeys(extra)),
        trust_proxy=trust == "1",
    )
    if serving and config.bind not in LOOPBACK_BINDS and not config.extra_hosts:
        raise ConfigError(
            "OPEN_SLOT_ALLOWED_HOSTS",
            "a non-loopback OPEN_SLOT_BIND needs OPEN_SLOT_ALLOWED_HOSTS or RENDER_EXTERNAL_HOSTNAME",
        )
    return config


def load_or_exit(serving=False, **kwargs):
    try:
        return load(serving=serving, **kwargs)
    except ConfigError as error:
        sys.stderr.write(f"open-slot: {error}\n")
        raise SystemExit(2) from None
