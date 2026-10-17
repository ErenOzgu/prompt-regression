import json
import sys

import pytest

from prompt_regression import providers
from prompt_regression.providers import (
    AnthropicProvider,
    CmdProvider,
    ProviderError,
    Recorder,
    ReplayProvider,
    recording_key,
)

from .fakes import FakeAnthropic


class TestCmd:
    def test_the_prompt_goes_in_on_stdin_and_stdout_comes_back(self):
        p = CmdProvider([sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"])
        got = p.complete("hello", "", "c", 1)
        assert got.text == "HELLO"
        assert got.seconds >= 0

    def test_only_one_trailing_newline_is_removed(self):
        p = CmdProvider([sys.executable, "-c", "print('a\\n')"])
        assert p.complete("", "", "c", 1).text == "a\n"

    def test_system_case_and_run_are_in_the_environment(self):
        code = "import os; print(os.environ['PROMPT_REGRESSION_SYSTEM'], os.environ['PROMPT_REGRESSION_CASE'], os.environ['PROMPT_REGRESSION_RUN'])"
        p = CmdProvider([sys.executable, "-c", code])
        assert p.complete("", "be brief", "refund", 3).text == "be brief refund 3"

    def test_arguments_are_not_run_by_a_shell(self):
        p = CmdProvider(["echo", "$(whoami); echo hacked"])
        assert p.complete("", "", "c", 1).text == "$(whoami); echo hacked"

    def test_it_runs_where_it_is_told_to(self, tmp_path):
        p = CmdProvider([sys.executable, "-c", "import os; print(os.getcwd())"], cwd=tmp_path)
        assert p.complete("", "", "c", 1).text == str(tmp_path.resolve())

    def test_a_failing_command_is_a_provider_error_with_its_last_stderr_line(self):
        p = CmdProvider(
            [sys.executable, "-c", "import sys; sys.stderr.write('a\\nquota exceeded\\n'); sys.exit(4)"]
        )
        with pytest.raises(ProviderError, match="exited 4: quota exceeded"):
            p.complete("", "", "c", 1)

    def test_a_missing_command(self):
        with pytest.raises(ProviderError, match="not found"):
            CmdProvider(["no-such-program-xyz"]).complete("", "", "c", 1)

    def test_a_slow_command(self):
        with pytest.raises(ProviderError, match="longer than 1 s"):
            CmdProvider([sys.executable, "-c", "import time; time.sleep(5)"], timeout=1).complete(
                "", "", "c", 1
            )


class TestAnthropic:
    def make(self, server, **kw):
        kw.setdefault("sleep", lambda s: None)
        return AnthropicProvider("some-model", api_key="k-test", base_url=server.url, **kw)

    def test_it_sends_the_documented_request_and_reads_the_text(self):
        server = FakeAnthropic("the answer")
        try:
            got = self.make(server, max_tokens=50, temperature=0.2).complete(
                "the prompt", "the system", "c", 1
            )
            req = server.requests[0]
            body = server.last_request_json()
        finally:
            server.close()
        assert got.text == "the answer"
        assert req["path"] == "/v1/messages"
        assert req["headers"]["x-api-key"] == "k-test"
        assert req["headers"]["anthropic-version"] == "2023-06-01"
        assert body == {
            "model": "some-model",
            "max_tokens": 50,
            "messages": [{"role": "user", "content": "the prompt"}],
            "system": "the system",
            "temperature": 0.2,
        }

    def test_system_and_temperature_are_left_out_when_unset(self):
        server = FakeAnthropic("x")
        try:
            self.make(server).complete("p", "", "c", 1)
            body = server.last_request_json()
        finally:
            server.close()
        assert "system" not in body
        assert "temperature" not in body
        assert body["max_tokens"] == 1024

    def test_token_usage_is_kept_when_the_api_reports_it(self, monkeypatch):
        server = FakeAnthropic("x")
        try:
            orig = providers.urllib.request.urlopen

            def fake(req, timeout):  # noqa: ANN001
                resp = orig(req, timeout=timeout)
                data = json.load(resp)
                data["usage"] = {"input_tokens": 12, "output_tokens": 3}

                class R:
                    def __enter__(self):
                        return self

                    def __exit__(self, *a):  # noqa: ANN002
                        return False

                    def read(self, *a):  # noqa: ANN002
                        return json.dumps(data).encode()

                return R()

            monkeypatch.setattr(providers.urllib.request, "urlopen", fake)
            got = self.make(server).complete("p", "", "c", 1)
        finally:
            server.close()
        assert (got.tokens_in, got.tokens_out) == (12, 3)

    def test_a_busy_api_is_retried_with_a_pause_then_gives_up(self):
        server = FakeAnthropic(status=529)
        pauses = []
        try:
            with pytest.raises(ProviderError, match="API returned 529"):
                self.make(server, sleep=pauses.append).complete("p", "", "c", 1)
            assert len(server.requests) == 3
        finally:
            server.close()
        assert pauses == [1, 2]

    def test_a_client_error_is_not_retried(self):
        server = FakeAnthropic(status=401)
        try:
            with pytest.raises(ProviderError, match="API returned 401"):
                self.make(server).complete("p", "", "c", 1)
            assert len(server.requests) == 1
        finally:
            server.close()

    def test_recovering_after_a_rate_limit(self):
        server = FakeAnthropic("fine")
        server.status = 429
        pauses = []

        def sleeper(s):  # noqa: ANN001
            pauses.append(s)
            server.status = 200  # the limit lifts while we wait

        try:
            got = self.make(server, sleep=sleeper).complete("p", "", "c", 1)
        finally:
            server.close()
        assert got.text == "fine"
        assert pauses == [1]

    def test_an_unreachable_api(self):
        p = AnthropicProvider(
            "m", api_key="k", base_url="http://127.0.0.1:9", sleep=lambda s: None, timeout=2
        )
        with pytest.raises(ProviderError, match="cannot reach the API"):
            p.complete("p", "", "c", 1)

    def test_the_key_comes_from_the_environment_and_is_required(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
            AnthropicProvider("m")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://example.invalid/")
        p = AnthropicProvider("m")
        assert (p.api_key, p.base) == ("from-env", "http://example.invalid")

    def test_the_key_never_appears_in_an_error(self):
        server = FakeAnthropic(status=500)
        try:
            with pytest.raises(ProviderError) as err:
                self.make(server).complete("p", "", "c", 1)
        finally:
            server.close()
        assert "k-test" not in str(err.value)


class TestReplay:
    def write(self, tmp_path, *rows):
        path = tmp_path / "rec.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return path

    def test_it_answers_by_prompt_system_and_run(self, tmp_path):
        key = recording_key("p", "s", 2)
        p = ReplayProvider(self.write(tmp_path, {"key": key, "case": "c", "run": 2, "text": "recorded"}))
        assert p.complete("p", "s", "c", 2).text == "recorded"

    def test_a_changed_prompt_has_no_recording(self, tmp_path):
        path = self.write(tmp_path, {"key": recording_key("p", "", 1), "text": "x"})
        with pytest.raises(ProviderError, match="no recording for case c run 1: the prompt"):
            ReplayProvider(path).complete("p changed", "", "c", 1)

    def test_runs_are_recorded_separately(self):
        assert recording_key("p", "", 1) != recording_key("p", "", 2)
        assert recording_key("p", "a", 1) != recording_key("p", "b", 1)
        assert recording_key("ab", "", 1) != recording_key("a", "b", 1)

    def test_a_missing_or_wrong_file(self, tmp_path):
        with pytest.raises(ProviderError, match="cannot read the recording"):
            ReplayProvider(tmp_path / "nope.jsonl")
        bad = tmp_path / "bad.jsonl"
        bad.write_text("not json\n", encoding="utf-8")
        with pytest.raises(ProviderError, match="not a recording"):
            ReplayProvider(bad)
        other = tmp_path / "other.jsonl"
        other.write_text('{"hello": 1}\n', encoding="utf-8")
        with pytest.raises(ProviderError, match="not a recording"):
            ReplayProvider(other)


def test_the_recorder_keeps_what_the_inner_provider_said():
    inner = CmdProvider([sys.executable, "-c", "print('hi')"])
    rec = Recorder(inner)
    assert rec.complete("p", "s", "c", 1).text == "hi"
    assert rec.rows == [{"key": recording_key("p", "s", 1), "case": "c", "run": 1, "text": "hi"}]
    assert rec.name == inner.name


def test_make_builds_each_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert isinstance(providers.make({"type": "cmd", "command": ["x"]}, tmp_path), CmdProvider)
    a = providers.make({"type": "anthropic", "model": "m1", "max_tokens": 7}, tmp_path, model="m2")
    assert (a.model, a.max_tokens) == ("m2", 7)
    (tmp_path / "r.jsonl").write_text("", encoding="utf-8")
    assert isinstance(providers.make({"type": "replay", "file": "r.jsonl"}, tmp_path), ReplayProvider)
