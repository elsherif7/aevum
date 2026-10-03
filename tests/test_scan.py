"""Local scanning: duration parsing, formatting, and the folder tree."""
from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest
from conftest import CLIP_SECONDS, needs_ffmpeg

from aevum_pkg._scan import (
    _VIDEO_EXT_SET,
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


def test_extension_set_has_common_formats_and_excludes_disc_images():
    for ext in (".mp4", ".mkv", ".webm", ".mp3", ".flac"):
        assert ext in _VIDEO_EXT_SET
    assert ".iso" not in _VIDEO_EXT_SET


def test_extension_list_has_no_duplicates():
    assert len(video_extensions) == len(set(video_extensions))


@pytest.mark.parametrize("ext", [".webp", ".avif", ".mng", ".sol", ".str", ".txt", ".jpg"])
def test_extension_set_excludes_images_and_source_code(ext):
    assert ext not in _VIDEO_EXT_SET


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
