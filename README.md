# Aevum
**Video Library Duration Scanner**  
Scan any folder on your PC, hard drive, or USB and see the total duration of all your videos — broken down by subfolder, with a grand total at the end.

---

## Requirements

- **Python** — https://python.org
- **FFmpeg** (includes ffprobe) — https://ffmpeg.org/download.html  
  After downloading, add FFmpeg to your system PATH.

---

## Install

1. Put these 3 files in the same folder:
   - `aevum.py`
   - `install.bat`
   - `uninstall.bat`

2. Right-click `install.bat` and run as Administrator  
   *(A UAC prompt will appear — click Yes)*

3. Open any new Command Prompt and type:
   ```
   aevum
   ```

That's it. You can delete the 3 installer files after.

---

## Usage

```
aevum
```

The app starts and waits for a path:

```
  ================================================================
  A E V U M  |  Video Library Duration Scanner
  ================================================================

  aevum> C:\Users\Abdul\Desktop\English
  Scanning...  240 video(s) found
  Done!

  ================================================================
  VIDEO LIBRARY  |  FOLDER SUMMARY
  ================================================================

  English
      +--  55h 24m 17s  |  240 videos

      1.  Advice
          +--  03h 01m 58s  |  12 videos
      ...

  ================================================================
  GRAND TOTAL
  ================================================================
  Total Videos  :  240
  Days          :  2d 07h 24m 17s
  Hours         :  55h 24m 17s
  Minutes       :  3324m 17s
  ================================================================

  [S] Scan another path    [C] Clear screen    [Q] Quit
```

---

## Commands

| Command      | What it does                        |
|-------------|--------------------------------------|
| *(any path)* | Scan that folder                    |
| `clear`      | Clear the screen                    |
| `exit`       | Quit the app                        |
| `Ctrl+C`     | Cancel a scan or quit               |

After every scan a menu also appears with the same options.

---

## Supported paths

Any path Windows can see works:

```
C:\Users\Abdul\Videos
D:\Movies
E:\Series
F:\
```

Drag and drop a folder into the cmd window — it auto-strips the quotes Windows adds.

---

## Supported video formats

`.mp4` `.mkv` `.avi` `.mov` `.webm` `.flv` `.wmv` `.m4v`  
`.mpg` `.mpeg` `.3gp` `.ts` `.vob` `.ogv` `.divx` `.rmvb` `.asf` `.m2ts`

---

## Uninstall

Run `uninstall.bat` — it removes all files and cleans up the PATH entry.
