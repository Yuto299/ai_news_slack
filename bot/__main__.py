"""使い方: python -m bot <チャンネル名> [--dry-run]"""

import argparse
import sys

from bot.channels import CHANNELS
from bot.core import run


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("channel", choices=sorted(CHANNELS))
    parser.add_argument("--dry-run", action="store_true", help="Slack に投稿せず結果を標準出力に表示")
    args = parser.parse_args()
    return run(CHANNELS[args.channel], args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
