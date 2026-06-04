"""

This example agent finds the best neapolitan pizza recipe on the web and writes it to a file.

"""

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

if __name__ == "__main__":
    for step in agent.stream(
        {"messages": "Find the best neapolitan pizza recipe and write it to the recipe.md file."},
        stream_mode="updates",
    ):
        for update in step.values():
            if update and (messages := update.get("messages")):
                for message in messages:
                    message.pretty_print()
