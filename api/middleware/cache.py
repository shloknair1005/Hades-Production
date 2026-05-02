"""
In-process TTL cache using cachetools.TTLCache.
No Redis dependency — suitable for single-process deployments.

Three separate caches, each with its own TTL:
  agent_configs_cache  — 5 min  — keyed by (org_id, agent_name)
  analytics_cache      — 10 min — keyed by (org_id, endpoint_name)
  shared_runs_cache    — 2 min  — keyed by share_token

Invalidation is explicit: call the appropriate invalidate_* function
whenever the underlying data changes (activate config, revoke share, etc.)
"""
from cachetools import TTLCache
from threading import Lock

# ── Cache instances ───────────────────────────────────────────────────────────

_agent_cfg_cache: TTLCache = TTLCache(maxsize=512, ttl=300)    # 5 min
_analytics_cache: TTLCache = TTLCache(maxsize=256, ttl=600)    # 10 min
_shared_run_cache: TTLCache = TTLCache(maxsize=1024, ttl=120)  # 2 min

# TTLCache is not thread-safe on its own — use a lock per cache
_agent_cfg_lock = Lock()
_analytics_lock = Lock()
_shared_run_lock = Lock()


# ── Agent config cache ────────────────────────────────────────────────────────

def get_agent_config(org_id: str | None, agent_name: str):
    key = (org_id, agent_name)
    with _agent_cfg_lock:
        return _agent_cfg_cache.get(key)


def set_agent_config(org_id: str | None, agent_name: str, value) -> None:
    key = (org_id, agent_name)
    with _agent_cfg_lock:
        _agent_cfg_cache[key] = value


def invalidate_agent_config(org_id: str | None, agent_name: str) -> None:
    key = (org_id, agent_name)
    with _agent_cfg_lock:
        _agent_cfg_cache.pop(key, None)


def invalidate_all_agent_configs_for_org(org_id: str | None) -> None:
    """Called when any config for an org is activated — clears all agent entries for that org."""
    with _agent_cfg_lock:
        stale = [k for k in _agent_cfg_cache if k[0] == org_id]
        for k in stale:
            del _agent_cfg_cache[k]


# ── Analytics cache ───────────────────────────────────────────────────────────

def get_analytics(org_id: str | None, endpoint: str):
    key = (org_id, endpoint)
    with _analytics_lock:
        return _analytics_cache.get(key)


def set_analytics(org_id: str | None, endpoint: str, value) -> None:
    key = (org_id, endpoint)
    with _analytics_lock:
        _analytics_cache[key] = value


def invalidate_analytics(org_id: str | None) -> None:
    """Called by scheduler after trust score aggregation completes."""
    with _analytics_lock:
        stale = [k for k in _analytics_cache if k[0] == org_id]
        for k in stale:
            del _analytics_cache[k]


def invalidate_all_analytics() -> None:
    """Called after platform-wide aggregation."""
    with _analytics_lock:
        _analytics_cache.clear()


# ── Shared run cache ──────────────────────────────────────────────────────────

def get_shared_run(share_token: str):
    with _shared_run_lock:
        return _shared_run_cache.get(share_token)


def set_shared_run(share_token: str, value) -> None:
    with _shared_run_lock:
        _shared_run_cache[share_token] = value


def invalidate_shared_run(share_token: str) -> None:
    """Called immediately when a share is revoked."""
    with _shared_run_lock:
        _shared_run_cache.pop(share_token, None)
