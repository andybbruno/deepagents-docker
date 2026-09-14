import subprocess
from unittest.mock import MagicMock, patch

import pytest

from deepagents_docker._docker import (
    DockerRunResult,
    docker_available,
    format_docker_error,
    run_docker,
)
from deepagents_docker.errors import DockerError


def test_format_docker_error_returns_plain_stderr() -> None:
    result = DockerRunResult(returncode=1, stdout="", stderr="image not found")
    assert format_docker_error(result) == "image not found"


def test_format_docker_error_extracts_json_message() -> None:
    result = DockerRunResult(
        returncode=1,
        stdout="",
        stderr='{"message": "Conflict. The container name is already in use."}',
    )
    assert format_docker_error(result) == "Conflict. The container name is already in use."


def test_format_docker_error_returns_json_without_message_field() -> None:
    payload = '{"errorDetail":{"code":404}}'
    result = DockerRunResult(returncode=1, stdout="", stderr=payload)
    assert format_docker_error(result) == payload


def test_format_docker_error_falls_back_to_exit_code() -> None:
    result = DockerRunResult(returncode=7, stdout="", stderr="")
    assert format_docker_error(result) == "exit code 7"


@patch("deepagents_docker._docker.run_docker")
def test_docker_available_true_when_info_succeeds(run_docker: MagicMock) -> None:
    run_docker.return_value = DockerRunResult(returncode=0, stdout="25.0.0\n", stderr="")
    assert docker_available() is True


@patch("deepagents_docker._docker.run_docker")
def test_docker_available_false_when_info_fails(run_docker: MagicMock) -> None:
    run_docker.return_value = DockerRunResult(returncode=1, stdout="", stderr="daemon down")
    assert docker_available() is False


@patch("deepagents_docker._docker.subprocess.run")
def test_run_docker_returns_captured_output(subprocess_run: MagicMock) -> None:
    completed = MagicMock()
    completed.returncode = 0
    completed.stdout = "ok\n"
    completed.stderr = ""
    subprocess_run.return_value = completed

    result = run_docker(["info"])

    assert result == DockerRunResult(returncode=0, stdout="ok\n", stderr="")
    subprocess_run.assert_called_once()


@patch("deepagents_docker._docker.subprocess.run")
def test_run_docker_raises_on_timeout(subprocess_run: MagicMock) -> None:
    subprocess_run.side_effect = subprocess.TimeoutExpired(cmd="docker", timeout=5)

    with pytest.raises(DockerError, match="timed out after 5 seconds"):
        run_docker(["exec", "cid", "true"], timeout=5)


@patch("deepagents_docker._docker.subprocess.run")
def test_run_docker_raises_when_docker_missing(subprocess_run: MagicMock) -> None:
    subprocess_run.side_effect = FileNotFoundError

    with pytest.raises(DockerError, match="not found on PATH"):
        run_docker(["info"])


def _fake_docker_script(body: str) -> list[str]:
    """Build args that make the patched `docker` binary run a python snippet."""
    return ["-c", body]


@patch("deepagents_docker._docker.subprocess.Popen")
def test_run_docker_capped_raises_when_docker_missing(popen: MagicMock) -> None:
    popen.side_effect = FileNotFoundError

    with pytest.raises(DockerError, match="not found on PATH"):
        run_docker(["info"], max_output_bytes=1024)


def test_run_docker_capped_returns_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "deepagents_docker._docker.subprocess.Popen",
        _popen_shim(["sh", "-c", "printf hello; printf oops >&2"]),
    )

    result = run_docker(["exec", "cid", "true"], max_output_bytes=1024)

    assert result.returncode == 0
    assert result.stdout == "hello"
    assert result.stderr == "oops"
    assert result.truncated is False


def test_run_docker_capped_kills_process_on_unbounded_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `yes` writes forever; the cap must stop us long before the host buffers it all.
    monkeypatch.setattr(
        "deepagents_docker._docker.subprocess.Popen",
        _popen_shim(["sh", "-c", "yes aaaaaaaaaaaaaaaa"]),
    )

    result = run_docker(["exec", "cid", "true"], timeout=30, max_output_bytes=4096)

    assert result.truncated is True
    assert len(result.stdout.encode()) == 4096
    assert result.returncode != 0


def test_run_docker_capped_forwards_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "deepagents_docker._docker.subprocess.Popen",
        _popen_shim(["sh", "-c", "cat"]),
    )

    result = run_docker(["exec", "-i", "cid", "cat"], input_text="ping", max_output_bytes=1024)

    assert result.stdout == "ping"


def test_run_docker_capped_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "deepagents_docker._docker.subprocess.Popen",
        _popen_shim(["sh", "-c", "sleep 30"]),
    )

    with pytest.raises(DockerError, match="timed out after 1 seconds"):
        run_docker(["exec", "cid", "sleep"], timeout=1, max_output_bytes=1024)


def _popen_shim(replacement: list[str]):
    """Replace the `docker ...` argv with a real local command, keeping Popen behavior."""
    real_popen = subprocess.Popen

    def _factory(_args: list[str], **kwargs: object) -> subprocess.Popen:
        return real_popen(replacement, **kwargs)

    return _factory
