from typing import IO, Optional
from dify_plugin.entities.model import AIModelEntity, FetchFrom, I18nObject, ModelType
from enum import IntEnum
from dify_plugin.entities.model import (
    ModelPropertyKey,
)
from dify_plugin.errors.model import (
    CredentialsValidateFailedError,
    InvokeBadRequestError,
)
from dify_plugin.interfaces.model.openai_compatible.speech2text import (
    OAICompatSpeech2TextModel,
)
import gzip
import json
import uuid
import websocket
import time
from pathlib import Path

from .audio import MAX_UPLOAD_BYTES, normalize_audio


class SpeechProtocol(IntEnum):
    """
    Enum class for llm feature.
    """

    PROTOCOL_VERSION = 0b0001
    DEFAULT_HEADER_SIZE = 0b0001

    # Message Type:
    FULL_CLIENT_REQUEST = 0b0001
    AUDIO_ONLY_REQUEST = 0b0010
    FULL_SERVER_RESPONSE = 0b1001
    SERVER_ACK = 0b1011
    SERVER_ERROR_RESPONSE = 0b1111

    # Message Type Specific Flags
    NO_SEQUENCE = 0b0000  # no check sequence
    POS_SEQUENCE = 0b0001
    NEG_SEQUENCE = 0b0010
    NEG_WITH_SEQUENCE = 0b0011
    NEG_SEQUENCE_1 = 0b0011

    # Message Serialization
    NO_SERIALIZATION = 0b0000
    JSON = 0b0001

    # Message Compression
    NO_COMPRESSION = 0b0000
    GZIP = 0b0001


class VolcengineOpenAISpeech2TextModel(OAICompatSpeech2TextModel):
    def get_customizable_model_schema(
        self, model: str, credentials: dict
    ) -> Optional[AIModelEntity]:
        """
        used to define customizable model schema
        """
        entity = AIModelEntity(
            model=model,
            label=I18nObject(en_us=model, zh_hans=model),
            fetch_from=FetchFrom.CUSTOMIZABLE_MODEL,
            model_type=ModelType.SPEECH2TEXT,
            model_properties={
                ModelPropertyKey.FILE_UPLOAD_LIMIT: 25,
                ModelPropertyKey.SUPPORTED_FILE_EXTENSIONS: "m4a,wav,mp3,mpga,amr,pcm",
            },
            parameter_rules=[],
        )
        return entity

    def _invoke(
        self, model: str, credentials: dict, file: IO[bytes], user: Optional[str] = None
    ) -> str:
        """
        Invoke speech2text model

        :param model: model name
        :param credentials: model credentials
        :param file: audio file
        :param user: unique user id
        :return: text for given audio file
        """
        return self._speech2text_invoke(model, credentials, file, user)

    def validate_credentials(self, model: str, credentials: dict) -> None:
        """
        Validate model credentials

        :param model: model name
        :param credentials: model credentials
        :return:
        """
        try:
            # Resolve demo audio from this file's directory to avoid cwd-dependent failures.
            audio_file_path = Path(__file__).resolve().with_name("demo.wav")
            with open(audio_file_path, "rb") as audio_file:
                self._speech2text_invoke(model, credentials, audio_file)
        except Exception as ex:
            raise CredentialsValidateFailedError(str(ex))

    def _speech2text_invoke(
        self, model: str, credentials: dict, file: IO[bytes], user: Optional[str] = None
    ) -> str:
        """
        Invoke speech2text model

        :param model: model name
        :param credentials: model credentials
        :param file: audio file
        :return: text for given audio file
        """
        # Dify's upload service labels every buffer temp.mp3. Decode the actual content.
        try:
            audio_data = normalize_audio(
                file.read(MAX_UPLOAD_BYTES + 1), str(getattr(file, "name", ""))
            )
        except ValueError as exc:
            raise InvokeBadRequestError(str(exc)) from exc
        reqid = str(uuid.uuid4())
        resource_id = credentials.get("resource_id", "volc.seedasr.sauc.duration")
        if resource_id not in {
            "volc.seedasr.sauc.duration",
            "volc.seedasr.sauc.concurrent",
            "volc.bigasr.sauc.duration",
            "volc.bigasr.sauc.concurrent",
        }:
            raise InvokeBadRequestError("Unsupported ASR Resource ID.")
        header = {"X-Api-Resource-Id": resource_id, "X-Api-Request-Id": reqid}
        auth_method = credentials.get("auth_method", "api_key")
        if auth_method == "api_key":
            if not credentials.get("api_key", "").strip():
                raise InvokeBadRequestError("Configure a speech API Key.")
            header["X-Api-Key"] = credentials["api_key"].strip()
        elif auth_method == "legacy":
            if (
                not credentials.get("app_key", "").strip()
                or not credentials.get("access_key", "").strip()
            ):
                raise InvokeBadRequestError(
                    "Configure both speech App ID and Access Token."
                )
            header["X-Api-App-Key"] = credentials["app_key"].strip()
            header["X-Api-Access-Key"] = credentials["access_key"].strip()
        else:
            raise InvokeBadRequestError("Unsupported authentication method.")
        request_params = {
            "user": {"uid": user or reqid},
            "audio": {
                "format": "pcm",
                "rate": 16000,
                "bits": 16,
                "channel": 1,
                "codec": "raw",
            },
            "request": {
                "model_name": "bigmodel",
                "enable_punc": True,
                "enable_itn": True,
                "result_type": "full",
            },
        }

        def packet(payload: bytes, sequence: int, *, audio: bool = False) -> bytes:
            compressed = gzip.compress(payload)
            flags = (
                SpeechProtocol.NEG_WITH_SEQUENCE
                if sequence < 0
                else SpeechProtocol.POS_SEQUENCE
            )
            header_bytes = self._generate_header(
                message_type=SpeechProtocol.AUDIO_ONLY_REQUEST
                if audio
                else SpeechProtocol.FULL_CLIENT_REQUEST,
                message_type_specific_flags=flags,
                serial_method=SpeechProtocol.NO_SERIALIZATION
                if audio
                else SpeechProtocol.JSON,
            )
            return (
                bytes(header_bytes)
                + sequence.to_bytes(4, "big", signed=True)
                + len(compressed).to_bytes(4, "big")
                + compressed
            )

        ws = None
        deadline = time.monotonic() + 120

        def set_timeout() -> None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise InvokeBadRequestError("Speech recognition timed out.")
            ws.settimeout(min(30, remaining))

        def receive() -> dict:
            set_timeout()
            raw = ws.recv()
            if not isinstance(raw, bytes) or not raw:
                raise InvokeBadRequestError(
                    "Speech provider disconnected before the final result."
                )
            result = self._parse_response(raw)
            if "code" in result:
                raise InvokeBadRequestError(
                    f"Speech provider rejected the request (code {result['code']})."
                )
            payload = result.get("payload_msg", {})
            if not isinstance(payload, dict) or payload.get("error"):
                raise InvokeBadRequestError(
                    "Speech provider returned an invalid or unsuccessful result."
                )
            return result

        try:
            # Fixed endpoint and normal certificate verification. No custom outbound URLs.
            ws = websocket.create_connection(
                "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream",
                header=header,
                timeout=30,
            )
            set_timeout()
            ws.send_binary(packet(json.dumps(request_params).encode(), 1))
            if receive().get("is_last_package"):
                raise InvokeBadRequestError(
                    "Speech provider ended recognition before receiving audio."
                )
            text = ""
            # 200 ms of mono PCM per packet. The provider acknowledges each input packet.
            for sequence, (chunk, last) in enumerate(
                self._slice_data(audio_data, 6400), 2
            ):
                set_timeout()
                ws.send_binary(
                    packet(chunk, -sequence if last else sequence, audio=True)
                )
                response = receive()
                while True:
                    result = response.get("payload_msg", {}).get("result", {})
                    if not isinstance(result, dict):
                        raise InvokeBadRequestError(
                            "Speech provider returned an invalid result."
                        )
                    if "text" in result:
                        if not isinstance(result["text"], str):
                            raise InvokeBadRequestError(
                                "Speech provider returned invalid transcript text."
                            )
                        text = result["text"]
                    if response.get("is_last_package"):
                        if not last:
                            raise InvokeBadRequestError(
                                "Speech provider ended recognition before receiving all audio."
                            )
                        return text
                    if not last:
                        break
                    response = receive()
            raise InvokeBadRequestError(
                "Speech provider did not return a final result."
            )
        except InvokeBadRequestError:
            raise
        except Exception as exc:
            # Do not echo provider errors, URLs, headers, or credentials back to callers.
            raise InvokeBadRequestError(
                "Speech recognition failed or timed out. Check the speech credentials and resource."
            ) from exc
        finally:
            if ws is not None:
                ws.close()

    def _generate_header(
        self,
        message_type=SpeechProtocol.FULL_CLIENT_REQUEST,
        message_type_specific_flags=SpeechProtocol.NO_SEQUENCE,
        serial_method=SpeechProtocol.JSON,
        compression_type=SpeechProtocol.GZIP,
        reserved_data=0x00,
    ):
        """
        protocol_version(4 bits), header_size(4 bits),
        message_type(4 bits), message_type_specific_flags(4 bits)
        serialization_method(4 bits) message_compression(4 bits)
        reserved （8bits)
        """
        header = bytearray()
        header_size = 1
        header.append(((SpeechProtocol.PROTOCOL_VERSION) << 4) | header_size)
        header.append((message_type << 4) | message_type_specific_flags)
        header.append((serial_method << 4) | compression_type)
        header.append(reserved_data)
        return header

    def _generate_before_payload(self, sequence: int):
        before_payload = bytearray()
        before_payload.extend(sequence.to_bytes(4, "big", signed=True))  # sequence
        return before_payload

    def _parse_response(self, res: bytes) -> dict:
        """Read a Volcengine V3 response, rejecting truncated/unsupported frames."""
        if len(res) < 4 or res[0] >> 4 != SpeechProtocol.PROTOCOL_VERSION:
            raise ValueError("Invalid speech response header")
        offset = (res[0] & 0x0F) * 4
        if offset < 4 or offset > len(res):
            raise ValueError("Invalid speech response header length")
        message_type = res[1] >> 4
        flags = res[1] & 0x0F
        serialization = res[2] >> 4
        compression = res[2] & 0x0F
        result = {"is_last_package": bool(flags & 2)}

        def read_u32() -> int:
            nonlocal offset
            if offset + 4 > len(res):
                raise ValueError("Truncated speech response")
            value = int.from_bytes(res[offset : offset + 4], "big")
            offset += 4
            return value

        if message_type == SpeechProtocol.SERVER_ERROR_RESPONSE:
            result["code"] = read_u32()
        elif message_type == SpeechProtocol.FULL_SERVER_RESPONSE:
            if flags & 1:
                read_u32()  # Sequence number is not needed for this single in-flight request.
        elif message_type == SpeechProtocol.SERVER_ACK:
            read_u32()
            if offset == len(res):
                return result
        else:
            raise ValueError("Unsupported speech response type")
        size = read_u32()
        if size != len(res) - offset:
            raise ValueError("Truncated speech response payload")
        payload = res[offset:]
        if compression == SpeechProtocol.GZIP:
            payload = gzip.decompress(payload)
        elif compression != SpeechProtocol.NO_COMPRESSION:
            raise ValueError("Unsupported speech response compression")
        if serialization == SpeechProtocol.JSON:
            result["payload_msg"] = json.loads(payload)
        elif payload and message_type != SpeechProtocol.SERVER_ERROR_RESPONSE:
            raise ValueError("Unsupported speech response serialization")
        return result

    def _slice_data(self, data: bytes, chunk_size: int) -> (list, bool):
        data_len = len(data)
        offset = 0
        while offset + chunk_size < data_len:
            yield data[offset : offset + chunk_size], False
            offset += chunk_size
        else:
            yield data[offset:data_len], True
