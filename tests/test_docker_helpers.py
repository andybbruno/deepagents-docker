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
