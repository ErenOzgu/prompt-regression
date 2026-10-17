"""Where completions come from: a command of yours, the Anthropic Messages API, or a recording of either."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

API_VERSION = "2023-06-01"
RETRY_STATUS = {429, 500, 502, 503, 504, 529}


class ProviderError(Exception):
    """One completion could not be had. The run counts as failed; the suite carries on."""


@dataclass
class Completion:
    text: str
    seconds: float = 0.0
    tokens_in: int | None = None
    tokens_out: int | None = None


class Provider(Protocol):
    name: str

    def complete(self, prompt: str, system: str, case_id: str, run: int) -> Completion: ...


def recording_key(prompt: str, system: str, run: int) -> str:
    return hashlib.sha256(json.dumps([system, prompt, run]).encode()).hexdigest()[:24]


class CmdProvider:
    """Runs ``command`` (an argument list, no shell) with the prompt on stdin; the reply is stdout.

    The system text, the case id and the run number are passed in PROMPT_REGRESSION_SYSTEM,
    PROMPT_REGRESSION_CASE and PROMPT_REGRESSION_RUN.
    """

    def __init__(self, command: list[str], timeout: float = 60, cwd: Path | None = None) -> None:
        self.command, self.timeout, self.cwd = command, timeout, cwd
        self.name = "cmd " + " ".join(command)

    def complete(self, prompt: str, system: str, case_id: str, run: int) -> Completion:
        env = {
            **os.environ,
            "PROMPT_REGRESSION_SYSTEM": system,
            "PROMPT_REGRESSION_CASE": case_id,
            "PROMPT_REGRESSION_RUN": str(run),
        }
        start = time.monotonic()
        try:
            p = subprocess.run(
                self.command,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
                cwd=self.cwd,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ProviderError(f"cannot run {self.command[0]!r}: not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(f"{self.command[0]!r} took longer than {self.timeout:g} s") from exc
        if p.returncode != 0:
            tail = (p.stderr or "").strip().splitlines()[-1:] or [""]
            raise ProviderError(f"{self.command[0]!r} exited {p.returncode}: {tail[0][:200]}")
        text = p.stdout[:-1] if p.stdout.endswith("\n") else p.stdout
        return Completion(text, time.monotonic() - start)


class AnthropicProvider:
    def __init__(
        self,
        model: str,
        max_tokens: int = 1024,
        temperature: float | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 120,
        sleep=time.sleep,  # noqa: ANN001
    ) -> None:
        self.model, self.max_tokens, self.temperature = model, max_tokens, temperature
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.base = (base_url or os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip(
            "/"
        )
        self.timeout, self.sleep = timeout, sleep
        self.name = f"anthropic {model}"
        if not self.api_key:
            raise ProviderError("the anthropic provider needs ANTHROPIC_API_KEY in the environment")

    def complete(self, prompt: str, system: str, case_id: str, run: int) -> Completion:
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system
        if self.temperature is not None:
            body["temperature"] = self.temperature
        payload = json.dumps(body).encode()
        last = ""
        start = time.monotonic()
        for attempt in range(3):
            req = urllib.request.Request(
                self.base + "/v1/messages",
                data=payload,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": API_VERSION,
                    "content-type": "application/json",
                    "user-agent": "prompt-regression",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.load(resp)
                break
            except urllib.error.HTTPError as exc:
                last = f"API returned {exc.code}: {exc.read(200).decode('utf-8', 'replace')}"
                if exc.code not in RETRY_STATUS:
                    raise ProviderError(last) from exc
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                last = f"cannot reach the API: {exc}"
            if attempt < 2:
                self.sleep(2**attempt)
        else:
            raise ProviderError(last)
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        usage = data.get("usage") or {}
        return Completion(
            text, time.monotonic() - start, usage.get("input_tokens"), usage.get("output_tokens")
        )


class ReplayProvider:
    """Answers from a JSON-lines recording made with ``prompt-regression record``."""

    def __init__(self, path: Path) -> None:
        self.name = f"replay {path.name}"
        self.entries: dict[str, str] = {}
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self.entries[row["key"]] = row["text"]
        except OSError as exc:
            raise ProviderError(f"cannot read the recording {path}: {exc.strerror}") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError(f"{path} is not a recording made by prompt-regression: {exc}") from exc

    def complete(self, prompt: str, system: str, case_id: str, run: int) -> Completion:
        key = recording_key(prompt, system, run)
        if key not in self.entries:
            raise ProviderError(
                f"no recording for case {case_id} run {run}: the prompt, the system text or the variables changed since it was recorded"
            )
        return Completion(self.entries[key])


class Recorder:
    """Wraps a provider and keeps what it said, for ``record``."""

    def __init__(self, inner: Provider) -> None:
        self.inner, self.name = inner, inner.name
        self.rows: list[dict[str, Any]] = []

    def complete(self, prompt: str, system: str, case_id: str, run: int) -> Completion:
        got = self.inner.complete(prompt, system, case_id, run)
        self.rows.append(
            {"key": recording_key(prompt, system, run), "case": case_id, "run": run, "text": got.text}
        )
        return got


def make(provider: dict[str, Any], base: Path, model: str | None = None) -> Provider:
    kind = provider["type"]
    if kind == "cmd":
        return CmdProvider(provider["command"], provider.get("timeout", 60), cwd=base)
    if kind == "anthropic":
        return AnthropicProvider(
            model or provider["model"], provider.get("max_tokens", 1024), provider.get("temperature")
        )
    return ReplayProvider(base / provider["file"])
