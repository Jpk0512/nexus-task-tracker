from __future__ import annotations

import argparse
import asyncio
import logging

from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(prog="prism")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("mcp")
    sub.add_parser("status")

    args = parser.parse_args()

    if args.cmd == "mcp":
        from prism.synthesis.mcp_server import start_mcp

        asyncio.run(start_mcp())
    elif args.cmd == "status":
        from prism.config import Config
        from prism.genome import BugGenome

        genome = BugGenome(Config.from_env().genome_path)
        for name in genome.COLLECTIONS:
            info = genome.client.get_collection(name)
            print(f"{name}: {info.points_count} points")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
