"""Module entrypoint for `python -m tmail`."""

import sys

from tmail.cli import main

if __name__ == "__main__":
    sys.exit(main())
