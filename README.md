# 07Project
my july projects, a lot of projects right there so be safe!

## remove_videos.py

This script deletes video files whose filenames contain a specified substring.

### Usage

```bash
python remove_videos.py [DIRECTORY] [SUBSTRING]
```

- `DIRECTORY` is the folder to search for videos. If omitted, you will be
  prompted for a directory (press Enter to use the current directory).
- `SUBSTRING` is the text that must appear in the filename. If omitted, you will
  be prompted for it.

Video extensions handled include `.mp4`, `.mkv`, `.avi`, `.mov`, `.wmv`,
`.flv`, and `.webm`.
