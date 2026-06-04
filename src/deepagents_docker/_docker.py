import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

from .errors import DockerError


@dataclass(frozen=True)
class DockerRunResult:
    """Captured output from a Docker CLI subprocess."""

    returncode: int
    stdout: str
    stderr: str


def run_docker(
    args: Sequence[str],
    *,
    timeout: float | None = None,
    input_text: str | None = None,
) -> DockerRunResult:
    """Run `docker` with the given arguments."""
    try:
        completed = subprocess.run(  # noqa: S603
            ["docker", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_text,
        )
    except subprocess.TimeoutExpired as exc:
        msg = f"docker command timed out after {timeout} seconds"
        raise DockerError(msg) from exc
    except FileNotFoundError as exc:
        msg = "docker executable not found on PATH"
        raise DockerError(msg) from exc

    return DockerRunResult(
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def docker_available() -> bool:
    """Return True when the Docker daemon responds to `docker info`."""
    result = run_docker(["info", "--format", "{{.ServerVersion}}"])
    return result.returncode == 0


def format_docker_error(result: DockerRunResult) -> str:
    """Combine stderr/stdout into a single error string."""
    detail = (result.stderr or result.stdout).strip()
    if not detail:
        detail = f"exit code {result.returncode}"
    try:
        payload = json.loads(detail)
    except json.JSONDecodeError:
        return detail
    if isinstance(payload, dict) and "message" in payload:
        return str(payload["message"])
    return detail
