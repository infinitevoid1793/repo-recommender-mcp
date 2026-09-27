"""The MCPServer instance.

It lives in its own module so tool modules can register against it without
importing the server entry point, which would be a circular import.
"""

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("repo-recommender")
