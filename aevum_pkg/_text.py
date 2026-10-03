from __future__ import annotations

import re
import sys

# Whole escape sequences: CSI (including private forms like ESC[?25l), OSC (ended by BEL or
# ESC \), charset switches (ESC ( 0), and two-character ones like ESC c (terminal reset).
_ANSI_ESCAPE = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?|[ -/]+[0-~]|[0-~])')
# Line breaks and tabs would split or misalign a printed line, so they become a space.
_WHITESPACE_CTRL = re.compile(r'[\n\r\t  ]')
# C0 controls, DEL and C1 controls. U+009B is CSI on terminals that honour 8-bit controls.
_CTRL_CHARS = re.compile(r'[\x00-\x1f\x7f-\x9f]')
# Bidi embeddings, overrides and isolates (which can make "gpj.exe" display as "exe.jpg"),
# plus the invisible direction marks U+200E, U+200F and U+061C.
_BIDI_CHARS = re.compile('[‎‏؜‪-‮⁦-⁩]')


def _safe(name: object, maxlen: int = 200) -> str:
    """Make an untrusted string (file name, video title, typed path) safe to print."""
    text = str(name)
    text = _ANSI_ESCAPE.sub('', text)
    text = _WHITESPACE_CTRL.sub(' ', text)
    text = _CTRL_CHARS.sub('', text)
    text = _BIDI_CHARS.sub('', text)
    text = text.encode('utf-8', 'replace').decode('utf-8')  # undecodable file name bytes become '?'
    return text[:maxlen]


def warn(message: object) -> None:
    print(f"  [WARN] {_safe(message, 500)}", file=sys.stderr)
