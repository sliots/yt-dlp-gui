#!/usr/bin/env python3
"""Use the MP4 muxer for yt-dlp's temporary M4A outputs."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence


REAL_FFMPEG = "/usr/bin/ffmpeg"


def build_ffmpeg_argv(args: Sequence[str]) -> list[str]:
    ffmpeg_args = list(args)
    if ffmpeg_args and ffmpeg_args[-1].lower().endswith(".temp.m4a"):
        ffmpeg_args[-1:-1] = ["-f", "mp4"]
    return [REAL_FFMPEG, *ffmpeg_args]


def main() -> None:
    argv = build_ffmpeg_argv(sys.argv[1:])
    os.execv(REAL_FFMPEG, argv)


if __name__ == "__main__":
    main()
