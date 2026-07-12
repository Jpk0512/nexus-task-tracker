"""Entry point for `python -m broker.daemon <verb> ...` — currently just
`ensure` (N31, plans/14 SS6).
"""
from __future__ import annotations

import sys

from broker.daemon.ensure import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
