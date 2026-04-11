# Aevum

**Video library duration scanner for Windows.**  
Point it at any folder and instantly see the total watch time — broken down by subfolder, with a grand total at the end.

---

## Features

- Recursively scans any folder for video files
- Displays duration per subfolder in a tree view
- Grand total in days, hours, and minutes
- Supports external drives, USB sticks, network paths — anything Windows can see
- Drag-and-drop a folder into the terminal window
- Clean, colored terminal UI

---

## Requirements

- **Python** — https://python.org
- **FFmpeg** (includes `ffprobe`) — https://ffmpeg.org/download.html  
  After downloading, make sure FFmpeg is on your system PATH.

---

## Installation

1. Download or clone this repo
2. Right-click `install.bat` → **Run as administrator**
3. Open a new Command Prompt and type:

```
aevum
```

The installer copies `aevum.py` to `%LOCALAPPDATA%\Aevum` and drops a launcher into your Python Scripts folder so `aevum` works from any terminal.

---

## Usage

```
aevum
```

Then enter any folder path at the prompt:

```
  ================================================================
    A E V U M  |  Video Library Duration Scanner
  ================================================================

  aevum> D:\Movies
  Scanning...  312 video(s) found
  Done!

  ================================================================
    VIDEO LIBRARY  |  FOLDER SUMMARY
  ================================================================

  Movies
      +--  438h 12m 05s  |  312 videos

      1.  Action
          +--  82h 44m 11s  |  58 videos

      2.  Drama
          +--  95h 30m 22s  |  71 videos
      ...

  ================================================================
    GRAND TOTAL
  ================================================================
  Total Videos  :  312
  Days          :  18d 06h 12m 05s
  Hours         :  438h 12m 05s
  Minutes       :  26292m 05s
  ================================================================
```

---

## Commands

| Input | Action |
|---|---|
| Any folder path | Scan that folder |
| `clear` | Clear the screen |
| `exit` / `quit` | Quit |
| `Ctrl+C` | Cancel a scan or quit |

After each scan a quick menu also appears with the same options.

---

## Supported Formats

`.mp4` `.mkv` `.avi` `.mov` `.webm` `.flv` `.wmv` `.m4v` `.mpg` `.mpeg` `.3gp` `.ts` `.vob` `.ogv` `.divx` `.rmvb` `.asf` `.m2ts`

---

## Uninstall

Run `uninstall.bat` as administrator. It removes the app folder and launcher — nothing else is touched.

