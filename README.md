<p align="center">
  <img src="https://github.com/andybbruno/deepagents-docker/blob/master/assets/deepagents-docker-banner.png?raw=true"  width="800" />
</p>
<div align="center">
  <h3>Docker sandbox backend for <a href="https://github.com/langchain-ai/deepagents">Deep Agents</a>.</h3>
</div>


<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/downloads/)
[![deepagents](https://img.shields.io/badge/built%20for-deepagents-orange)](https://github.com/langchain-ai/deepagents)

</div>

## deepagents-docker
Run [Deep Agents](https://github.com/langchain-ai/deepagents) in an isolated Docker container without compromising your host machine.

## Quickstart

Requires [Docker](https://docs.docker.com/get-docker/) on your machine.

Install with `uv`:
```bash
uv add deepagents-docker
```
or with `pip`:
```bash
pip install deepagents-docker
```

```python
from deepagents import create_deep_agent
from deepagents_docker import DockerSandbox

agent = create_deep_agent(
    model="openai:gpt-5.5",
    backend=DockerSandbox(),
    system_prompt="You are a research assistant.",
)

result = agent.invoke({"messages": "Research the latest trends in AI and write a summary."})
```

## Configuration

Constructor options let you change the docker image of the container, shared folder path, command timeout, resource limits, outbound network access, and any extra `docker run` flags:

```python
DockerSandbox(
    image="python:3.12-bookworm",      # default image (Debian-based, includes curl, etc.)
    allow_outbound_traffic=True,       # False → no network; True (default) → allow outbound traffic
    shared_dir="/path/to/project",     # host folder shared with the container; see note below
    timeout=120,                       # per-command timeout (seconds)
    max_output_bytes=100_000,          # combined stdout/stderr cap per command
    memory="256m",                     # default memory limit
    cpus=0.5,                          # default CPU limit
    pids_limit=128,
    auto_remove=True,                  # remove container on close()
    extra_run_args=["--env", "FOO=bar"],
)
```

> [!NOTE]
> Pass an explicit `shared_dir` path to keep files after the container stops. When omitted, a temporary directory is created within the host filesystem and **removed when the DockerSandbox is closed**.


## How it works

The creation of a `DockerSandbox` object results in the starting of a long-running docker container. Every shell command executed by the agent is actually run **inside the container**, not on your host OS. Therefore, library installations, cURL downloads, and any other filesystem changes stay inside Docker, not on your host. The only link between the container and your machine is **shared_dir** (if provided), a folder on disk that is mounted at `/shared` (with that directory as the shell working directory) so you can share files between the agent and your host.

> [!NOTE]
> The container is stopped and removed automatically when the Python process exits (`atexit`). Use a context manager (below) to tear down earlier.


### Using a context manager

Use a context manager when you want the container stopped and removed as soon as you leave the block:

```python
from deepagents import create_deep_agent
from deepagents_docker import DockerSandbox

with DockerSandbox() as backend:
    agent = create_deep_agent(model="openai:gpt-5.5", backend=backend)
    agent.invoke({"messages": "..."})

# Container stopped and removed here.
print("Done!")
```

## Examples

Prerequisites:
- An OpenAI API key
- Docker installed and running
- Python 3.12 or higher

Set the OpenAI API key:

```bash
export OPENAI_API_KEY=your_api_key
```

### 1. Pizza agent

The [pizza agent](examples/pizza_agent.py) searches the web for a Neapolitan pizza recipe and writes it to a file in the shared folder:

```python
from deepagents import create_deep_agent
from deepagents_docker import DockerSandbox

backend = DockerSandbox(
    shared_dir="examples/data",
    allow_outbound_traffic=True,
)

agent = create_deep_agent(
    model="openai:gpt-5.5",
    backend=backend,
    system_prompt="You are a pizza chef.",
)

for step in agent.stream(
    {"messages": "Find the best neapolitan pizza recipe and write it to the recipe.md file."},
    stream_mode="updates",
):
    for update in step.values():
        if update and (messages := update.get("messages")):
            for message in messages:
                message.pretty_print()
```

The agent writes `recipe.md` under `examples/data/`.

### 2. Sales analyst

The [sales analyst](examples/sales_analyst.py) reads `sales.csv` from the shared folder, installs Python packages inside the container as needed (for example `pandas`, `matplotlib`), runs an analysis script, and writes a markdown report with charts:

```python
from deepagents import create_deep_agent
from deepagents_docker import DockerSandbox

backend = DockerSandbox(
    shared_dir="examples/data",
    allow_outbound_traffic=True,
)

agent = create_deep_agent(
    model="openai:gpt-5.5",
    backend=backend,
    system_prompt="You are a sales analyst assistant.",
)

for step in agent.stream(
    {
        "messages": (
            'Analyze the "sales.csv" data and write a report (with charts) into '
            'a file called "sales_report.md". Put images in an "img" directory.'
        )
    },
    stream_mode="updates",
):
    for update in step.values():
        if update and (messages := update.get("messages")):
            for message in messages:
                message.pretty_print()
```

The agent writes `sales_report.md` and chart images under `examples/data/img/`.

## Development

```bash
git clone https://github.com/andybbruno/deepagents-docker.git
cd deepagents-docker
uv sync
uv run pytest
```

## Contributing

Contributions are welcome! Please feel free to open an issue or submit a pull request.

## Security

Use this for trusted workloads and development, not as a hard multi-tenant boundary. Do not put secrets in the shared folder. See [Deep Agents security](https://github.com/langchain-ai/deepagents?tab=security-ov-file).

## License

MIT — [LICENSE](LICENSE).
