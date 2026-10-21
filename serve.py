"""Compatibility entry point for ``python serve.py``."""

import asyncio

from scuthpc_console.serve import main


if __name__ == "__main__":
    asyncio.run(main())
