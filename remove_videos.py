import argparse
import os
from pathlib import Path

VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm'}

def remove_videos_with_string(directory: Path, substring: str) -> None:
    """Remove video files in directory containing substring in their name."""
    substring_lower = substring.lower()
    for path in directory.rglob('*'):
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
            if substring_lower in path.stem.lower():
                try:
                    path.unlink()
                    print(f"Removed {path}")
                except Exception as e:
                    print(f"Failed to remove {path}: {e}")

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove videos containing a specific string in their filenames."
    )
    parser.add_argument(
        "directory",
        type=Path,
        nargs="?",
        help="Directory to search for videos (default: current directory)",
    )
    parser.add_argument(
        "substring",
        nargs="?",
        help="Substring to look for in video filenames",
    )

    args = parser.parse_args()

    directory = args.directory
    if directory is None:
        user_input = input(
            "Directory to search for videos (leave blank for current directory): "
        ).strip()
        directory = Path(user_input) if user_input else Path.cwd()

    substring = args.substring
    if substring is None:
        substring = input("Substring to search for in video filenames: ").strip()
        if not substring:
            raise SystemExit("Substring cannot be empty")

    if not directory.is_dir():
        raise SystemExit(f"{directory} is not a valid directory")

    remove_videos_with_string(directory, substring)

if __name__ == '__main__':
    main()
