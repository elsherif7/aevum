import os

# Enable ANSI colors on Windows
if os.name == 'nt':
    import ctypes
    kernel32 = ctypes.windll.kernel32
    _handle = kernel32.GetStdHandle(-11)
    if _handle and _handle != -1:
        kernel32.SetConsoleMode(_handle, 7)

R   = "\033[91m"
G   = "\033[92m"
Y   = "\033[93m"
B   = "\033[94m"
M   = "\033[95m"
C   = "\033[96m"
W   = "\033[97m"
DIM = "\033[2m"
RST = "\033[0m"

LINE = "=" * 64

def clear():
    print('\033[2J\033[H', end='', flush=True)

def _disable_color():
    global R, G, Y, B, M, C, W, DIM, RST
    R = G = Y = B = M = C = W = DIM = RST = ""
