import gzip
import io
import json
import math
import wave
from array import array
from pathlib import Path
from unittest.mock import patch

import pytest
from dify_plugin.entities.model import ModelPropertyKey
from dify_plugin.errors.model import InvokeBadRequestError

from models.speech2text import audio as audio_module
from models.speech2text.audio import normalize_audio
from models.speech2text.speech2text import VolcengineOpenAISpeech2TextModel

M4A = Path(__file__).parent / "fixtures" / "stereo-48k.m4a"


@pytest.fixture
def model():
    return object.__new__(VolcengineOpenAISpeech2TextModel)


def response(payload=None, *, final=False, error=None):
    data = gzip.compress(json.dumps(payload or {}).encode())
    if error is not None:
        return (
            b"\x11\xf0\x11\x00"
            + error.to_bytes(4, "big")
            + len(data).to_bytes(4, "big")
            + data
        )
    return (
        bytes([0x11, 0x93 if final else 0x91, 0x11, 0])
        + (-1 if final else 1).to_bytes(4, "big", signed=True)
        + len(data).to_bytes(4, "big")
        + data
    )


class SpeechSocket:
    """A provider that acknowledges input but sends the final transcript separately."""

    def __init__(self):
        self.sent = []
        self.responses = []
        self.closed = False

    def settimeout(self, timeout):
        assert 0 < timeout <= 30

    def send_binary(self, packet):
        self.sent.append(packet)
        self.responses.append(response({"result": {"text": "中间结果"}}))
        if packet[1] & 2:
            self.responses.append(
                response({"result": {"text": "最终识别结果"}}, final=True)
            )

    def recv(self):
        return self.responses.pop(0)

    def close(self):
        self.closed = True


def test_real_stereo_m4a_is_decoded_even_with_dify_mp3_filename():
    pcm = normalize_audio(M4A.read_bytes(), "temp.mp3")
    # AAC can add a partial frame of padding; expected duration is approximately 0.4s.
    assert 12000 <= len(pcm) <= 14000
    samples = array("h", pcm)
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    assert 500 < rms < 5000


def test_wav_is_resampled_to_mono_16k():
    data = io.BytesIO()
    with wave.open(data, "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(48000)
        output.writeframes(b"\x01\x00\x02\x00" * 4800)
    assert len(normalize_audio(data.getvalue(), "temp.mp3")) == 3200


def test_explicit_raw_pcm_is_preserved():
    pcm = b"\x01\x00" * 1600
    assert normalize_audio(pcm, "recording.pcm") == pcm


@pytest.mark.parametrize("data", [b"", b"not audio", b"\x00\x00\x00\x20ftypM4A "])
def test_invalid_audio_is_rejected(data):
    with pytest.raises(ValueError):
        normalize_audio(data, "temp.mp3")


def test_decoded_size_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(audio_module, "MAX_PCM_BYTES", 100)
    with pytest.raises(ValueError, match="300 seconds"):
        normalize_audio(M4A.read_bytes())


def test_upload_size_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(audio_module, "MAX_UPLOAD_BYTES", 10)
    with pytest.raises(ValueError, match="25 MiB"):
        normalize_audio(M4A.read_bytes())


@pytest.mark.parametrize(
    "credentials,auth_header",
    [
        ({"api_key": "secret"}, "X-Api-Key"),
        (
            {"auth_method": "legacy", "app_key": "app", "access_key": "secret"},
            "X-Api-Access-Key",
        ),
    ],
)
def test_m4a_round_trip_waits_for_final_result_and_closes_socket(
    model, credentials, auth_header
):
    socket = SpeechSocket()
    file = io.BytesIO(M4A.read_bytes())
    file.name = "temp.mp3"
    with patch(
        "models.speech2text.speech2text.websocket.create_connection",
        return_value=socket,
    ) as connect:
        assert model._invoke("doubao-seed-asr", credentials, file) == "最终识别结果"
    assert socket.closed
    assert (
        connect.call_args.args[0]
        == "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream"
    )
    assert connect.call_args.kwargs["header"][auth_header] == "secret"
    assert (
        "sslopt" not in connect.call_args.kwargs
    )  # TLS verification is never disabled.
    params = json.loads(gzip.decompress(socket.sent[0][12:]))
    assert params["audio"] == {
        "format": "pcm",
        "rate": 16000,
        "bits": 16,
        "channel": 1,
        "codec": "raw",
    }
    assert params["request"]["result_type"] == "full"
    chunks = socket.sent[1:]
    assert all(len(gzip.decompress(chunk[12:])) <= 6400 for chunk in chunks)
    assert b"".join(gzip.decompress(chunk[12:]) for chunk in chunks) == normalize_audio(
        M4A.read_bytes()
    )
    assert [int.from_bytes(chunk[4:8], "big", signed=True) for chunk in chunks] == [
        *range(2, len(chunks) + 1),
        -(len(chunks) + 1),
    ]
    assert all(chunk[2] >> 4 == 0 for chunk in chunks)
    assert not socket.responses


def test_corrupt_m4a_fails_before_provider_call(model):
    with patch("models.speech2text.speech2text.websocket.create_connection") as connect:
        with pytest.raises(InvokeBadRequestError, match="decode"):
            model._invoke("asr", {"api_key": "secret"}, io.BytesIO(b"corrupted"))
        connect.assert_not_called()


@pytest.mark.parametrize(
    "credentials", [{}, {"auth_method": "legacy", "app_key": "app"}]
)
def test_missing_credentials_fail_before_connection(model, credentials):
    with patch("models.speech2text.speech2text.websocket.create_connection") as connect:
        with pytest.raises(InvokeBadRequestError, match="Configure"):
            model._invoke("asr", credentials, io.BytesIO(M4A.read_bytes()))
        connect.assert_not_called()


@pytest.mark.parametrize(
    "wire", [b"", b"invalid", response({"message": "secret"}, error=45000001)]
)
def test_provider_failures_close_connection_without_exposing_details(model, wire):
    socket = SpeechSocket()
    socket.recv = lambda: wire
    with patch(
        "models.speech2text.speech2text.websocket.create_connection",
        return_value=socket,
    ):
        with pytest.raises(InvokeBadRequestError) as error:
            model._invoke("asr", {"api_key": "secret"}, io.BytesIO(M4A.read_bytes()))
    assert socket.closed
    assert "secret" not in str(error.value)


def test_disconnect_after_last_input_does_not_return_partial_transcript(model):
    socket = SpeechSocket()
    original_send = socket.send_binary

    def send(packet):
        original_send(packet)
        if packet[1] & 2:
            socket.responses[-1] = b""

    socket.send_binary = send
    with patch(
        "models.speech2text.speech2text.websocket.create_connection",
        return_value=socket,
    ):
        with pytest.raises(InvokeBadRequestError, match="final result"):
            model._invoke("asr", {"api_key": "secret"}, io.BytesIO(M4A.read_bytes()))
    assert socket.closed


@pytest.mark.parametrize("wire", [b"", b"\x11", response()[:-1], b"\x10\x81\x11\x00"])
def test_truncated_protocol_frame_is_rejected(model, wire):
    with pytest.raises(ValueError):
        model._parse_response(wire)


def test_model_schema_advertises_m4a(model):
    schema = model.get_customizable_model_schema("asr", {})
    assert "m4a" in schema.model_properties[
        ModelPropertyKey.SUPPORTED_FILE_EXTENSIONS
    ].split(",")
