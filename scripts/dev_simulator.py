"""Compatibility entry point: debugging now lives in the main web sidebar."""

import sys

from dev import main

if __name__ == "__main__":
    print("Use the Debugging button in the main web: http://127.0.0.1:5180", flush=True)
    sys.exit(main())
