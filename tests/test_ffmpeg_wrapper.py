from app.ffmpeg_wrapper import REAL_FFMPEG, build_ffmpeg_argv


def test_temp_m4a_uses_mp4_muxer():
    assert build_ffmpeg_argv(["-i", "input.m4a", "output.temp.m4a"]) == [
        REAL_FFMPEG,
        "-i",
        "input.m4a",
        "-f",
        "mp4",
        "output.temp.m4a",
    ]


def test_other_outputs_are_unchanged():
    assert build_ffmpeg_argv(["-i", "input.webm", "output.temp.webm"]) == [
        REAL_FFMPEG,
        "-i",
        "input.webm",
        "output.temp.webm",
    ]
