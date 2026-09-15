"""Run in your own Terminal to enter an OpenAI key without showing it."""
import getpass
import os
from pathlib import Path
import sys


def save_key(path: Path, key: str) -> None:
    key = key.strip()
    if not key or any(c.isspace() for c in key):
        raise ValueError("The key must be nonempty and contain no whitespace.")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.ftruncate(fd, 0)
        with os.fdopen(fd, "w", encoding="utf-8", closefd=False) as output:
            output.write(key + "\n")
    finally:
        os.close(fd)


def main() -> int:
    if not sys.stdin.isatty():
        print("Run this command in your own interactive Terminal.", file=sys.stderr)
        return 1
    path = Path(__file__).resolve().parent / ".apidocgen" / "openai-key"
    try:
        key = getpass.getpass("OpenAI API key (hidden): ")
        save_key(path, key)
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled; key unchanged.")
        return 1
    except (OSError, ValueError):
        print("Could not save the key. Check the input and folder permissions.", file=sys.stderr)
        return 1
    print("Key saved locally. You can now run Analyze, then Render.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
