"""
Self-update logic for Aevum: pip install --upgrade with an animated progress bar.
"""
import os
import sys
from pathlib import Path

from ._color  import clr
from ._config import save_config
from ._exit   import EX


def _run_pip_upgrade(src_dir, quiet=False):
    """
    Run pip install --upgrade in a background thread with an animated bar.

    Issue 21 fix: error output from the subprocess is now passed out of the
    thread via a plain list (_err) instead of a function attribute
    (_worker.err), which was unconventional and fragile.
    """
    import subprocess as _sp
    import threading

    pip_cmd = [sys.executable, "-m", "pip", "install", str(src_dir), "--upgrade", "-q"]
    if quiet:
        return _sp.run(pip_cmd, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL).returncode

    _frames = [
        "\u2588\u2588\u2588\u2588\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591",
        "\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591",
        "\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591",
        "\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591",
        "\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2591\u2591\u2591\u2591",
        "\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588",
    ]
    _done = threading.Event()
    _rc   = [0]
    _err  = [""]   # Issue 21: use a list, not a function attribute

    def _worker():
        r      = _sp.run(pip_cmd, stdout=_sp.DEVNULL, stderr=_sp.PIPE)
        _rc[0] = r.returncode
        _err[0] = r.stderr.decode(errors="replace").strip() if r.returncode != 0 else ""
        _done.set()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    fi = 0
    while not _done.wait(timeout=0.2):
        print(f"\r  {clr.C}Installing...{clr.RST}  {clr.Y}{_frames[fi % len(_frames)]}{clr.RST}  ",
              end="", flush=True)
        fi += 1
    t.join()
    if _rc[0] == 0:
        print(f"\r  {clr.G}Done!{clr.RST}          {clr.G}{chr(0x2588) * 24}{clr.RST}  ")
        print(f"\n  {clr.G}[OK]{clr.RST}  Aevum updated successfully.\n")
    else:
        print(f"\r  {clr.R}[FAIL]{clr.RST} pip install failed (exit {_rc[0]}).  ")
        for line in _err[0].splitlines()[-6:]:
            print(f"  {clr.DIM}{line}{clr.RST}")
        print()
    return _rc[0]


def _open_appdata():
    import subprocess as _sp
    from ._paths import APPDATA
    # S-07: resolve and validate the path before passing to shell opener
    appdata = APPDATA.resolve()
    appdata.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        _sp.Popen(["explorer", str(appdata)])
    else:
        _sp.Popen(["xdg-open", str(appdata)])
    return appdata


def _do_update(cfg, dry_run=False, quiet=False):
    """
    Core update flow.  Returns the pip exit code, or 0 on early exit.
    Mutates cfg['project_dir'] if the user enters a new path.

    Issue 18 fix: extracted into a shared function so the headless 'update'
    command uses a single clean implementation.
    """
    def _find_project_dir():
        saved = cfg.get('project_dir', '')
        if saved and (Path(saved) / "pyproject.toml").exists():
            return Path(saved)
        if (Path.cwd() / "pyproject.toml").exists():
            # S-08: verify this is actually the Aevum project before trusting cwd
            try:
                toml_content = (Path.cwd() / "pyproject.toml").read_text(encoding="utf-8")
                if 'name = "aevum"' in toml_content or "name = 'aevum'" in toml_content:
                    return Path.cwd()
            except OSError:
                pass
        return None

    src_dir = _find_project_dir()

    saved = cfg.get('project_dir', '')
    if saved and not (Path(saved) / "pyproject.toml").exists():
        print(f"\n  {clr.Y}[WARN]{clr.RST}  Saved project path no longer exists: {clr.W}{saved}{clr.RST}")
        print(f"  The project folder may have moved or been renamed.")

    if src_dir is None:
        print(f"\n  {clr.Y}Aevum project folder not found.{clr.RST}")
        print(f"  {clr.DIM}Option 1:{clr.RST}  cd to your Aevum folder and run {clr.W}aevum update{clr.RST} from there.")
        print(f"  {clr.DIM}Option 2:{clr.RST}  Paste the path to your Aevum folder below.")
        print()
        try:
            pasted = input(f"  {clr.C}Aevum folder path{clr.RST} (or Enter to cancel)> ").strip().strip("'\"")
        except (KeyboardInterrupt, EOFError):
            print()
            return 0
        if not pasted:
            return 0
        src_dir = Path(pasted).resolve()
        if not (src_dir / "pyproject.toml").exists():
            print(f"\n  {clr.R}[ERROR]{clr.RST} No pyproject.toml found at {src_dir}.\n", file=sys.stderr)
            return EX.ERR_ARGS
        # S-08: verify this is actually the Aevum project, not a malicious package
        try:
            toml_content = (src_dir / "pyproject.toml").read_text(encoding="utf-8")
            if 'name = "aevum"' not in toml_content and "name = 'aevum'" not in toml_content:
                print(f"\n  {clr.R}[ERROR]{clr.RST} This does not appear to be the Aevum project "
                      f"(pyproject.toml does not contain name = \"aevum\").\n", file=sys.stderr)
                return EX.ERR_ARGS
        except OSError as e:
            print(f"\n  {clr.R}[ERROR]{clr.RST} Could not read pyproject.toml: {e}\n", file=sys.stderr)
            return EX.ERR_ARGS
        cfg['project_dir'] = str(src_dir)
        save_config(cfg)
        print(f"  {clr.G}[OK]{clr.RST}  Path saved. You can run {clr.W}aevum update{clr.RST} from anywhere now.\n")

    pip_cmd = [sys.executable, "-m", "pip", "install", str(src_dir), "--upgrade", "-q"]
    if dry_run:
        print(f"  {clr.DIM}Would run:{clr.RST}  {clr.W}{' '.join(pip_cmd)}{clr.RST}")
        return 0
    if not quiet:
        print(f"  {clr.W}Upgrading Aevum from{clr.RST}  {clr.C}{src_dir}{clr.RST}\n")
    return _run_pip_upgrade(src_dir, quiet=quiet)
