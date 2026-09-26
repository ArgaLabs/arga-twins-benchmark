"""File relay between a candidate's browser tool and an external Computer Use driver.

This module never operates a browser. The operator connects their existing CUA,
accessibility or browser driver, which executes only the requested UI action and
returns the observed page. Provider URLs and driver internals stay outside the model.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from arga_twins_benchmark.lifecycle import write_private_json

ACTIONS = ("observe", "click", "fill", "type", "press", "scroll", "back", "reload")
KEYS = (
    "Return",
    "Enter",
    "Tab",
    "Shift+Tab",
    "Escape",
    "BackSpace",
    "Delete",
    "Up",
    "Down",
    "Left",
    "Right",
    "Home",
    "End",
    "ControlOrMeta+a",
    "ControlOrMeta+c",
    "ControlOrMeta+v",
    "ControlOrMeta+z",
    "ControlOrMeta+Shift+z",
)


class BrowserRelay:
    def __init__(self, directory: Path, workspaces: object, *, timeout_seconds: float = 120):
        if not isinstance(workspaces, dict) or not workspaces:
            raise ValueError("Browser relay requires provisioned browser workspaces")
        checked_workspaces: dict[str, str] = {}
        for provider, url in cast(dict[object, object], workspaces).items():
            if not isinstance(provider, str) or not isinstance(url, str):
                raise ValueError("Invalid browser workspace")
            parsed = urlsplit(url)
            if (
                parsed.scheme != "http"
                or parsed.hostname != "127.0.0.1"
                or not parsed.port
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("Browser workspaces must use local candidate proxies")
            checked_workspaces[provider] = url
        if timeout_seconds <= 0:
            raise ValueError("Browser driver timeout must be positive")
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        self.providers = tuple(sorted(checked_workspaces))
        self.timeout_seconds = timeout_seconds
        self.session = secrets.token_hex(16)
        self.calls = 0
        self.completed_calls = 0
        self.infrastructure_error: str | None = None
        self.observations: dict[str, str] = {}
        self.lock = asyncio.Lock()
        write_private_json(
            directory / "driver.json",
            {"protocol": "arga-browser-relay/1", "session": self.session, "workspaces": checked_workspaces},
        )

    @property
    def tool(self) -> dict[str, Any]:
        return {
            "name": "browser_ui",
            "description": (
                "Operate the selected provider's frontend through the connected Computer Use driver. "
                "Start with observe. Use only element indices from its latest observation and pass that "
                "observation_id with an action. Every result contains the updated page. APIs remain available; "
                "choose browser, API or mixed steps freely."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "provider": {"type": "string", "enum": list(self.providers)},
                    "action": {"type": "string", "enum": list(ACTIONS)},
                    "observation_id": {"type": "string"},
                    "element": {"type": "integer", "minimum": 0},
                    "text": {"type": "string", "maxLength": 100000},
                    "key": {"type": "string", "enum": list(KEYS)},
                    "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                    "pages": {"type": "number", "minimum": 0.1, "maximum": 3},
                },
                "required": ["provider", "action"],
                "additionalProperties": False,
            },
        }

    def validate(self, arguments: dict[str, Any]) -> str | None:
        if set(arguments) - set(self.tool["input_schema"]["properties"]):
            return "Unknown browser argument"
        provider, action = arguments.get("provider"), arguments.get("action")
        if (
            not isinstance(provider, str)
            or not isinstance(action, str)
            or provider not in self.providers
            or action not in ACTIONS
        ):
            return "Unknown browser provider or action"
        if "element" in arguments and (type(arguments["element"]) is not int or arguments["element"] < 0):
            return "Invalid element index"
        if "text" in arguments and (not isinstance(arguments["text"], str) or len(arguments["text"]) > 100000):
            return "Invalid text"
        if action != "observe" and (
            not self.observations.get(provider) or arguments.get("observation_id") != self.observations[provider]
        ):
            return "Observe this workspace again before acting; observation_id is missing or stale"
        if action in {"click", "fill"}:
            element = arguments.get("element")
            if type(element) is not int or element < 0:
                return "This action requires an observed element index"
        if action in {"fill", "type"} and (
            not isinstance(arguments.get("text"), str) or len(arguments["text"]) > 100000
        ):
            return "This action requires text of at most 100000 characters"
        if action == "press" and arguments.get("key") not in KEYS:
            return "Unsupported page key"
        if action == "scroll":
            pages = arguments.get("pages", 1)
            if (
                arguments.get("direction") not in ("up", "down", "left", "right")
                or type(pages) not in (int, float)
                or not 0.1 <= pages <= 3
            ):
                return "Invalid scroll direction or page count"
        return None

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        async with self.lock:
            if self.infrastructure_error:
                return {"infrastructure_error": self.infrastructure_error}
            if error := self.validate(arguments):
                return {"error": error}
            self.calls += 1
            request_id = f"{self.calls:04d}"
            nonce = secrets.token_hex(16)
            request = {
                "protocol": "arga-browser-relay/1",
                "session": self.session,
                "id": request_id,
                "nonce": nonce,
                "arguments": arguments,
            }
            write_private_json(self.directory / f"{request_id}.request.json", request)
            response_path = self.directory / f"{request_id}.response.json"
            deadline = asyncio.get_running_loop().time() + self.timeout_seconds
            try:
                while asyncio.get_running_loop().time() < deadline:
                    if response_path.exists():
                        if response_path.is_symlink() or response_path.stat().st_size > 512000:
                            raise ValueError("Invalid browser driver response file")
                        raw_response: object = json.loads(response_path.read_text())
                        if not isinstance(raw_response, dict):
                            raise ValueError("Browser driver response must be an object")
                        response = cast(dict[str, Any], raw_response)
                        if any(response.get(key) != request[key] for key in ("session", "id", "nonce")):
                            raise ValueError("Browser driver response does not match the pending action")
                        if response.get("infrastructure_error"):
                            raise ValueError("Browser driver reported an infrastructure failure")
                        observation = response.get("observation")
                        if not isinstance(observation, str) or len(observation) > 100000:
                            raise ValueError("Browser driver must return a bounded text observation")
                        observation_id = f"{request_id}:{nonce}"
                        self.observations[arguments["provider"]] = observation_id
                        self.completed_calls += 1
                        result: dict[str, Any] = {"observation_id": observation_id, "observation": observation}
                        if isinstance(response.get("error"), str):
                            result["error"] = response["error"][:2000]
                        return result
                    await asyncio.sleep(0.1)
                raise TimeoutError("Browser driver did not answer before its deadline")
            except asyncio.CancelledError:
                write_private_json(
                    self.directory / f"{request_id}.cancelled.json", {"id": request_id, "reason": "candidate cancelled"}
                )
                raise
            except (OSError, ValueError, TimeoutError) as exc:
                self.infrastructure_error = str(exc)
                write_private_json(
                    self.directory / f"{request_id}.failed.json",
                    {"id": request_id, "infrastructure_error": self.infrastructure_error},
                )
                return {"infrastructure_error": self.infrastructure_error}
