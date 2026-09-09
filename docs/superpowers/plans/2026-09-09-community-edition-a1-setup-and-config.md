# Community Edition Phase A1 — Setup and Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the app configurable and runnable by somebody who is not Koji — no `.env`, no shell, no hardcoded credentials — and make it survive a retired Claude model without a rebuild.

**Architecture:** A JSON config file at `~/.nxt-a1/config.json` becomes the credential source for a distributed install, layered *under* environment variables so Koji's development machine keeps working exactly as it does today. A first-run wizard writes that file. A capabilities endpoint reports which integrations are configured, so the UI can show three states instead of pretending everything works. The scan path translates Anthropic SDK exceptions into typed, translatable error codes.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 (async), Pydantic Settings v2, React 19, TanStack Query v5, Radix UI, Tailwind v4, pytest.

**Spec:** `docs/superpowers/specs/2026-09-08-community-edition-distribution-design.md` — Phase A items W3, W4, W5, W7, W10.

---

## Context for the implementer

You have not seen this codebase. Read these before starting:

- `app/config.py` — a single `Settings(BaseSettings)` object, imported everywhere as `from app.config import settings`. It currently reads `.env` only.
- `app/routers/v2/settings.py` — the existing settings router, mounted at `/api/v2/settings`. It handles `my_companies` and `relationship_types` today.
- `frontend/src/api/client.ts` — every frontend HTTP call goes through `get`/`post`/`patch`/`del` here. Do not call `fetch` directly.
- `frontend/src/i18n.ts` — a `translations` object with one key set per language in `LANG_CYCLE = ['ja', 'en', 'zh-TW']`. **TypeScript enforces key parity across all three**, so adding a string to `ja` without adding it to `en` and `zh-TW` fails the build. That is intentional.
- `tests/conftest.py` — provides the `client_with_test_db` fixture. Read its docstring: it deliberately does **not** use `TestClient` as a context manager, because that would run migrations against Koji's real database.

### Two operational facts that will waste your time if you miss them

1. **Backend changes need a LaunchAgent reload to take effect.** Editing a router and refreshing the browser is not enough:
   ```bash
   launchctl unload ~/Library/LaunchAgents/co.nxta.nxt-a1-backend.plist && launchctl load ~/Library/LaunchAgents/co.nxta.nxt-a1-backend.plist
   ```
2. **Frontend source edits do nothing until you build.** `cd frontend && npm run build`, then hard-refresh the browser with Cmd+Shift+R — the bundle filename is content-hashed and the old one is cached.

### Security constraint that applies to every task

`~/.nxt-a1/config.json` will contain a live Anthropic API key. Therefore:

- The file is written with mode `0600`.
- **No endpoint ever returns the key.** Return `has_api_key: true` or a masked suffix (`"...AbC3"`), never the value. A `GET` that echoes the key would put it in browser history, logs, and any screenshot Koji takes during a setup session.
- Never log the key, not even at debug level.

---

## File structure

| File | Responsibility |
|---|---|
| `app/services/app_config.py` | **Create.** Read/write `~/.nxt-a1/config.json`. Sole owner of that file's format. |
| `app/config.py` | **Modify.** Layer the JSON file under environment variables. |
| `app/services/anthropic_health.py` | **Create.** Validate a key, list models, estimate per-card cost. Sole owner of "talking to Anthropic about anything that isn't a scan". |
| `app/services/capabilities.py` | **Create.** Decide the state of each integration destination. One place, so the UI and the export path cannot disagree. |
| `app/errors.py` | **Create.** `ScanErrorCode` enum + the exception the scan path raises. |
| `app/routers/v2/setup.py` | **Create.** The wizard's endpoints. Kept separate from `settings.py` because it is first-run-only and has different auth implications. |
| `app/routers/v2/settings.py` | **Modify.** Add capabilities + model endpoints. |
| `app/services/claude_parser.py` | **Modify.** Translate SDK exceptions into `ScanError`. |
| `frontend/src/api/setup.ts` | **Create.** Typed client for the new endpoints. |
| `frontend/src/components/SetupWizard.tsx` | **Create.** The first-run flow. |
| `frontend/src/components/DeferredFeatureDialog.tsx` | **Create.** The "contact Koji" dialog, reusable. |
| `frontend/src/App.tsx` | **Modify.** Gate on setup completion; delete the Docker launcher code. |
| `frontend/src/pages/SettingsPage.tsx` | **Modify.** Add the model picker. |
| `frontend/src/components/ExportDestinationSelector.tsx` | **Modify.** Three states. |
| `frontend/src/i18n.ts` | **Modify.** New strings in all three languages. |

---

## Task 1: Config file store

**Files:**
- Create: `app/services/app_config.py`
- Test: `tests/test_app_config.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the ~/.nxt-a1/config.json store."""
import json
import stat

from app.services import app_config


def test_load_returns_empty_dict_when_file_missing(tmp_path):
    assert app_config.load_config(tmp_path / "config.json") == {}


def test_save_then_load_roundtrips(tmp_path):
    path = tmp_path / "config.json"
    app_config.save_config({"anthropic_api_key": "sk-ant-test", "claude_model": "claude-sonnet-5"}, path)
    assert app_config.load_config(path) == {
        "anthropic_api_key": "sk-ant-test",
        "claude_model": "claude-sonnet-5",
    }


def test_save_creates_parent_directory(tmp_path):
    path = tmp_path / "nested" / "deeper" / "config.json"
    app_config.save_config({"claude_model": "claude-sonnet-5"}, path)
    assert path.exists()


def test_save_writes_owner_only_permissions(tmp_path):
    """The file holds a live API key, so group and other must have no access."""
    path = tmp_path / "config.json"
    app_config.save_config({"anthropic_api_key": "sk-ant-secret"}, path)
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_update_merges_rather_than_replacing(tmp_path):
    path = tmp_path / "config.json"
    app_config.save_config({"anthropic_api_key": "sk-ant-test", "claude_model": "old"}, path)
    app_config.update_config({"claude_model": "claude-sonnet-5"}, path)
    loaded = app_config.load_config(path)
    assert loaded["anthropic_api_key"] == "sk-ant-test"
    assert loaded["claude_model"] == "claude-sonnet-5"


def test_load_returns_empty_dict_on_corrupt_json(tmp_path):
    """A truncated or hand-edited file must not crash startup."""
    path = tmp_path / "config.json"
    path.write_text("{not valid json")
    assert app_config.load_config(path) == {}


def test_is_setup_complete_requires_an_api_key(tmp_path):
    path = tmp_path / "config.json"
    assert app_config.is_setup_complete(path) is False
    app_config.save_config({"claude_model": "claude-sonnet-5"}, path)
    assert app_config.is_setup_complete(path) is False
    app_config.save_config({"anthropic_api_key": "sk-ant-test"}, path)
    assert app_config.is_setup_complete(path) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_app_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.app_config'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Read and write ~/.nxt-a1/config.json — the credential store for an install
that has no .env file.

This module is the only place that knows the file's format. It holds a live
Anthropic API key, so the file is always written 0600 and its contents are
never logged.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Sits beside meishi.db and images/ in the same per-user data directory.
DEFAULT_CONFIG_PATH = Path.home() / ".nxt-a1" / "config.json"


def _resolve(path: Optional[Path]) -> Path:
    return path if path is not None else DEFAULT_CONFIG_PATH


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Return the stored config, or {} if it is missing or unreadable.

    A corrupt file must never prevent the app from starting — the user can
    always re-run the setup wizard, but only if the process boots first.
    """
    target = _resolve(path)
    try:
        with open(target, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError):
        # Deliberately does not include the exception text: a partially written
        # file could echo the API key into the log.
        logger.warning("Config file at %s is unreadable; treating as empty.", target)
        return {}
    return data if isinstance(data, dict) else {}


def save_config(data: Dict[str, Any], path: Optional[Path] = None) -> None:
    """Write the config atomically with owner-only permissions."""
    target = _resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    # Write to a temp file in the same directory, chmod it before it holds any
    # secret, then rename — so the key is never briefly world-readable.
    tmp = target.with_suffix(".json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, target)
    os.chmod(target, 0o600)


def update_config(changes: Dict[str, Any], path: Optional[Path] = None) -> Dict[str, Any]:
    """Merge `changes` into the stored config and persist. Returns the result."""
    merged = load_config(path)
    merged.update(changes)
    save_config(merged, path)
    return merged


def is_setup_complete(path: Optional[Path] = None) -> bool:
    """Setup is complete once an API key exists. Everything else has a default."""
    return bool(load_config(path).get("anthropic_api_key"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python3 -m pytest tests/test_app_config.py -v`
Expected: PASS, 7 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/app_config.py tests/test_app_config.py
git commit -m "feat: add ~/.nxt-a1/config.json store for distributed installs"
```

---

## Task 2: Layer the config file under environment variables

**Files:**
- Modify: `app/config.py`
- Test: `tests/test_config_precedence.py`

**Why this shape:** Koji's machine must keep behaving exactly as it does today. Environment variables and `.env` therefore win; the JSON file only fills gaps. Getting this backwards would mean a stale `config.json` silently overriding his real credentials.

- [ ] **Step 1: Write the failing test**

```python
"""Config source precedence: env / .env  >  ~/.nxt-a1/config.json  >  defaults."""
from app.config import Settings
from app.services import app_config


def test_config_file_supplies_value_when_env_is_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    path = tmp_path / "config.json"
    app_config.save_config({"anthropic_api_key": "sk-ant-from-file"}, path)

    s = Settings(_env_file=None, _config_path=path)
    assert s.anthropic_api_key == "sk-ant-from-file"


def test_env_var_wins_over_config_file(tmp_path, monkeypatch):
    """Koji's .env must not be overridden by a stale config.json."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")
    path = tmp_path / "config.json"
    app_config.save_config({"anthropic_api_key": "sk-ant-from-file"}, path)

    s = Settings(_env_file=None, _config_path=path)
    assert s.anthropic_api_key == "sk-ant-from-env"


def test_default_model_is_sonnet_5(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)
    s = Settings(_env_file=None, _config_path=tmp_path / "missing.json")
    assert s.claude_model == "claude-sonnet-5"


def test_config_file_can_override_the_default_model(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)
    path = tmp_path / "config.json"
    app_config.save_config({"claude_model": "claude-haiku-4-5"}, path)

    s = Settings(_env_file=None, _config_path=path)
    assert s.claude_model == "claude-haiku-4-5"


def test_missing_config_file_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = Settings(_env_file=None, _config_path=tmp_path / "nope.json")
    assert s.anthropic_api_key == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_config_precedence.py -v`
Expected: FAIL — `Settings.__init__() got an unexpected keyword argument '_config_path'`

- [ ] **Step 3: Write minimal implementation**

Replace the class definition in `app/config.py`. Two changes: the `claude_model` default becomes `claude-sonnet-5`, and `__init__` merges the JSON file in as a fallback layer.

```python
from pathlib import Path
from typing import Any, Optional

from pydantic_settings import BaseSettings

from app.services.app_config import DEFAULT_CONFIG_PATH, load_config


class Settings(BaseSettings):
    # API auth
    api_key: str = ""

    # Anthropic
    anthropic_api_key: str = ""
    # Current generation, and cheaper per card than claude-sonnet-4-6
    # ($2/$10 vs $3/$15 per MTok). Changeable from Settings via the model picker.
    claude_model: str = "claude-sonnet-5"

    # ... every other existing field stays exactly as it is ...

    def __init__(self, _config_path: Optional[Path] = None, **kwargs: Any):
        """Layer ~/.nxt-a1/config.json *underneath* env vars and .env.

        Pydantic Settings resolves env and .env itself. We pre-seed kwargs from
        the JSON file only for keys the caller did not pass, then let Pydantic
        overwrite anything it finds in the environment — so a distributed
        install is configured by the file, and Koji's .env still wins on his
        own machine.
        """
        path = _config_path if _config_path is not None else DEFAULT_CONFIG_PATH
        file_values = load_config(path)

        merged = {k: v for k, v in file_values.items() if k in self.model_fields}
        merged.update(kwargs)
        super().__init__(**merged)

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "env_ignore_empty": True,
        "extra": "ignore",
    }


settings = Settings()
```

> **Note on the precedence test:** Pydantic Settings gives environment variables
> priority over values passed to `__init__`, which is exactly the ordering we
> want. If `test_env_var_wins_over_config_file` fails, that assumption is wrong
> for this Pydantic version — fix it by popping any key present in `os.environ`
> (case-insensitively) out of `merged` before calling `super().__init__`, rather
> than by changing the test.

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python3 -m pytest tests/test_config_precedence.py -v`
Expected: PASS, 5 passed

Run the whole suite — `settings` is imported everywhere, so a mistake here breaks far-away tests:

Run: `venv/bin/python3 -m pytest tests/ -q`
Expected: PASS, no new failures

- [ ] **Step 5: Verify the running app still starts**

```bash
launchctl unload ~/Library/LaunchAgents/co.nxta.nxt-a1-backend.plist && launchctl load ~/Library/LaunchAgents/co.nxta.nxt-a1-backend.plist
sleep 3 && curl -s localhost:8000/api/v1/health
```
Expected: a healthy JSON response, and `/tmp/nxt-a1-backend.log` shows no traceback.

- [ ] **Step 6: Commit**

```bash
git add app/config.py tests/test_config_precedence.py
git commit -m "feat: layer config.json under env vars, default to claude-sonnet-5"
```

---

## Task 3: Integration capability states

**Files:**
- Create: `app/services/capabilities.py`
- Test: `tests/test_capabilities.py`

**Why a service and not inline logic:** the export selector, the settings page, and the auto-sync-on-confirm path all need the same answer. Three copies of this rule would drift.

- [ ] **Step 1: Write the failing test**

```python
"""Each integration is configured, deferred, or unavailable."""
from app.services.capabilities import DEFERRED, UNAVAILABLE, CONFIGURED, destination_states


def test_google_contacts_is_configured_when_a_refresh_token_exists():
    states = destination_states(google_refresh_token="tok", odoo_url="https://x", odoo_username="u")
    assert states["google_contacts"] == CONFIGURED


def test_google_contacts_is_deferred_when_unconfigured():
    """Deferred, not hidden — the button stays visible and explains itself."""
    states = destination_states(google_refresh_token="", odoo_url="", odoo_username="")
    assert states["google_contacts"] == DEFERRED


def test_odoo_is_unavailable_when_unconfigured():
    """Odoo is Koji's ERP and is meaningless to a friend, so it hides entirely."""
    states = destination_states(google_refresh_token="", odoo_url="", odoo_username="")
    assert states["odoo"] == UNAVAILABLE


def test_odoo_is_configured_when_url_and_username_are_present():
    states = destination_states(google_refresh_token="", odoo_url="https://x", odoo_username="u")
    assert states["odoo"] == CONFIGURED


def test_download_destinations_are_always_configured():
    """CSV and vCard need no credentials, so they never degrade."""
    states = destination_states(google_refresh_token="", odoo_url="", odoo_username="")
    assert states["google_csv"] == CONFIGURED
    assert states["odoo_export"] == CONFIGURED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_capabilities.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.capabilities'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Which integration destinations are usable on this install.

Three states, not two:

  configured   — credentials present; works normally
  deferred     — not configured, but the feature genuinely exists. The UI keeps
                 the button and explains that setup is needed. Google Contacts
                 is the only one: friends will want it, and the button is how
                 they discover it and get in touch.
  unavailable  — not applicable to this install; the UI hides it. Odoo is
                 Koji's own ERP and means nothing on a friend's machine.
"""
from __future__ import annotations

from typing import Dict, Optional

CONFIGURED = "configured"
DEFERRED = "deferred"
UNAVAILABLE = "unavailable"


def destination_states(
    google_refresh_token: Optional[str] = None,
    odoo_url: Optional[str] = None,
    odoo_username: Optional[str] = None,
) -> Dict[str, str]:
    """Map each export destination key to its state.

    Keys match those in frontend/src/components/ExportDestinationSelector.tsx.
    Arguments are passed in rather than read from `settings` so this stays
    trivially testable.
    """
    google_ok = bool(google_refresh_token)
    odoo_ok = bool(odoo_url) and bool(odoo_username)

    return {
        "odoo": CONFIGURED if odoo_ok else UNAVAILABLE,
        "google_contacts": CONFIGURED if google_ok else DEFERRED,
        # Downloads produce a file locally and need no third-party credentials.
        "odoo_export": CONFIGURED,
        "google_csv": CONFIGURED,
    }


def current_states() -> Dict[str, str]:
    """destination_states() bound to the live settings object."""
    from app.config import settings

    return destination_states(
        google_refresh_token=settings.google_refresh_token,
        odoo_url=settings.odoo_url,
        odoo_username=settings.odoo_username,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python3 -m pytest tests/test_capabilities.py -v`
Expected: PASS, 5 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/capabilities.py tests/test_capabilities.py
git commit -m "feat: add three-state capability model for export destinations"
```

---

## Task 4: Typed scan errors

**Files:**
- Create: `app/errors.py`
- Modify: `app/services/claude_parser.py`
- Test: `tests/test_scan_errors.py`

**Why:** a friend whose prepaid credit runs out must see "your Claude credit has run out, top it up at console.anthropic.com" in their own language — not a raw `BadRequestError`. The backend supplies a stable code; the frontend owns the wording, because only the frontend knows the selected language.

- [ ] **Step 1: Write the failing test**

```python
"""Anthropic SDK exceptions map to stable, translatable codes."""
import httpx
import pytest
from anthropic import APIStatusError

from app.errors import ScanError, ScanErrorCode, classify_anthropic_error


def _status_error(status: int, body: dict) -> APIStatusError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status_code=status, json=body, request=request)
    return APIStatusError("boom", response=response, body=body)


def test_401_maps_to_invalid_key():
    err = _status_error(401, {"error": {"type": "authentication_error", "message": "invalid x-api-key"}})
    assert classify_anthropic_error(err) == ScanErrorCode.INVALID_API_KEY


def test_credit_balance_message_maps_to_credit_exhausted():
    err = _status_error(400, {"error": {"type": "invalid_request_error",
                                        "message": "Your credit balance is too low to access the Anthropic API"}})
    assert classify_anthropic_error(err) == ScanErrorCode.CREDIT_EXHAUSTED


def test_404_on_model_maps_to_model_unavailable():
    err = _status_error(404, {"error": {"type": "not_found_error",
                                        "message": "model: claude-retired-1"}})
    assert classify_anthropic_error(err) == ScanErrorCode.MODEL_UNAVAILABLE


def test_429_maps_to_rate_limited():
    err = _status_error(429, {"error": {"type": "rate_limit_error", "message": "slow down"}})
    assert classify_anthropic_error(err) == ScanErrorCode.RATE_LIMITED


def test_unrecognised_error_maps_to_unknown():
    err = _status_error(500, {"error": {"type": "api_error", "message": "kaboom"}})
    assert classify_anthropic_error(err) == ScanErrorCode.UNKNOWN


def test_scan_error_carries_its_code():
    e = ScanError(ScanErrorCode.CREDIT_EXHAUSTED)
    assert e.code == ScanErrorCode.CREDIT_EXHAUSTED
    assert e.code.value == "credit_exhausted"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_scan_errors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.errors'`

- [ ] **Step 3: Write minimal implementation**

Create `app/errors.py`:

```python
"""Stable error codes for the scan path.

The frontend translates these into the user's language, so the strings here are
identifiers and must not change once shipped.
"""
from __future__ import annotations

from enum import Enum


class ScanErrorCode(str, Enum):
    INVALID_API_KEY = "invalid_api_key"
    CREDIT_EXHAUSTED = "credit_exhausted"
    MODEL_UNAVAILABLE = "model_unavailable"
    RATE_LIMITED = "rate_limited"
    UNKNOWN = "unknown"


class ScanError(Exception):
    """Raised by the scan path with a code the UI can translate."""

    def __init__(self, code: ScanErrorCode, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code.value}: {detail}" if detail else code.value)


def classify_anthropic_error(exc: Exception) -> ScanErrorCode:
    """Map an Anthropic SDK exception to a code the user can act on.

    Matches on status code first, then on message text for the cases the status
    code alone cannot distinguish — a low credit balance and an unknown model
    both arrive as 400/404 style errors with a distinguishing message.
    """
    status = getattr(getattr(exc, "response", None), "status_code", None)
    message = str(getattr(exc, "message", "") or exc).lower()

    if status == 401 or "authentication" in message or "invalid x-api-key" in message:
        return ScanErrorCode.INVALID_API_KEY
    if "credit balance" in message or "insufficient" in message:
        return ScanErrorCode.CREDIT_EXHAUSTED
    if status == 404 or "model:" in message or "not_found" in message:
        return ScanErrorCode.MODEL_UNAVAILABLE
    if status == 429 or "rate limit" in message:
        return ScanErrorCode.RATE_LIMITED
    return ScanErrorCode.UNKNOWN
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python3 -m pytest tests/test_scan_errors.py -v`
Expected: PASS, 6 passed

- [ ] **Step 5: Wrap the Anthropic call in claude_parser**

In `app/services/claude_parser.py`, the streaming call currently reads:

```python
    full_text = ""
    async with client.messages.stream(**stream_kwargs) as stream:
```

Wrap it so SDK exceptions become `ScanError`. Add the import at the top of the file:

```python
from anthropic import APIStatusError

from app.errors import ScanError, classify_anthropic_error
```

and replace the `async with` block's opening so the whole streaming section is inside:

```python
    full_text = ""
    try:
        async with client.messages.stream(**stream_kwargs) as stream:
            # ... existing body unchanged ...
            pass
    except APIStatusError as exc:
        # Deliberately does not include the raw message: it can echo request
        # content. The code is what the UI needs.
        raise ScanError(classify_anthropic_error(exc)) from exc
```

Keep the existing body of the `async with` exactly as it is — only the `try`/`except` and indentation change.

- [ ] **Step 6: Surface the code over HTTP**

In `app/routers/v2/sessions.py`, find the endpoint that runs the analysis and add a handler so the code reaches the client as a 502 with a machine-readable body:

```python
from app.errors import ScanError

# inside the analyze endpoint, around the call into claude_parser:
    try:
        result = await parse_card(...)   # existing call, arguments unchanged
    except ScanError as exc:
        raise HTTPException(
            status_code=502,
            detail={"scan_error_code": exc.code.value},
        ) from exc
```

- [ ] **Step 7: Run the full suite**

Run: `venv/bin/python3 -m pytest tests/ -q`
Expected: PASS, no new failures

- [ ] **Step 8: Commit**

```bash
git add app/errors.py app/services/claude_parser.py app/routers/v2/sessions.py tests/test_scan_errors.py
git commit -m "feat: translate Anthropic SDK errors into typed scan error codes"
```

---

## Task 5: Anthropic health and model listing

**Files:**
- Create: `app/services/anthropic_health.py`
- Test: `tests/test_anthropic_health.py`

**Why:** the wizard must reject a bad key *at setup time*, and the model picker must list what the friend's account can actually reach — a hardcoded list would go stale exactly when it matters.

- [ ] **Step 1: Write the failing test**

```python
"""Key validation and model listing, with the network stubbed out."""
import pytest

from app.services.anthropic_health import estimate_cost_per_card, PRICING


def test_estimate_uses_measured_token_counts():
    """~4,240 input + ~600 output tokens for a two-sided card (spec §8)."""
    cents = estimate_cost_per_card("claude-haiku-4-5")
    # 4242 * $1/1M + 600 * $5/1M = $0.00724
    assert 0.6 < cents < 0.9


def test_sonnet_5_is_cheaper_than_sonnet_4_6():
    """The whole reason for changing the default."""
    assert estimate_cost_per_card("claude-sonnet-5") < estimate_cost_per_card("claude-sonnet-4-6")


def test_unknown_model_returns_none_rather_than_guessing():
    assert estimate_cost_per_card("claude-something-unreleased") is None


def test_opus_models_are_flagged_as_expensive():
    """claude_parser.py enables adaptive thinking for any model containing
    'opus', which multiplies cost. The picker must be able to warn."""
    from app.services.anthropic_health import uses_extended_thinking
    assert uses_extended_thinking("claude-opus-5") is True
    assert uses_extended_thinking("claude-sonnet-5") is False


def test_pricing_table_covers_the_default_model():
    assert "claude-sonnet-5" in PRICING
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_anthropic_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.anthropic_health'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Everything the app says to Anthropic that is not a card scan: validating a
key, listing models, and estimating what a scan will cost.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import anthropic

from app.errors import classify_anthropic_error

logger = logging.getLogger(__name__)

# Measured against the app's own SYSTEM_PROMPT/_RULES/_SCHEMA with two images
# at MAX_DIMENSION=1568 and an empty few-shot block. See spec §8.
TOKENS_IN_PER_CARD = 4242
TOKENS_OUT_PER_CARD = 600

# USD per million tokens: (input, output). Extend when models change; an absent
# model simply yields no estimate rather than a wrong one.
PRICING: Dict[str, Tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def uses_extended_thinking(model: str) -> bool:
    """Mirror the branch in claude_parser.py that enables adaptive thinking.

    Kept in sync deliberately: selecting an Opus model both moves to a higher
    price tier and adds thinking tokens, so the picker warns about it.
    """
    return "opus" in model


def estimate_cost_per_card(model: str) -> Optional[float]:
    """US cents for one two-sided card scan, or None for an unpriced model."""
    prices = PRICING.get(model)
    if prices is None:
        return None
    in_rate, out_rate = prices
    dollars = (TOKENS_IN_PER_CARD * in_rate + TOKENS_OUT_PER_CARD * out_rate) / 1_000_000
    return dollars * 100


async def validate_api_key(api_key: str) -> Tuple[bool, Optional[str]]:
    """Return (ok, error_code). Makes the cheapest call that proves the key works."""
    client = anthropic.AsyncAnthropic(api_key=api_key)
    try:
        await client.models.list(limit=1)
        return True, None
    except Exception as exc:  # noqa: BLE001 — every failure is a validation failure
        return False, classify_anthropic_error(exc).value


async def list_models(api_key: str) -> List[dict]:
    """Models this account can reach, newest first, with a cost estimate each.

    Listing live is what lets an install survive a model retirement without a
    rebuild — see spec W10.
    """
    client = anthropic.AsyncAnthropic(api_key=api_key)
    out: List[dict] = []
    async for model in client.models.list():
        out.append({
            "id": model.id,
            "display_name": getattr(model, "display_name", model.id),
            "cost_per_card_cents": estimate_cost_per_card(model.id),
            "extended_thinking": uses_extended_thinking(model.id),
        })
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python3 -m pytest tests/test_anthropic_health.py -v`
Expected: PASS, 5 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/anthropic_health.py tests/test_anthropic_health.py
git commit -m "feat: add key validation, live model listing, and per-card cost estimates"
```

---

## Task 6: Setup and settings endpoints

**Files:**
- Create: `app/routers/v2/setup.py`
- Modify: `app/routers/v2/settings.py`, `app/main.py`
- Test: `tests/test_setup_endpoints.py`

- [ ] **Step 1: Write the failing test**

```python
"""Setup + settings endpoints. The API key must never come back out."""
from app.services import app_config


def test_status_reports_incomplete_before_setup(client_with_test_db, tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "DEFAULT_CONFIG_PATH", tmp_path / "config.json")
    r = client_with_test_db.get("/api/v2/setup/status")
    assert r.status_code == 200
    assert r.json()["setup_complete"] is False


def test_status_never_returns_the_api_key(client_with_test_db, tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(app_config, "DEFAULT_CONFIG_PATH", path)
    app_config.save_config({"anthropic_api_key": "sk-ant-supersecret"}, path)

    r = client_with_test_db.get("/api/v2/setup/status")
    assert r.status_code == 200
    assert "sk-ant-supersecret" not in r.text
    assert r.json()["setup_complete"] is True


def test_capabilities_endpoint_returns_three_states(client_with_test_db):
    r = client_with_test_db.get("/api/v2/settings/capabilities")
    assert r.status_code == 200
    body = r.json()["destinations"]
    assert set(body) == {"odoo", "google_contacts", "odoo_export", "google_csv"}
    assert body["google_csv"] == "configured"


def test_model_can_be_changed_without_resupplying_the_key(client_with_test_db, tmp_path, monkeypatch):
    """The Settings model picker sends only a model; it must not 422."""
    path = tmp_path / "config.json"
    monkeypatch.setattr(app_config, "DEFAULT_CONFIG_PATH", path)
    app_config.save_config({"anthropic_api_key": "sk-ant-existing"}, path)

    r = client_with_test_db.patch("/api/v2/setup/model", json={"claude_model": "claude-haiku-4-5"})
    assert r.status_code == 200, r.text
    assert r.json()["claude_model"] == "claude-haiku-4-5"
    # The key must survive a model change.
    assert app_config.load_config(path)["anthropic_api_key"] == "sk-ant-existing"


def test_saving_config_rejects_an_empty_key(client_with_test_db, tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "DEFAULT_CONFIG_PATH", tmp_path / "config.json")
    r = client_with_test_db.post("/api/v2/setup/complete", json={"anthropic_api_key": ""})
    assert r.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_setup_endpoints.py -v`
Expected: FAIL — all five 404, since the routes do not exist

- [ ] **Step 3: Write the setup router**

Create `app/routers/v2/setup.py`:

```python
"""First-run setup endpoints.

Deliberately separate from settings.py: these run before the app is configured,
and the response bodies are audited to guarantee the API key never leaves the
process.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import app_config
from app.services.anthropic_health import validate_api_key

router = APIRouter(prefix="/api/v2/setup", tags=["setup"])


class SetupStatus(BaseModel):
    setup_complete: bool
    # A four-character suffix so the user can tell which key is stored without
    # the key itself ever being returned.
    api_key_suffix: str | None = None
    claude_model: str


class ValidateKeyRequest(BaseModel):
    anthropic_api_key: str = Field(min_length=1)


class ValidateKeyResponse(BaseModel):
    valid: bool
    error_code: str | None = None


class CompleteSetupRequest(BaseModel):
    anthropic_api_key: str = Field(min_length=1)
    claude_model: str = "claude-sonnet-5"


@router.get("/status", response_model=SetupStatus)
async def setup_status() -> SetupStatus:
    cfg = app_config.load_config()
    key = cfg.get("anthropic_api_key") or ""
    return SetupStatus(
        setup_complete=bool(key),
        api_key_suffix=key[-4:] if key else None,
        claude_model=cfg.get("claude_model", "claude-sonnet-5"),
    )


@router.post("/validate-key", response_model=ValidateKeyResponse)
async def validate_key(body: ValidateKeyRequest) -> ValidateKeyResponse:
    """Check a key before storing it, so a typo fails here and not mid-scan."""
    ok, code = await validate_api_key(body.anthropic_api_key)
    return ValidateKeyResponse(valid=ok, error_code=code)


class SetModelRequest(BaseModel):
    claude_model: str = Field(min_length=1)


@router.patch("/model", response_model=SetupStatus)
async def set_model(body: SetModelRequest) -> SetupStatus:
    """Change the scan model without touching the stored key.

    Separate from /complete because that endpoint requires an API key, and a
    model change must work on an already-configured install.
    """
    app_config.update_config({"claude_model": body.claude_model})

    from app.config import Settings
    import app.config as config_module
    config_module.settings = Settings()

    return await setup_status()


@router.post("/complete", response_model=SetupStatus)
async def complete_setup(body: CompleteSetupRequest) -> SetupStatus:
    ok, code = await validate_api_key(body.anthropic_api_key)
    if not ok:
        raise HTTPException(status_code=400, detail={"error_code": code})

    app_config.update_config({
        "anthropic_api_key": body.anthropic_api_key,
        "claude_model": body.claude_model,
    })

    # Refresh the live settings object so the very next scan uses the new key
    # without requiring a restart.
    from app.config import Settings
    import app.config as config_module
    config_module.settings = Settings()

    return await setup_status()
```

- [ ] **Step 4: Add the settings endpoints**

Append to `app/routers/v2/settings.py`:

```python
from app.services.anthropic_health import list_models
from app.services.capabilities import current_states


@router.get("/capabilities")
async def get_capabilities():
    """Which destinations are configured / deferred / unavailable.

    One source of truth, so the export selector and the settings page cannot
    disagree about what this install can do.
    """
    return {"destinations": current_states()}


@router.get("/models")
async def get_models():
    """Models this account can reach. Listed live so a retired model does not
    permanently break an install that cannot be updated."""
    from app.config import settings

    if not settings.anthropic_api_key:
        return {"models": [], "current": settings.claude_model}
    try:
        models = await list_models(settings.anthropic_api_key)
    except Exception:  # noqa: BLE001
        logger.exception("Could not list models")
        return {"models": [], "current": settings.claude_model}
    return {"models": models, "current": settings.claude_model}
```

- [ ] **Step 5: Register the setup router**

In `app/main.py`, add the import alongside the other v2 routers:

```python
from app.routers.v2 import setup as v2_setup
```

and register it next to the others:

```python
app.include_router(v2_setup.router)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `venv/bin/python3 -m pytest tests/test_setup_endpoints.py -v`
Expected: PASS, 5 passed

- [ ] **Step 7: Verify against the running backend**

```bash
launchctl unload ~/Library/LaunchAgents/co.nxta.nxt-a1-backend.plist && launchctl load ~/Library/LaunchAgents/co.nxta.nxt-a1-backend.plist
sleep 3
curl -s localhost:8000/api/v2/settings/capabilities | head -c 300
```
Expected: JSON with four destination keys.

> **Watch out:** a 200 response proves little here — `app/main.py` has an SPA
> catch-all that returns `index.html` for unmatched paths. Confirm the body is
> JSON, not HTML.

- [ ] **Step 8: Commit**

```bash
git add app/routers/v2/setup.py app/routers/v2/settings.py app/main.py tests/test_setup_endpoints.py
git commit -m "feat: add setup status, key validation, capabilities and model endpoints"
```

---

## Task 7: Frontend API client and i18n strings

**Files:**
- Create: `frontend/src/api/setup.ts`
- Modify: `frontend/src/i18n.ts`

- [ ] **Step 1: Write the API client**

```typescript
// Setup + capability endpoints. The API key is write-only: the backend never
// returns it, so there is no getter for it here either.
import { get, patch, post } from './client'

export type DestinationState = 'configured' | 'deferred' | 'unavailable'

export interface SetupStatus {
  setup_complete: boolean
  api_key_suffix: string | null
  claude_model: string
}

export interface ModelOption {
  id: string
  display_name: string
  cost_per_card_cents: number | null
  extended_thinking: boolean
}

export const getSetupStatus = () => get<SetupStatus>('/api/v2/setup/status')

export const validateKey = (anthropic_api_key: string) =>
  post<{ valid: boolean; error_code: string | null }>('/api/v2/setup/validate-key', { anthropic_api_key })

export const completeSetup = (anthropic_api_key: string, claude_model: string) =>
  post<SetupStatus>('/api/v2/setup/complete', { anthropic_api_key, claude_model })

export const getCapabilities = () =>
  get<{ destinations: Record<string, DestinationState> }>('/api/v2/settings/capabilities')

export const getModels = () =>
  get<{ models: ModelOption[]; current: string }>('/api/v2/settings/models')

// Changing the model must NOT go through /setup/complete — that endpoint
// requires a non-empty API key and would reject a model-only change.
export const setModel = (claude_model: string) =>
  patch<SetupStatus>('/api/v2/setup/model', { claude_model })
```

- [ ] **Step 2: Add i18n strings in all three languages**

In `frontend/src/i18n.ts`, add these keys to the `ja`, `en`, and `zh-TW` blocks. **All three are mandatory** — `tsc` fails the build if a key is missing from any language.

English:

```typescript
    // Setup wizard
    setupWelcome: 'Welcome to 名片整理器',
    setupPrereqTitle: 'Before you start, you will need',
    setupPrereqEmail: 'An email address',
    setupPrereqCard: 'A credit card, to create your Claude account',
    setupPrereqNet: 'An internet connection',
    setupPrereqTime: 'About 20 minutes',
    setupPrereqCost: 'Scanning costs about US$5 for 300 cards.',
    setupPrereqContinue: 'I have these — continue',
    setupKeyTitle: 'Paste your Claude API key',
    setupKeyHelp: 'Create one at console.anthropic.com, then paste it here.',
    setupKeyPlaceholder: 'sk-ant-...',
    setupKeyChecking: 'Checking your key…',
    setupKeyInvalid: 'That key was not accepted. Check for a missing character.',
    setupCompanyTitle: 'Add your company',
    setupCompanyHelp: 'This is your own company, used to tell your cards from theirs.',
    setupDone: 'All set',

    // Scan errors
    scanErrInvalidKey: 'Your Claude API key was rejected. Check it in Settings.',
    scanErrCreditExhausted: 'Your Claude credit has run out. Top it up at console.anthropic.com.',
    scanErrModelUnavailable: 'The selected Claude model is no longer available. Choose another in Settings.',
    scanErrRateLimited: 'Too many requests at once. Wait a moment and try again.',
    scanErrUnknown: 'The scan failed. Please try again.',

    // Deferred features
    deferredTitle: 'Additional setup needed',
    deferredBody: 'This feature needs further settings. Please contact Koji.',
    deferredClose: 'Close',

    // Model picker
    modelTitle: 'Scan model',
    modelCostPerCard: 'about {cents}¢ per card',
    modelCostUnknown: 'cost unknown',
    modelThinkingWarning: 'Extended thinking — significantly more expensive',
```

Japanese:

```typescript
    setupWelcome: '名片整理器へようこそ',
    setupPrereqTitle: '始める前に必要なもの',
    setupPrereqEmail: 'メールアドレス',
    setupPrereqCard: 'クレジットカード（Claudeアカウント作成用）',
    setupPrereqNet: 'インターネット接続',
    setupPrereqTime: '約20分',
    setupPrereqCost: 'スキャン費用は名刺300枚でおよそ5米ドルです。',
    setupPrereqContinue: '準備できました — 次へ',
    setupKeyTitle: 'Claude APIキーを貼り付けてください',
    setupKeyHelp: 'console.anthropic.com で作成し、ここに貼り付けます。',
    setupKeyPlaceholder: 'sk-ant-...',
    setupKeyChecking: 'キーを確認しています…',
    setupKeyInvalid: 'このキーは受け付けられませんでした。文字の抜けをご確認ください。',
    setupCompanyTitle: '自社を追加',
    setupCompanyHelp: '自分の会社です。相手の名刺と区別するために使います。',
    setupDone: '設定完了',

    scanErrInvalidKey: 'Claude APIキーが拒否されました。設定でご確認ください。',
    scanErrCreditExhausted: 'Claudeのクレジットが不足しています。console.anthropic.com でチャージしてください。',
    scanErrModelUnavailable: '選択中のClaudeモデルは利用できなくなりました。設定で別のモデルを選んでください。',
    scanErrRateLimited: 'リクエストが多すぎます。少し待ってからお試しください。',
    scanErrUnknown: 'スキャンに失敗しました。もう一度お試しください。',

    deferredTitle: '追加設定が必要です',
    deferredBody: 'この機能には追加の設定が必要です。Kojiにご連絡ください。',
    deferredClose: '閉じる',

    modelTitle: 'スキャンモデル',
    modelCostPerCard: '1枚あたり約{cents}セント',
    modelCostUnknown: '費用不明',
    modelThinkingWarning: '拡張思考 — 費用が大幅に高くなります',
```

Traditional Chinese:

```typescript
    setupWelcome: '歡迎使用名片整理器',
    setupPrereqTitle: '開始前，您需要準備',
    setupPrereqEmail: '電子郵件地址',
    setupPrereqCard: '信用卡（用於建立 Claude 帳戶）',
    setupPrereqNet: '網路連線',
    setupPrereqTime: '約 20 分鐘',
    setupPrereqCost: '掃描費用約為每 300 張名片 5 美元。',
    setupPrereqContinue: '已準備好 — 繼續',
    setupKeyTitle: '請貼上您的 Claude API 金鑰',
    setupKeyHelp: '請至 console.anthropic.com 建立後貼在此處。',
    setupKeyPlaceholder: 'sk-ant-...',
    setupKeyChecking: '正在驗證金鑰…',
    setupKeyInvalid: '此金鑰未被接受，請檢查是否有遺漏字元。',
    setupCompanyTitle: '新增您的公司',
    setupCompanyHelp: '這是您自己的公司，用於區分您與對方的名片。',
    setupDone: '設定完成',

    scanErrInvalidKey: 'Claude API 金鑰遭拒。請至設定確認。',
    scanErrCreditExhausted: 'Claude 額度已用完。請至 console.anthropic.com 儲值。',
    scanErrModelUnavailable: '所選的 Claude 模型已無法使用。請至設定選擇其他模型。',
    scanErrRateLimited: '請求過於頻繁，請稍候再試。',
    scanErrUnknown: '掃描失敗，請再試一次。',

    deferredTitle: '需要進一步設定',
    deferredBody: '此功能需要進一步設定，請聯絡 Koji。',
    deferredClose: '關閉',

    modelTitle: '掃描模型',
    modelCostPerCard: '每張約 {cents} 美分',
    modelCostUnknown: '費用未知',
    modelThinkingWarning: '延伸思考 — 費用明顯較高',
```

- [ ] **Step 3: Verify the build type-checks**

Run: `cd frontend && npm run build`
Expected: build succeeds. A missing key in any language surfaces here as a `tsc` error naming the key.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/setup.ts frontend/src/i18n.ts
git commit -m "feat: add setup API client and i18n strings in ja/en/zh-TW"
```

---

## Task 8: Deferred feature dialog and three-state destinations

**Files:**
- Create: `frontend/src/components/DeferredFeatureDialog.tsx`
- Modify: `frontend/src/components/ExportDestinationSelector.tsx`

- [ ] **Step 1: Write the dialog**

```tsx
// Shown when a user presses a button for a feature that exists but is not
// configured on this install — currently only Google Contacts sync.
//
// Styled as information, not as an error: nothing has gone wrong, and the
// button is deliberately visible so the feature is discoverable.
import * as Dialog from '@radix-ui/react-dialog'
import { useLang } from '../LangContext'

export function DeferredFeatureDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { t } = useLang()

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/30" />
        <Dialog.Content className="fixed left-1/2 top-1/2 w-[min(24rem,90vw)] -translate-x-1/2 -translate-y-1/2 rounded-lg bg-white p-6 shadow-xl">
          <Dialog.Title className="text-base font-medium text-gray-900">
            {t.deferredTitle}
          </Dialog.Title>
          <Dialog.Description className="mt-2 text-sm text-gray-600">
            {t.deferredBody}
          </Dialog.Description>
          <div className="mt-5 flex justify-end">
            <Dialog.Close className="rounded-md bg-gray-900 px-3 py-1.5 text-sm text-white">
              {t.deferredClose}
            </Dialog.Close>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
```

- [ ] **Step 2: Drive destination state from the backend**

In `frontend/src/components/ExportDestinationSelector.tsx`, the destination list at lines 26–29 hardcodes `configured: true`. Replace that hardcoding with the live capability query, and hide `unavailable` entries while keeping `deferred` ones visible:

```tsx
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { getCapabilities, type DestinationState } from '../api/setup'
import { DeferredFeatureDialog } from './DeferredFeatureDialog'

// ... inside the component ...
const { data: caps } = useQuery({
  queryKey: ['capabilities'],
  queryFn: getCapabilities,
})
const [deferredOpen, setDeferredOpen] = useState(false)

const stateOf = (key: string): DestinationState =>
  (caps?.destinations?.[key] ?? 'configured') as DestinationState

// Hide destinations that do not apply to this install; keep deferred ones.
const visibleDestinations = DESTINATIONS.filter(d => stateOf(d.key) !== 'unavailable')
```

When rendering each destination, a `deferred` one must not be selectable — pressing it opens the dialog instead of toggling the checkbox:

```tsx
{visibleDestinations.map(dest => {
  const deferred = stateOf(dest.key) === 'deferred'
  return (
    <label
      key={dest.key}
      className={deferred ? 'flex items-center gap-2 opacity-60' : 'flex items-center gap-2'}
      onClick={deferred ? (e) => { e.preventDefault(); setDeferredOpen(true) } : undefined}
    >
      <input
        type="checkbox"
        checked={!deferred && selected.includes(dest.key)}
        readOnly={deferred}
        onChange={deferred ? undefined : () => toggle(dest.key)}
      />
      <span>{dest.label}</span>
    </label>
  )
})}

<DeferredFeatureDialog open={deferredOpen} onOpenChange={setDeferredOpen} />
```

Keep the existing `DESTINATIONS` array but drop the now-meaningless `configured: true` field from each entry.

- [ ] **Step 3: Build and verify in the browser**

```bash
cd frontend && npm run build
```

Then hard-refresh with Cmd+Shift+R and check, on the export screen:

1. Odoo and "Odoo Export" behave as they do today (Koji's install has Odoo configured, so both stay `configured`).
2. Temporarily blank `GOOGLE_REFRESH_TOKEN` in `.env`, reload the LaunchAgent, hard-refresh: the Google Contacts row is still **visible**, dimmed, and clicking it opens the dialog rather than ticking the box.
3. Restore `.env` and reload.

> A passing `tsc` build does not prove any of this. Click it.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/DeferredFeatureDialog.tsx frontend/src/components/ExportDestinationSelector.tsx
git commit -m "feat: three-state export destinations with a deferred-feature dialog"
```

---

## Task 9: First-run setup wizard

**Files:**
- Create: `frontend/src/components/SetupWizard.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Write the wizard**

```tsx
// First-run flow, shown when the backend reports setup_complete === false.
//
// Step order matters: the prerequisites checklist comes FIRST, before anything
// is asked of the user, so nobody starts and stalls at the billing step with
// half a setup behind them (spec §10, "friend stalls at the billing step").
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { completeSetup, validateKey } from '../api/setup'
import { useLang } from '../LangContext'

type Step = 'prereq' | 'key' | 'done'

export function SetupWizard() {
  const { t } = useLang()
  const qc = useQueryClient()
  const [step, setStep] = useState<Step>('prereq')
  const [key, setKey] = useState('')
  const [checking, setChecking] = useState(false)
  const [invalid, setInvalid] = useState(false)

  async function submitKey() {
    setChecking(true)
    setInvalid(false)
    try {
      const res = await validateKey(key)
      if (!res.valid) {
        setInvalid(true)
        return
      }
      await completeSetup(key, 'claude-sonnet-5')
      await qc.invalidateQueries({ queryKey: ['setup-status'] })
      setStep('done')
    } finally {
      setChecking(false)
    }
  }

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center p-6">
      <div className="w-full max-w-md rounded-lg bg-white p-8 shadow">
        <h1 className="text-lg font-medium text-gray-900">{t.setupWelcome}</h1>

        {step === 'prereq' && (
          <div className="mt-5">
            <p className="text-sm font-medium text-gray-700">{t.setupPrereqTitle}</p>
            <ul className="mt-3 space-y-2 text-sm text-gray-600 list-disc pl-5">
              <li>{t.setupPrereqEmail}</li>
              <li>{t.setupPrereqCard}</li>
              <li>{t.setupPrereqNet}</li>
              <li>{t.setupPrereqTime}</li>
            </ul>
            <p className="mt-4 rounded-md bg-blue-50 p-3 text-sm text-blue-900">
              {t.setupPrereqCost}
            </p>
            <button
              className="mt-5 w-full rounded-md bg-gray-900 px-4 py-2 text-sm text-white"
              onClick={() => setStep('key')}
            >
              {t.setupPrereqContinue}
            </button>
          </div>
        )}

        {step === 'key' && (
          <div className="mt-5">
            <p className="text-sm font-medium text-gray-700">{t.setupKeyTitle}</p>
            <p className="mt-1 text-sm text-gray-500">{t.setupKeyHelp}</p>
            <input
              type="password"
              value={key}
              onChange={e => setKey(e.target.value)}
              placeholder={t.setupKeyPlaceholder}
              className="mt-3 w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
            />
            {invalid && <p className="mt-2 text-sm text-red-600">{t.setupKeyInvalid}</p>}
            <button
              disabled={!key || checking}
              onClick={submitKey}
              className="mt-4 w-full rounded-md bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
            >
              {checking ? t.setupKeyChecking : t.setupPrereqContinue}
            </button>
          </div>
        )}

        {step === 'done' && (
          <div className="mt-5">
            <p className="text-sm text-gray-700">{t.setupDone}</p>
            <button
              className="mt-4 w-full rounded-md bg-gray-900 px-4 py-2 text-sm text-white"
              onClick={() => window.location.reload()}
            >
              {t.setupDone}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Gate the app on setup, and delete the Docker launcher**

In `frontend/src/App.tsx`, `BackendGate` currently posts to a launcher on `http://127.0.0.1:8001`, tries to start Docker, and tells the user to "check that Docker is running." None of that applies. Delete the `LAUNCHER` constant, the `startBackend()`/`sendBeacon` calls, and the Docker wording, and replace the gate with a setup check:

```tsx
import { useQuery } from '@tanstack/react-query'
import { getSetupStatus } from './api/setup'
import { SetupWizard } from './components/SetupWizard'

function SetupGate({ children }: { children: React.ReactNode }) {
  const { data, isLoading } = useQuery({
    queryKey: ['setup-status'],
    queryFn: getSetupStatus,
    retry: false,
  })

  if (isLoading) return null
  if (data && !data.setup_complete) return <SetupWizard />
  return <>{children}</>
}
```

Replace the `<BackendGate>` usage with `<SetupGate>`.

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: build succeeds

- [ ] **Step 4: Verify the wizard actually appears**

The wizard only shows when no key is configured, and Koji's install has one in `.env`. To exercise it honestly:

```bash
# Create a throwaway macOS user account, or temporarily move the real config
# and blank the env key, whichever you prefer. The point is that the app must
# be observed with NO key available.
mv ~/.nxt-a1/config.json ~/.nxt-a1/config.json.bak 2>/dev/null || true
```

Comment `ANTHROPIC_API_KEY` out of `.env`, reload the LaunchAgent, hard-refresh. Confirm:

1. The prerequisites checklist is the **first** screen, before any input.
2. Pasting a deliberately wrong key shows `setupKeyInvalid` and does not advance.
3. Pasting the real key advances to done, and `~/.nxt-a1/config.json` exists with mode `600` (`ls -l ~/.nxt-a1/config.json`).
4. Switching the language cycles the wizard text through ja / en / zh-TW.

Then restore `.env` and `config.json.bak`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/SetupWizard.tsx frontend/src/App.tsx
git commit -m "feat: first-run setup wizard, replacing the Docker backend gate"
```

---

## Task 10: Model picker and scan error display

**Files:**
- Modify: `frontend/src/pages/SettingsPage.tsx`, `frontend/src/pages/ScanPage.tsx`

- [ ] **Step 1: Add the model picker to Settings**

`SettingsPage` renders a series of `<section>` blocks (my companies, occasions, countries). Add one more, following the same shape:

```tsx
import { getModels, setModel } from '../api/setup'

function ModelSection() {
  const { t } = useLang()
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['models'], queryFn: getModels })

  const mutation = useMutation({
    mutationFn: (id: string) => setModel(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['models'] }),
  })

  return (
    <section>
      <h2 className="text-sm font-medium text-gray-700 mb-3">{t.modelTitle}</h2>
      <div className="space-y-2">
        {(data?.models ?? []).map(m => (
          <label key={m.id} className="flex items-center gap-3 text-sm">
            <input
              type="radio"
              name="claude-model"
              checked={data?.current === m.id}
              onChange={() => mutation.mutate(m.id)}
            />
            <span className="font-medium">{m.display_name}</span>
            <span className="text-gray-500">
              {m.cost_per_card_cents != null
                ? t.modelCostPerCard.replace('{cents}', m.cost_per_card_cents.toFixed(1))
                : t.modelCostUnknown}
            </span>
            {m.extended_thinking && (
              <span className="text-amber-700">{t.modelThinkingWarning}</span>
            )}
          </label>
        ))}
      </div>
    </section>
  )
}
```

Render `<ModelSection />` alongside the existing sections.

- [ ] **Step 2: Translate scan errors in ScanPage**

Where `ScanPage` surfaces a failure from the analyze call, map the backend code to a translated string instead of printing the raw error:

```tsx
const SCAN_ERROR_KEYS: Record<string, keyof typeof t> = {
  invalid_api_key: 'scanErrInvalidKey',
  credit_exhausted: 'scanErrCreditExhausted',
  model_unavailable: 'scanErrModelUnavailable',
  rate_limited: 'scanErrRateLimited',
  unknown: 'scanErrUnknown',
}

function scanErrorMessage(err: unknown): string {
  // The backend sends 502 with {"detail": {"scan_error_code": "..."}}; the
  // client throws an Error whose message contains the serialised body.
  const text = err instanceof Error ? err.message : String(err)
  const match = text.match(/"scan_error_code"\s*:\s*"([a-z_]+)"/)
  const code = match?.[1] ?? 'unknown'
  return t[SCAN_ERROR_KEYS[code] ?? 'scanErrUnknown'] as string
}
```

Use `scanErrorMessage(err)` wherever the raw error is currently displayed.

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: build succeeds

- [ ] **Step 4: Verify both in the browser**

1. Open Settings. The model list is populated from the live API and `claude-sonnet-5` is selected. Selecting a different model persists across a reload.
2. Force an error: set `claude_model` to a nonsense value in `~/.nxt-a1/config.json`, reload the LaunchAgent, and run a scan. Confirm the UI shows the translated `scanErrModelUnavailable` text — **not** a raw `502` or a JSON blob. Repeat with the language switched to ja.
3. Restore the real model.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/SettingsPage.tsx frontend/src/pages/ScanPage.tsx
git commit -m "feat: live model picker in Settings and translated scan errors"
```

---

## Task 11: Full-suite verification

- [ ] **Step 1: Backend suite**

Run: `venv/bin/python3 -m pytest tests/ -v`
Expected: PASS, including the five new test files

- [ ] **Step 2: Frontend build**

Run: `cd frontend && npm run build`
Expected: build succeeds with no `tsc` errors

- [ ] **Step 3: Confirm the key never leaves the process**

```bash
curl -s localhost:8000/api/v2/setup/status
curl -s localhost:8000/api/v2/settings/models
```
Expected: neither response contains the key. Only a four-character suffix appears.

- [ ] **Step 4: Confirm Koji's own install is unchanged**

With `.env` intact: Odoo and Google Contacts both still appear as normal working destinations, and a scan completes end to end.

- [ ] **Step 5: Commit any fixes and open the PR**

```bash
git push -u origin feat/community-edition-a1
gh pr create --title "Community Edition A1: setup and configuration" --body "$(cat <<'EOF'
Implements Phase A1 of the Community Edition spec: W3, W4, W5, W7, W10.

- `~/.nxt-a1/config.json` credential store, layered under env vars so Koji's `.env` still wins
- First-run wizard opening with a prerequisites checklist
- Three-state export destinations: Google Contacts deferred with a dialog, Odoo hidden
- Typed, translated scan errors (invalid key / credit exhausted / model unavailable / rate limited)
- Live model picker with per-card cost estimates; default moved to `claude-sonnet-5`

Spec: `docs/superpowers/specs/2026-09-08-community-edition-distribution-design.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
