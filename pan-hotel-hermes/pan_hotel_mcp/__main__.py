"""Run MCP server: python -m pan_hotel_mcp"""

from pan_hotel_mcp.server import main

if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
