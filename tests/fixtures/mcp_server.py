"""A tiny MCP server for the tests: run over stdio by tests/test_mcp.py."""

from mcp.server.mcpserver import MCPServer

server = MCPServer("fixture", instructions="Call add for sums.")


@server.tool()
def add(a: int, b: int) -> str:
    """Add two numbers."""
    return str(a + b)


@server.tool()
def fail() -> str:
    """Always fails."""
    raise ValueError("boom")


if __name__ == "__main__":
    server.run()
