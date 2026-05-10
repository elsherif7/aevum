# Security Policy

## Supported Versions

Only the latest release receives security fixes.

| Version | Supported |
|---------|-----------|
| 2.2.x   | Yes       |
| < 2.2   | No        |

---

## Reporting a Vulnerability

If you discover a security vulnerability in Aevum, **please do not open a
public GitHub issue**. Public disclosure before a fix is available puts all
users at risk.

Instead, report it privately:

**Email:** create a GitHub private security advisory at  
`https://github.com/elsherif7/aevum/security/advisories/new`

Or email directly if you prefer not to use GitHub.

Please include:
- A clear description of the vulnerability
- Steps to reproduce it
- The potential impact
- Any suggested fix if you have one

You will receive a response within **72 hours**. If the issue is confirmed,
a fix will be released as quickly as possible and you will be credited in the
changelog unless you prefer to remain anonymous.

---

## Security Design

Aevum is a local CLI tool. Its attack surface is intentionally narrow:

### What Aevum does
- Reads files from paths you provide
- Calls `ffprobe` (from FFmpeg) as a subprocess
- Makes HTTPS requests to the YouTube Data API v3 (only when scanning YouTube URLs)
- Reads and writes files in your platform's app data directory

### What Aevum never does
- No telemetry — nothing is sent anywhere except the YouTube API when you
  explicitly scan a YouTube URL
- No network requests on local folder scans
- No code execution from scanned files
- No elevated privileges

### Key security properties

**Subprocess safety**  
All `ffprobe` calls use list form (`subprocess.run(["ffprobe", ...], shell=False)`).
Shell injection is not possible regardless of the file path.

**Path validation**  
Export output paths are validated before writing. System directories
(`C:\Windows`, `/etc`, `/usr`, etc.) are blocked. Only `.txt`, `.csv`,
`.json`, and `.html` extensions are accepted.

**Atomic writes**  
All persistent state (cache, config, quota tracker, history, rate limiter)
is written atomically via temp file + `os.replace()`. A crash mid-write
cannot corrupt your data.

**API key storage**  
YouTube API keys are stored using the OS keyring (Windows Credential Manager,
macOS Keychain, Linux Secret Service) when available. Falls back to a
randomly-keyed Fernet-encrypted file, then plaintext with a visible warning.
Keys are never logged or included in error messages.

**Input validation**  
- Cache files are validated on load — malformed entries are skipped silently
- Config values are type-checked and range-validated on load
- YouTube API responses are validated before use
- Duration values are clamped to prevent integer overflow
- Quota tracker values are clamped — negative values cannot bypass the daily
  quota guard

**Symlink safety**  
Directory traversal uses inode tracking to detect and break symlink loops.
Maximum recursion depth is capped at 30 levels.

**CSV injection prevention**  
CSV exports escape formula-injection characters (`=`, `+`, `-`, `@`, `|`,
tab) that could execute in Excel or Google Sheets.

**HTML safety**  
HTML exports use `html.escape()` on all user-controlled content (file names,
folder names, paths).

**Rate limiting**  
YouTube API requests are rate-limited to 100 per hour via a persistent token
bucket enforced across process invocations. Playlist pagination is capped at
100,000 videos.

**Environment variable validation**  
`LOCALAPPDATA` and `XDG_DATA_HOME` are validated as absolute, non-UNC paths
before use. A malicious environment variable cannot redirect Aevum's data
directory to a system path.

---

## Known Limitations

**YouTube API key in URL query string**  
The YouTube Data API v3 requires the API key as a URL query parameter. This
means the key appears in server logs, proxy logs, and network monitoring tools.
This is a design limitation of the YouTube Data API v3, not a bug in Aevum.
For high-security environments, consider using a YouTube API key with
IP-based restrictions in the Google Cloud Console.

**`ffprobe` is a trusted binary**  
Aevum passes file paths to `ffprobe` for duration probing. If `ffprobe` itself
has a vulnerability that can be triggered by a malformed media file, Aevum
inherits that risk. Keep FFmpeg up to date.

**Plaintext API key fallback**  
If neither `keyring` nor `cryptography` is installed, the YouTube API key is
stored in plaintext with `0o600` permissions. Install the `secure` extras to
avoid this:

```bash
pip install "aevum[secure]"
```
