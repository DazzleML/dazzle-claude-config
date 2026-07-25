"""Allow running as: python -m dazzle_claude_config

sys.exit(main()) is load-bearing -- without it this entry point always
exits 0 and the A7 exit-code contract (0 clean / 1 drift / 2 error) is
silently lost for anyone scripting the module form in CI.
"""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
