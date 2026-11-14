"""Decode uploaded recordings without requiring an ffmpeg executable.

PyAV wheels include FFmpeg libraries. Inspect the content, not the filename:
Dify currently passes uploaded recordings to model plugins as ``temp.mp3``.
"""

import io
import sys
from array import array

import av

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
SAMPLE_RATE = 16000
MAX_DURATION_SECONDS = 300
MAX_PCM_BYTES = SAMPLE_RATE * 2 * MAX_DURATION_SECONDS


def normalize_audio(data: bytes, filename: str = "") -> bytes:
    """Return mono 16 kHz signed 16-bit little-endian PCM, with bounded output."""
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("Upload a non-empty audio recording of at most 25 MiB.")
    if filename.lower().endswith(".pcm"):
        if len(data) % 2 or len(data) > MAX_PCM_BYTES:
            raise ValueError(
                "Raw PCM must be 16-bit mono at 16 kHz, at most 300 seconds."
            )
        return data

    output = io.BytesIO()

    def append(frame: av.AudioFrame) -> None:
        # AV planes may contain alignment padding; only copy actual mono s16 samples.
        chunk = bytes(frame.planes[0])[: frame.samples * 2]
        if sys.byteorder != "little":
            samples = array("h", chunk)
            samples.byteswap()
            chunk = samples.tobytes()
        if output.tell() + len(chunk) > MAX_PCM_BYTES:
            raise ValueError("Recordings must not exceed 300 seconds.")
        output.write(chunk)

    try:
        with av.open(
            io.BytesIO(data),
            mode="r",
            options={
                "protocol_whitelist": "file,pipe",
                "format_whitelist": "mov,wav,mp3,amr",
            },
        ) as container:
            if not container.streams.audio:
                raise ValueError("The upload contains no audio track.")
            stream = container.streams.audio[0]
            if stream.duration is not None and stream.time_base is not None:
                if float(stream.duration * stream.time_base) > MAX_DURATION_SECONDS:
                    raise ValueError("Recordings must not exceed 300 seconds.")
            resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
            for frame in container.decode(stream):
                for converted in resampler.resample(frame):
                    append(converted)
            for converted in resampler.resample(None):
                append(converted)
    except av.FFmpegError as exc:
        raise ValueError(
            "Unable to decode the recording. Upload a valid M4A, WAV, MP3 or AMR file."
        ) from exc

    if output.tell() == 0:
        raise ValueError("The recording contains no decodable audio.")
    return output.getvalue()
