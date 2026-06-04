"""

In this example, we create an agent that analyzes sales data and writes a report to a markdown file.
Please note that the agent will create a python script to analyze the data, in case of missing packages,
it will automatically install any necessary python packages to make the analysis possible (like pandas, matplotlib, etc.).
Finally, it will run the script to generate the report as well as the images.
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
    system_prompt="""You are a sales analyst assistant.""",
)

if __name__ == "__main__":
    for step in agent.stream(
        {
            "messages": """
            Analyze the "sales.csv" data and write a report (with charts) into a file called "sales_report.md".
            Do not write any temporary files in our "shared" directory: write only the final report and put the images into a "img" directory.
            """
        },
        stream_mode="updates",
    ):
        for update in step.values():
            if update and (messages := update.get("messages")):
                for message in messages:
                    message.pretty_print()
