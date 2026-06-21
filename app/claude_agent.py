import os
import json
import logging
from anthropic import Anthropic
from dotenv import load_dotenv
import k8s_tools

load_dotenv()
log = logging.getLogger(__name__)

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))

SYSTEM_PROMPT = """You are a read-only Kubernetes operations assistant running inside a Slack bot.
You help engineers understand the state of their cluster by calling tools — you NEVER have
access to mutating operations like delete, scale, or apply. Only informational, read-only tools exist.

When answering:
- Use the tools to fetch real data before answering. Never guess or make up cluster state.
- Summarise technical output in clear, concise plain English suitable for Slack.
- If something looks wrong (crash loops, high restarts, pending pods), point it out proactively.
- Keep responses focused — use bullet points for lists, short paragraphs otherwise.
- If a namespace isn't allowed, tell the user which namespaces you can see.
"""

TOOLS = [
    {
        "name": "list_pods",
        "description": "List pods in a namespace with status, restart count, node, and age.",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "Kubernetes namespace, defaults to 'default'"}
            },
        },
    },
    {
        "name": "get_pod_logs",
        "description": "Get the last N lines of logs for a specific pod.",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "pod_name": {"type": "string"},
                "tail_lines": {"type": "integer", "description": "Number of log lines, default 50"},
            },
            "required": ["namespace", "pod_name"],
        },
    },
    {
        "name": "describe_pod",
        "description": "Get detailed pod status, container states, and recent events — use this to debug why a pod is unhealthy.",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "pod_name": {"type": "string"},
            },
            "required": ["namespace", "pod_name"],
        },
    },
    {
        "name": "list_deployments",
        "description": "List deployments in a namespace with ready/desired replica counts.",
        "input_schema": {
            "type": "object",
            "properties": {"namespace": {"type": "string"}},
        },
    },
    {
        "name": "get_events",
        "description": "Get recent Kubernetes events — useful for spotting scheduling failures, image pull errors, OOM kills.",
        "input_schema": {
            "type": "object",
            "properties": {"namespace": {"type": "string"}},
        },
    },
    {
        "name": "list_namespaces",
        "description": "List the namespaces this bot is permitted to read from.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

TOOL_FUNCTIONS = {
    "list_pods": k8s_tools.list_pods,
    "get_pod_logs": k8s_tools.get_pod_logs,
    "describe_pod": k8s_tools.describe_pod,
    "list_deployments": k8s_tools.list_deployments,
    "get_events": k8s_tools.get_events,
    "list_namespaces": k8s_tools.list_namespaces,
}


def _execute_tool(name: str, tool_input: dict) -> str:
    func = TOOL_FUNCTIONS.get(name)
    if not func:
        return f"Unknown tool: {name}"
    try:
        result = func(**tool_input)
        return json.dumps(result, default=str)
    except PermissionError as e:
        return f"Permission denied: {e}"
    except Exception as e:
        log.error(f"Tool '{name}' failed: {e}")
        return f"Tool error: {e}"


def ask(question: str) -> str:
    """
    Sends the question to Claude with tool definitions, executes any tool calls,
    and returns the final plain-English answer.
    """
    messages = [{"role": "user", "content": question}]

    for _ in range(5):  # allow a few rounds of tool use
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            text_blocks = [b.text for b in response.content if b.type == "text"]
            return "\n".join(text_blocks) if text_blocks else "I couldn't generate a response."

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                log.info(f"Claude called tool '{block.name}' with {block.input}")
                result = _execute_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

        messages.append({"role": "user", "content": tool_results})

    return "I ran out of tool-use attempts trying to answer that — try rephrasing your question."