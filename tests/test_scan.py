"""Local scanning: duration parsing, formatting, and the folder tree."""
from __future__ import annotations

import inspect
import os
import struct
import subprocess
from pathlib import Path

import pytest
from conftest import CLIP_SECONDS, needs_ffmpeg

from aevum_pkg import _scan
from aevum_pkg._display import print_tree
from aevum_pkg._scan import (
    _MKV_EXTENSIONS,
    _MP4_EXTENSIONS,
    _VIDEO_EXT_SET,
    MAX_DEPTH,
    PROBE_TIMEOUT,
    _looks_like_text,
    _read_mkv_duration,
    _read_mp4_duration,
    format_duration,
    format_size,
    get_duration,
    scan_parallel,
    video_extensions,
)

TOLERANCE = 0.1  # seconds; container rounding differs a little per format


# warnings

def test_ffprobe_timeout_warning_is_sanitised(tmp_path, monkeypatch, capsys):
    import subprocess

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=15)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert get_duration(tmp_path / "evil\x1b[2J\nname.avi") == 0.0
    err = capsys.readouterr().err
    assert err == f"  [WARN] ffprobe timed out on: {tmp_path / 'evil'} name.avi\n".replace(
        f"{tmp_path / 'evil'} name", f"{tmp_path}/evil name")


# formatting

def test_format_duration_breakdown():
    fmt = format_duration(90061)  # 1d 1h 1m 1s
    assert fmt["days_fmt"] == "1d 01h 01m 01s"
    assert fmt["hours_fmt"] == "25h 01m 01s"
    assert fmt["minutes_fmt"] == "1501m 01s"


def test_format_duration_clamps_negative_to_zero():
    assert format_duration(-5)["hours_fmt"] == "00h 00m 00s"


@pytest.mark.parametrize("size, expected", [
    (0, "0 B"),
    (1023, "1023 B"),
    (1024, "1.0 KB"),
    (1_048_576, "1.0 MB"),
    (1_073_741_824, "1.00 GB"),
])
def test_format_size(size, expected):
    assert format_size(size) == expected


_APPROVED_EXTENSIONS = frozenset(f".{e}" for e in """
    mp4 mkv avi mov webm flv wmv m4v mpg mpeg 3gp ts m2ts mts vob ogv asf
    3g2 f4v divx rmvb rm m2v m1v mpe m2p m2t m4p mxf qt dv dvr-ms wtv ogm ogx h264 h265 hevc mk3d
    mp3 aac flac wav ogg wma m4a m4b opus aiff aif ac3 mka amr
    aifc ape wv tta mp2 mpa au caf ra oga spx mpc dsf dff aax aa dts dtshd eac3 truehd thd awb w64 rf64 bwf
""".split())


def test_extension_list_is_the_approved_set():
    assert len(video_extensions) == len(set(video_extensions)) == 78
    assert _VIDEO_EXT_SET == _APPROVED_EXTENSIONS


def test_extension_set_has_common_formats_and_excludes_disc_images():
    for ext in (".mp4", ".mkv", ".webm", ".mp3", ".flac", ".m4b"):
        assert ext in _VIDEO_EXT_SET
    assert ".iso" not in _VIDEO_EXT_SET


@pytest.mark.parametrize("ext", [
    ".webp", ".avif", ".mng", ".sol", ".str", ".txt", ".jpg",
    # files that share an extension with non-media files
    ".ifo", ".bdmv", ".mpl", ".mid", ".midi", ".ram", ".sln", ".ace", ".avs", ".drc", ".mod", ".scm",
    # raw, ringtone and game formats
    ".pcm", ".yuv", ".rtttl", ".bik", ".smk", ".f4a", ".mp4v",
])
def test_extension_set_excludes_non_media_raw_and_obscure_formats(ext):
    assert ext not in _VIDEO_EXT_SET


def test_removed_extensions_are_never_probed(tmp_path, monkeypatch):
    for name in ("VIDEO_TS.IFO", "index.bdmv", "song.mid", "link.ram", "app.sln", "x.avs", "t.mod"):
        (tmp_path / name).write_bytes(b"x")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))
    stats: dict[str, int] = {}
    _, count, *_ = scan_parallel(tmp_path, stats=stats)
    assert count == 0
    assert calls == []
    assert stats["unreadable_files"] == 0


# get_duration (native parser first, ffprobe fallback)

@needs_ffmpeg
@pytest.mark.parametrize("name", ["clip.mp4", "clip.mkv", "clip.webm", "clip.mp3"])
def test_get_duration_matches_real_length(clips, name):
    assert get_duration(clips[name]) == pytest.approx(CLIP_SECONDS, abs=TOLERANCE)


@needs_ffmpeg
def test_get_duration_returns_zero_for_garbage(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"this is not a media file" * 100)
    assert get_duration(bad) == 0.0


@needs_ffmpeg
def test_get_duration_returns_zero_for_empty_file(tmp_path):
    empty = tmp_path / "empty.mkv"
    empty.write_bytes(b"")
    assert get_duration(empty) == 0.0


# native MP4 parser

def _box(name: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), name) + payload


def _synthetic_mp4(path: Path, mvhd_body: bytes) -> Path:
    path.write_bytes(
        _box(b"ftyp", b"isom\0\0\0\0isom") + _box(b"moov", _box(b"mvhd", mvhd_body))
    )
    return path


@needs_ffmpeg
def test_native_mp4_matches_ffprobe(clips):
    assert _read_mp4_duration(clips["clip.mp4"]) == pytest.approx(CLIP_SECONDS, abs=TOLERANCE)


def test_native_mp4_version0_header(tmp_path):
    # version 0: 1000 ticks/s, 90000 ticks -> 90 s
    body = bytes([0, 0, 0, 0]) + struct.pack(">IIII", 0, 0, 1000, 90000) + b"\0" * 80
    assert _read_mp4_duration(_synthetic_mp4(tmp_path / "v0.mp4", body)) == 90.0


def test_native_mp4_version1_header(tmp_path):
    # version 1 (64-bit times) with realistic non-zero creation/modification times
    body = bytes([1, 0, 0, 0]) + struct.pack(">QQIQ", 3_800_000_000, 3_800_000_123, 1000, 90000) + b"\0" * 80
    assert _read_mp4_duration(_synthetic_mp4(tmp_path / "v1.mp4", body)) == 90.0


def test_native_mp4_truncated_file_returns_none(tmp_path):
    f = tmp_path / "trunc.mp4"
    f.write_bytes(b"\x00\x00\x00")
    assert _read_mp4_duration(f) is None


# native MKV parser

@needs_ffmpeg
@pytest.mark.parametrize("name", ["clip.mkv", "clip.webm"])
def test_native_mkv_matches_ffprobe(clips, name):
    assert _read_mkv_duration(clips[name]) == pytest.approx(CLIP_SECONDS, abs=TOLERANCE)


def _mkv_with_info(path: Path, *, void_bytes: int = 0, unknown_size_segment: bool = True) -> Path:
    """Hand-built MKV: EBML header, Segment, optional big Void, Info (5.0 s)."""
    ebml = b"\x1a\x45\xdf\xa3\x80"                                   # empty EBML header
    timescale = b"\x2a\xd7\xb1\x83" + (1_000_000).to_bytes(3, "big")  # 1 ms ticks
    duration = b"\x44\x89\x84" + struct.pack(">f", 5000.0)            # 5000 ticks = 5 s
    info = b"\x15\x49\xa9\x66" + bytes([0x80 | (len(timescale) + len(duration))]) + timescale + duration
    void = b""
    if void_bytes:
        void = b"\xec" + (0x200000 | void_bytes).to_bytes(3, "big") + b"\0" * void_bytes
    body = void + info
    if unknown_size_segment:
        seg_size = b"\x01\xff\xff\xff\xff\xff\xff\xff"          # "unknown size"
    else:
        seg_size = (0x0100000000000000 | len(body)).to_bytes(8, "big")
    path.write_bytes(ebml + b"\x18\x53\x80\x67" + seg_size + body)
    return path


@pytest.mark.parametrize("unknown_size", [True, False])
def test_native_mkv_synthetic_segment(tmp_path, unknown_size):
    f = _mkv_with_info(tmp_path / "s.mkv", unknown_size_segment=unknown_size)
    assert _read_mkv_duration(f) == pytest.approx(5.0)


def test_native_mkv_skips_elements_larger_than_1mb(tmp_path):
    # regression: big elements used to be skipped 64 KB at a time, landing mid-element
    f = _mkv_with_info(tmp_path / "big.mkv", void_bytes=1_500_000)
    assert _read_mkv_duration(f) == pytest.approx(5.0)


def test_native_mkv_garbage_returns_none(tmp_path):
    f = tmp_path / "junk.mkv"
    f.write_bytes(bytes(range(256)) * 64)
    assert _read_mkv_duration(f) is None


# scan_parallel / tree

@needs_ffmpeg
def test_scan_counts_only_readable_media(library):
    total_sec, total_count, tree, durations, sizes = scan_parallel(library)

    # a.mp4, ep1.mkv, ep2.webm, song.mp3 — notes.txt and broken.mp4 are excluded
    assert total_count == 4
    assert total_sec == pytest.approx(4 * CLIP_SECONDS, abs=4 * TOLERANCE)
    assert {p.name for p in durations} == {"a.mp4", "ep1.mkv", "ep2.webm", "song.mp3"}
    assert set(durations) == set(sizes)
    assert all(size > 0 for size in sizes.values())


@needs_ffmpeg
def test_scan_builds_folder_tree(library):
    _, _, tree, _, sizes = scan_parallel(library)

    # root holds a.mp4 directly; empty/ is omitted because it has no media
    assert [p.name for p, _ in tree.direct_files] == ["a.mp4"]
    assert [n.name for n in tree.children] == ["Season1"]
    assert tree.root_bytes == sum(sizes.values())

    season = tree.children[0]
    assert season.total_count == 3
    assert [p.name for p, _ in season.direct_files] == ["ep1.mkv", "ep2.webm"]
    assert [n.name for n in season.children] == ["extras"]
    assert season.children[0].total_count == 1


@needs_ffmpeg
def test_scan_progress_callback_reaches_total(library):
    calls = []
    scan_parallel(library, on_progress=lambda done, total: calls.append((done, total)))
    assert calls, "progress callback was never called"
    done, total = max(calls)
    assert done == total


def test_scan_folder_without_media_returns_zero(tmp_path):
    (tmp_path / "readme.txt").write_text("hi")
    total_sec, total_count, tree, durations, sizes = scan_parallel(tmp_path)
    assert (total_sec, total_count, durations, sizes) == (0.0, 0, {}, {})
    assert tree.children == [] and tree.direct_files == []


def test_scan_missing_folder_returns_zero(tmp_path):
    total_sec, total_count, *_ = scan_parallel(tmp_path / "nope")
    assert (total_sec, total_count) == (0.0, 0)


# Same file reachable by several paths is counted once

def _first_clip(library):
    return sorted(library.rglob("*.mp4"))[0]


@needs_ffmpeg
@pytest.mark.skipif(not hasattr(os, "link"), reason="no hardlinks")
def test_hardlink_is_counted_once(library):
    _, base_count, _, base_durs, _ = scan_parallel(library)
    src = _first_clip(library)
    try:
        os.link(src, src.with_name("hardlinked_copy.mp4"))
    except OSError:
        pytest.skip("filesystem refuses hardlinks")
    stats: dict[str, int] = {}
    total_sec, count, _, durs, _ = scan_parallel(library, stats=stats)
    assert count == base_count
    assert stats["duplicates_skipped"] == 1
    assert total_sec == pytest.approx(sum(base_durs.values()))
    assert src in durs   # alphabetical tie-break keeps the same name each run


@needs_ffmpeg
def test_file_symlink_is_counted_once_and_real_file_wins(library):
    _, base_count, *_ = scan_parallel(library)
    src = _first_clip(library)
    link = src.with_name("0_link_first_alphabetically.mp4")   # sorts before src
    try:
        link.symlink_to(src)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    stats: dict[str, int] = {}
    _, count, _, durs, _ = scan_parallel(library, stats=stats)
    assert count == base_count
    assert stats["duplicates_skipped"] == 1
    assert src in durs and link not in durs     # real file beats a symlink


@needs_ffmpeg
def test_no_duplicates_reports_zero(library):
    stats: dict[str, int] = {}
    scan_parallel(library, stats=stats)
    assert stats["duplicates_skipped"] == 0


# robustness: files that vanish, extensions, timeouts, sniffing, skipped items

def _fake_probe(bad=()):
    def probe(path):
        if Path(path).name in bad:
            raise RuntimeError("parser bug")
        return 5.0, False
    return probe


def test_get_duration_of_missing_mkv_is_zero(tmp_path):
    assert get_duration(tmp_path / "gone.mkv") == 0.0
    assert get_duration(tmp_path / "gone.webm") == 0.0


def test_unexpected_error_in_one_file_does_not_stop_the_scan(tmp_path, monkeypatch):
    for name in ("a.mp3", "bad.mp3", "c.mp3"):
        (tmp_path / name).write_bytes(b"x")
    monkeypatch.setattr(_scan, "_probe_duration", _fake_probe(bad={"bad.mp3"}))
    stats: dict[str, int] = {}
    _, count, _, durs, _ = scan_parallel(tmp_path, stats=stats)
    assert count == 2
    assert {p.name for p in durs} == {"a.mp3", "c.mp3"}
    assert stats["unreadable_files"] == 1
    assert stats["timed_out"] == 0


def test_native_parser_extensions_are_all_scanned():
    assert _MP4_EXTENSIONS <= _VIDEO_EXT_SET
    assert _MKV_EXTENSIONS <= _VIDEO_EXT_SET


@pytest.mark.parametrize("ext, muxer", [(".m4b", "ipod"), (".mk3d", "matroska")])
@needs_ffmpeg
def test_audiobook_and_3d_matroska_extensions_are_scanned(tmp_path, ext, muxer):
    clip = tmp_path / f"book{ext}"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={CLIP_SECONDS}", "-c:a", "aac", "-f", muxer, str(clip)],
        check=True,
    )
    total_sec, count, *_ = scan_parallel(tmp_path)
    assert count == 1
    assert total_sec == pytest.approx(CLIP_SECONDS, abs=TOLERANCE * 3)


def test_library_reports_unreadable_media(library):
    stats: dict[str, int] = {}
    scan_parallel(library, stats=stats)
    assert stats["unreadable_files"] == 1       # broken.mp4
    assert stats["timed_out"] == 0
    assert stats["unreadable_dirs"] == 0
    assert stats["skipped_deep"] == 0


def test_clean_library_reports_nothing_skipped(tmp_path, monkeypatch):
    (tmp_path / "a.mp3").write_bytes(b"x")
    monkeypatch.setattr(_scan, "_probe_duration", _fake_probe())
    stats: dict[str, int] = {}
    scan_parallel(tmp_path, stats=stats)
    assert (stats["unreadable_files"], stats["timed_out"],
            stats["unreadable_dirs"], stats["skipped_deep"]) == (0, 0, 0, 0)


def test_ffprobe_timeout_is_counted_and_uses_the_shared_timeout(tmp_path, monkeypatch):
    (tmp_path / "ok.mp3").write_bytes(b"x")
    (tmp_path / "slow.mp3").write_bytes(b"x")
    timeouts = []

    def fake_run(cmd, **kwargs):
        timeouts.append(kwargs["timeout"])
        if cmd[-1].endswith("slow.mp3"):
            raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=kwargs["timeout"])
        return subprocess.CompletedProcess(cmd, 0, stdout="5.0\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    stats: dict[str, int] = {}
    _, count, *_ = scan_parallel(tmp_path, stats=stats)
    assert count == 1
    assert stats["unreadable_files"] == 1
    assert stats["timed_out"] == 1
    assert set(timeouts) == {PROBE_TIMEOUT} == {30}


@pytest.mark.parametrize("blocked", ["root", "sub"])
def test_unreadable_folder_is_counted(tmp_path, monkeypatch, blocked):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.mp3").write_bytes(b"x")
    (tmp_path / "b.mp3").write_bytes(b"x")
    monkeypatch.setattr(_scan, "_probe_duration", _fake_probe())
    target = str((tmp_path if blocked == "root" else tmp_path / "sub").resolve())
    real_scandir = os.scandir

    def fake_scandir(path="."):
        if os.fspath(path) == target:
            raise PermissionError(13, "denied")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", fake_scandir)
    stats: dict[str, int] = {}
    _, count, *_ = scan_parallel(tmp_path, stats=stats)
    assert stats["unreadable_dirs"] == 1
    assert count == (0 if blocked == "root" else 1)


def test_unreadable_root_is_reported(tmp_path, monkeypatch):
    def fail_stat(self, *args, **kwargs):
        raise PermissionError(13, "denied")

    stats: dict[str, int] = {}
    monkeypatch.setattr(Path, "stat", fail_stat)
    scan_parallel(tmp_path, stats=stats)
    assert stats["unreadable_dirs"] == 1


def _deep_chain(root: Path, levels: int) -> Path:
    folder = root
    for _ in range(levels):
        folder = folder / "d"
    try:
        folder.mkdir(parents=True)
    except OSError:
        pytest.skip("path too long for this filesystem")
    (folder / "x.mp3").write_bytes(b"x")
    return folder


def test_folders_beyond_max_depth_are_counted_as_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(_scan, "_probe_duration", _fake_probe())
    _deep_chain(tmp_path, MAX_DEPTH + 5)
    stats: dict[str, int] = {}
    _, count, *_ = scan_parallel(tmp_path, stats=stats)
    assert count == 0
    assert stats["skipped_deep"] == 1


def test_folders_at_max_depth_are_fully_counted(tmp_path, monkeypatch):
    monkeypatch.setattr(_scan, "_probe_duration", _fake_probe())
    _deep_chain(tmp_path, MAX_DEPTH)
    stats: dict[str, int] = {}
    _, count, tree, _, _ = scan_parallel(tmp_path, stats=stats)
    assert count == 1
    assert stats["skipped_deep"] == 0
    assert tree.root_bytes == 1
    assert tree.children[0].total_count == 1


def test_one_depth_limit_is_shared():
    assert MAX_DEPTH == 100
    assert inspect.signature(print_tree).parameters["max_depth"].default == MAX_DEPTH


# text files with media extensions

@pytest.mark.parametrize("name", ["a.ts", "D.TS"])
def test_text_files_with_ambiguous_extensions_are_not_probed(tmp_path, monkeypatch, name):
    (tmp_path / name).write_text("export const x = 1;\n")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))
    stats: dict[str, int] = {}
    _, count, *_ = scan_parallel(tmp_path, stats=stats)
    assert count == 0
    assert calls == []
    assert stats["unreadable_files"] == 0


def test_looks_like_text(tmp_path):
    cases = {
        "plain": (b"hello\n", True),
        "empty": (b"", True),
        "cut_mid_character": (b"a" + "é".encode() * 3000, True),
        "nul_byte": (b"MOD\x00\x00data", False),
        "invalid_utf8": (b"\xff\xfe abc", False),
    }
    for name, (data, expected) in cases.items():
        f = tmp_path / name
        f.write_bytes(data)
        assert _looks_like_text(f) is expected, name
    assert _looks_like_text(tmp_path / "missing") is False


@needs_ffmpeg
def test_real_mpeg_ts_clip_is_still_counted(tmp_path):
    clip = tmp_path / "clip.ts"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={CLIP_SECONDS}", "-c:a", "aac", "-f", "mpegts", str(clip)],
        check=True,
    )
    (tmp_path / "code.ts").write_text("const a: number = 1;\n")
    stats: dict[str, int] = {}
    total_sec, count, _, durs, _ = scan_parallel(tmp_path, stats=stats)
    assert count == 1
    assert set(p.name for p in durs) == {"clip.ts"}
    assert total_sec == pytest.approx(CLIP_SECONDS, abs=0.5)
    assert stats["unreadable_files"] == 0


# folder symlinks

def _link(target: Path, link: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")


@pytest.mark.parametrize("link_name", ["0_alias", "zz_alias"])
def test_folder_link_inside_root_is_counted_once_under_the_real_path(tmp_path, monkeypatch, link_name):
    monkeypatch.setattr(_scan, "_probe_duration", _fake_probe())
    root = tmp_path / "root"
    (root / "real").mkdir(parents=True)
    (root / "real" / "a.mp3").write_bytes(b"x")
    _link(root / "real", root / link_name)
    _, count, _, durs, _ = scan_parallel(root)
    assert count == 1
    assert list(durs) == [(root / "real" / "a.mp3").resolve()]


def test_folder_link_outside_root_is_followed(tmp_path, monkeypatch):
    monkeypatch.setattr(_scan, "_probe_duration", _fake_probe())
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "b.mp3").write_bytes(b"x")
    (root / "a.mp3").write_bytes(b"x")
    _link(outside, root / "drive")
    _, count, _, durs, _ = scan_parallel(root)
    assert count == 2
    assert {p.name for p in durs} == {"a.mp3", "b.mp3"}
    assert any("drive" in p.parts for p in durs if p.name == "b.mp3")


@pytest.mark.parametrize("target", ["root", "parent"])
def test_folder_link_to_an_ancestor_does_not_loop(tmp_path, monkeypatch, target):
    monkeypatch.setattr(_scan, "_probe_duration", _fake_probe())
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "a.mp3").write_bytes(b"x")
    _link(root if target == "root" else tmp_path, root / "sub" / "up")
    _, count, _, durs, _ = scan_parallel(root)
    assert count == 1
    assert {p.name for p in durs} == {"a.mp3"}


@pytest.mark.skipif(not hasattr(os, "link"), reason="no hardlinks")
def test_real_file_beats_a_file_reached_through_a_folder_link(tmp_path, monkeypatch):
    monkeypatch.setattr(_scan, "_probe_duration", _fake_probe())
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    (root / "real").mkdir(parents=True)
    outside.mkdir()
    (outside / "x.mp3").write_bytes(b"x")
    try:
        os.link(outside / "x.mp3", root / "real" / "x_hard.mp3")
    except OSError:
        pytest.skip("filesystem refuses hardlinks")
    _link(outside, root / "0_drive")          # sorts before "real"
    stats: dict[str, int] = {}
    _, count, _, durs, _ = scan_parallel(root, stats=stats)
    assert count == 1
    assert stats["duplicates_skipped"] == 1
    assert [p.name for p in durs] == ["x_hard.mp3"]


def test_visited_inodes_is_no_longer_a_parameter():
    assert "_visited_inodes" not in inspect.signature(scan_parallel).parameters
