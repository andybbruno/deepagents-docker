"""Unit tests for DockerSandbox (Docker CLI mocked)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from deepagents_docker import DockerSandbox
from deepagents_docker._docker import DockerError, DockerRunResult
from deepagents_docker.backend import DEFAULT_IMAGE


def _docker_info_ok() -> DockerRunResult:
    return DockerRunResult(returncode=0, stdout="25.0.0\n", stderr="")


def _docker_run_ok() -> DockerRunResult:
    return DockerRunResult(returncode=0, stdout="", stderr="")


def _docker_inspect_ok() -> DockerRunResult:
    return DockerRunResult(returncode=0, stdout="abc123container\n", stderr="")


@patch("deepagents_docker.backend.run_docker")
@patch("deepagents_docker.backend.docker_available", return_value=True)
@patch("deepagents_docker.backend.inspect_container_id", return_value="abc123container")
def test_default_image_is_python_bookworm(
    _inspect: MagicMock,
    _available: MagicMock,
    run_docker: MagicMock,
    tmp_path: Path,
) -> None:
    run_docker.return_value = _docker_run_ok()

    sandbox = DockerSandbox(workspace_dir=tmp_path)
    try:
        run_args = run_docker.call_args_list[0][0][0]
        assert DEFAULT_IMAGE in run_args
        assert run_args[
            run_args.index(DEFAULT_IMAGE) + 1 : run_args.index(DEFAULT_IMAGE) + 3
        ] == [
            "sleep",
            "infinity",
        ]
    finally:
        sandbox.close()


@patch("deepagents_docker.backend.run_docker")
@patch("deepagents_docker.backend.docker_available", return_value=True)
@patch("deepagents_docker.backend.inspect_container_id", return_value="abc123container")
def test_start_container_applies_security_flags(
    _inspect: MagicMock,
    _available: MagicMock,
    run_docker: MagicMock,
    tmp_path: Path,
) -> None:
    run_docker.return_value = _docker_run_ok()

    sandbox = DockerSandbox(workspace_dir=tmp_path, image="test-image:local")
    try:
        run_args = run_docker.call_args_list[0][0][0]
        assert "run" in run_args
        assert "--network" in run_args and "none" in run_args
        assert "--cap-drop" in run_args and "ALL" in run_args
        assert "--read-only" in run_args
        assert f"{tmp_path.resolve()}:/workspace:rw" in " ".join(run_args)
        assert sandbox.id == "abc123container"
    finally:
        sandbox.close()


@patch("deepagents_docker.backend.subprocess.run")
@patch("deepagents_docker.backend.run_docker")
@patch("deepagents_docker.backend.docker_available", return_value=True)
@patch("deepagents_docker.backend.inspect_container_id", return_value="cid")
def test_execute_wraps_command_and_returns_output(
    _inspect: MagicMock,
    _available: MagicMock,
    run_docker: MagicMock,
    subprocess_run: MagicMock,
    tmp_path: Path,
) -> None:
    run_docker.return_value = _docker_run_ok()
    completed = MagicMock()
    completed.stdout = "hello\n"
    completed.stderr = ""
    completed.returncode = 0
    subprocess_run.return_value = completed

    sandbox = DockerSandbox(workspace_dir=tmp_path, image="test-image:local")
    try:
        result = sandbox.execute("echo hello")
        assert result.exit_code == 0
        assert "hello" in result.output

        docker_cmd = subprocess_run.call_args[0][0]
        assert docker_cmd[:2] == ["docker", "exec"]
        shell_cmd = docker_cmd[-1]
        assert shell_cmd.startswith("cd /workspace && ")
        assert "echo hello" in shell_cmd
    finally:
        sandbox.close()


@patch("deepagents_docker.backend.run_docker")
@patch("deepagents_docker.backend.docker_available", return_value=True)
@patch("deepagents_docker.backend.inspect_container_id", return_value="cid")
def test_write_and_read_via_virtual_paths(
    _inspect: MagicMock,
    _available: MagicMock,
    run_docker: MagicMock,
    tmp_path: Path,
) -> None:
    run_docker.return_value = _docker_run_ok()

    sandbox = DockerSandbox(workspace_dir=tmp_path, image="test-image:local")
    try:
        write_result = sandbox.write("/notes.txt", "alpha\n")
        assert write_result.error is None
        assert (tmp_path / "notes.txt").read_text() == "alpha\n"

        read_result = sandbox.read("/notes.txt")
        assert read_result.error is None
        assert read_result.file_data is not None
        assert "alpha" in read_result.file_data["content"]
    finally:
        sandbox.close()


@patch("deepagents_docker.backend.docker_available", return_value=False)
def test_raises_when_docker_unavailable(_available: MagicMock) -> None:
    with pytest.raises(DockerError, match="Docker is not available"):
        DockerSandbox(image="missing:latest")


@patch("deepagents_docker.backend.subprocess.run")
@patch("deepagents_docker.backend.run_docker")
@patch("deepagents_docker.backend.docker_available", return_value=True)
@patch("deepagents_docker.backend.inspect_container_id", return_value="cid")
def test_execute_timeout(
    _inspect: MagicMock,
    _available: MagicMock,
    run_docker: MagicMock,
    subprocess_run: MagicMock,
    tmp_path: Path,
) -> None:
    run_docker.return_value = _docker_run_ok()
    subprocess_run.side_effect = subprocess.TimeoutExpired(cmd="docker", timeout=1)

    sandbox = DockerSandbox(workspace_dir=tmp_path, image="test-image:local", timeout=1)
    try:
        result = sandbox.execute("sleep 10", timeout=1)
        assert result.exit_code == 124
        assert "timed out" in result.output.lower()
    finally:
        sandbox.close()
