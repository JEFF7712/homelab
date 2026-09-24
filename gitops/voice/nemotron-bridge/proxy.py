"""Wyoming Voice-ID Proxy.

Intercepts Wyoming STT audio events, identifies the speaker using sherpa-onnx
speaker embeddings, and prepends speaker <Name> to the transcript.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import urllib.request
from pathlib import Path

try:
    import numpy as np
    import sherpa_onnx
    from wyoming.asr import Transcript
    from wyoming.audio import AudioChunk, AudioStart, AudioStop
    from wyoming.event import async_read_event, async_write_event
except ImportError:
    np = None  # type: ignore[assignment]
    sherpa_onnx = None  # type: ignore[assignment]
    Transcript = None  # type: ignore[assignment]
    AudioChunk = None  # type: ignore[assignment]
    AudioStart = None  # type: ignore[assignment]
    AudioStop = None  # type: ignore[assignment]
    async_read_event = None  # type: ignore[assignment]
    async_write_event = None  # type: ignore[assignment]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [voice-id] %(message)s",
)
logger = logging.getLogger("voice-id")


def pad_or_tile(
    samples: np.ndarray, target_sec: float = 3.5, rate: int = 16000
) -> np.ndarray:
    """Pad or tile audio samples to target duration to ensure sufficient temporal receptive field."""
    target_samples = int(target_sec * rate)
    if len(samples) >= target_samples:
        return samples
    repeats = int(np.ceil(target_samples / len(samples)))
    return np.tile(samples, repeats)[:target_samples]


class VoiceIdClassifier:
    def __init__(
        self,
        model_path: str | Path,
        profiles_path: str | Path,
        model_url: str | None = None,
    ) -> None:
        self.model_path = Path(model_path)
        self.profiles_path = Path(profiles_path)
        self.model_url = model_url

        self._ensure_model()
        self.config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(self.model_path),
            num_threads=2,
            debug=False,
        )
        self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(self.config)
        self.profiles: dict[str, np.ndarray] = {}
        self.threshold = 0.35
        self.min_margin = 0.10
        self._load_profiles()

    def _ensure_model(self) -> None:
        if self.model_path.exists():
            return
        if not self.model_url:
            raise FileNotFoundError(
                f"Model not found at {self.model_path} and no model_url provided"
            )
        logger.info(
            f"Model not found at {self.model_path}. Downloading from {self.model_url}..."
        )
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.model_path.with_suffix(".tmp")
        urllib.request.urlretrieve(self.model_url, tmp_path)
        tmp_path.rename(self.model_path)
        logger.info(f"Model downloaded successfully to {self.model_path}")

    def _load_profiles(self) -> None:
        if not self.profiles_path.exists():
            logger.warning(
                f"Profiles file not found at {self.profiles_path}; running with empty profiles."
            )
            return
        with self.profiles_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        self.threshold = float(data.get("threshold", 0.35))
        self.min_margin = float(data.get("min_margin", 0.10))
        self.profiles = {
            name: np.array(vec, dtype=np.float32)
            for name, vec in data.get("speakers", {}).items()
        }
        logger.info(
            f"Loaded {len(self.profiles)} speaker profiles: {list(self.profiles.keys())} "
            f"(threshold: {self.threshold}, min_margin: {self.min_margin})"
        )

    def identify_pcm(
        self, pcm_bytes: bytes, rate: int = 16000
    ) -> tuple[str | None, dict[str, float]]:
        """Identify speaker from 16kHz 16-bit mono PCM bytes."""
        min_samples = int(0.6 * rate)  # minimum 0.6s
        if not self.profiles or len(pcm_bytes) < min_samples * 2:
            return None, {}

        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        # Check energy
        rms = float(np.sqrt(np.mean(samples**2)))
        if rms < 0.003:
            logger.debug(f"Audio energy too low (rms={rms:.5f}); skipping speaker ID")
            return None, {}

        # Pad / tile to 3.5s to ensure full context for the neural network
        processed = pad_or_tile(samples, target_sec=3.5, rate=rate)

        stream = self.extractor.create_stream()
        stream.accept_waveform(rate, processed)
        stream.input_finished()
        vec = np.array(self.extractor.compute(stream), dtype=np.float32)
        norm = float(np.linalg.norm(vec))
        if norm < 1e-6:
            return None, {}
        vec /= norm

        scores = {
            name: float(np.dot(vec, centroid))
            for name, centroid in self.profiles.items()
        }
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_speaker, top_score = sorted_scores[0]
        runner_up_score = sorted_scores[1][1] if len(sorted_scores) > 1 else -1.0
        margin = top_score - runner_up_score

        if top_score >= self.threshold and margin >= self.min_margin:
            logger.info(
                f"Identified speaker: {top_speaker} (score={top_score:.3f}, margin={margin:.3f})"
            )
            return top_speaker, scores

        logger.info(
            f"Ambiguous or unknown speaker: top={top_speaker} ({top_score:.3f}), "
            f"runner_up=({runner_up_score:.3f}, margin={margin:.3f}, thresh={self.threshold})"
        )
        return None, scores


class SessionState:
    def __init__(self) -> None:
        self.audio_buffer = bytearray()
        self.speaker: str | None = None
        self.recognition_done = asyncio.Event()
        self.recognition_done.set()
        self.identify_task: asyncio.Task | None = None

    def reset_audio(self) -> None:
        if self.identify_task is not None and not self.identify_task.done():
            self.identify_task.cancel()
        self.identify_task = None
        self.audio_buffer.clear()
        self.speaker = None
        self.recognition_done.set()


def normalize_transcript(text: str) -> str:
    """Lowercase, drop sentence punctuation, collapse whitespace."""
    clean = text.strip().lower()
    for punct in ".!?,;:":
        clean = clean.replace(punct, "")
    return " ".join(clean.split())


# Exact normalized transcripts that are observed Whisper hallucinations, never
# legitimate commands. Keep this tight: short real commands ("Stop.") must
# keep working, and bare wake-word echoes stay untouched so a lone
# "Hey Jarvis" still gets its normal acknowledgement.
_GARBAGE_DENYLIST = frozenset(
    {
        "stu",
        "control",
        "further",
        "chew away",
        "say something",
        "today whoever",
        "you wait hey jarvis who am i",
        "govee",
        "govee v",
    }
)


def is_garbage_transcript(text: str) -> bool:
    """Detect non-speech Whisper hallucinations that must not reach HA.

    A garbage turn still runs the full pipeline and speaks a confabulated
    reply, which the mic picks up again (self-talk loop). Dropping here is
    safe: HA treats the resulting empty transcript as an abandoned turn and
    stays silent.
    """
    clean = normalize_transcript(text)
    if not clean:
        return True
    tokens = [tok.strip(",.") for tok in clean.split()]
    tokens = [tok for tok in tokens if tok]
    if len(tokens) >= 3 and len(set(tokens)) == 1:
        return True
    return clean in _GARBAGE_DENYLIST


def format_transcript_with_speaker(transcript: str, speaker: str | None) -> str:
    """Format transcript with speaker context when relevant.

    For speaker-dependent commands (identity queries and music playback),
    prepends 'speaker <Name>' so Home Assistant's conversation triggers
    and HassIL sentences match and extract the speaker slot cleanly.
    For standard home control commands (e.g. lights, climate, sensors),
    leaves the transcript unaltered so Home Assistant's built-in local intent
    engine matches deterministically without waking the LLM.
    """
    if not speaker:
        return transcript

    clean = transcript.strip().rstrip(".!?").lower()

    # Music requests: "play ...", "put on ...", "put some ... on",
    # "listen to ...". Each prefix mirrors a sentence in
    # home-assistant/automations/jarvis_voice_music_playback.yaml; keep them
    # in sync (see test_speaker_prefix_covers_automation_commands).
    music_prefixes = ("play ", "put on ", "put some ", "listen to ")
    is_music = any(clean.startswith(p) for p in music_prefixes)

    # Identity requests: "who am i", "who is speaking", etc. Each phrase
    # mirrors a command in
    # home-assistant/automations/jarvis_voice_identity.yaml; keep them in
    # sync (see test_speaker_prefix_covers_automation_commands).
    identity_phrases = (
        "who am i",
        "who is speaking",
        "who is this",
        "what is my name",
        "what's my name",
        "who's speaking",
        "do you know who i am",
        "do you know who this is",
        "can you tell who i am",
        "are you able to tell who is speaking",
        "who do you think i am",
        "who are you speaking to",
        "who is talking",
    )
    is_identity = any(phrase in clean for phrase in identity_phrases)

    if is_music or is_identity:
        body = transcript.strip().rstrip(".!?")
        return f"speaker {speaker} {body}"

    return transcript


async def handle_client(
    reader_c: asyncio.StreamReader,
    writer_c: asyncio.StreamWriter,
    upstream_host: str,
    upstream_port: int,
    classifier: VoiceIdClassifier,
) -> None:
    client_addr = writer_c.get_extra_info("peername")
    logger.info(f"Incoming connection from {client_addr}")

    try:
        reader_u, writer_u = await asyncio.open_connection(upstream_host, upstream_port)
    except Exception as e:
        logger.error(
            f"Failed to connect to upstream {upstream_host}:{upstream_port}: {e}"
        )
        writer_c.close()
        await writer_c.wait_closed()
        return

    session = SessionState()

    async def client_to_upstream() -> None:
        try:
            while True:
                event = await async_read_event(reader_c)
                if event is None:
                    break

                if AudioStart.is_type(event.type):
                    session.reset_audio()

                elif AudioChunk.is_type(event.type):
                    if event.payload:
                        session.audio_buffer.extend(event.payload)

                elif AudioStop.is_type(event.type):
                    session.recognition_done.clear()
                    pcm = bytes(session.audio_buffer)

                    # Promptly forward audio-stop to upstream Whisper
                    await async_write_event(event, writer_u)

                    # Compute speaker identification off the asyncio loop
                    async def do_identify(audio_data: bytes) -> None:
                        try:
                            if classifier is not None and audio_data:
                                speaker, _ = await asyncio.to_thread(
                                    classifier.identify_pcm, audio_data
                                )
                                session.speaker = speaker
                            else:
                                session.speaker = None
                        except Exception as e:
                            logger.error(
                                f"Error in speaker identification: {e}", exc_info=True
                            )
                            session.speaker = None
                        finally:
                            session.recognition_done.set()

                    if (
                        session.identify_task is not None
                        and not session.identify_task.done()
                    ):
                        session.identify_task.cancel()
                    session.identify_task = asyncio.create_task(do_identify(pcm))
                    continue

                await async_write_event(event, writer_u)
        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            logger.error(f"Error in client_to_upstream: {e}", exc_info=True)
        finally:
            session.reset_audio()
            writer_u.close()
            try:
                await writer_u.wait_closed()
            except Exception:
                pass

    async def upstream_to_client() -> None:
        try:
            while True:
                event = await async_read_event(reader_u)
                if event is None:
                    break

                if Transcript.is_type(event.type):
                    try:
                        await asyncio.wait_for(
                            session.recognition_done.wait(), timeout=1.5
                        )
                    except asyncio.TimeoutError:
                        logger.warning(
                            "Timed out waiting for speaker recognition to finish"
                        )

                    if event.data and "text" in event.data:
                        orig_text = event.data["text"].strip()
                        if is_garbage_transcript(orig_text):
                            logger.warning(
                                f"Dropping hallucinated transcript: '{orig_text}' "
                                f"(speaker: {session.speaker})"
                            )
                            orig_text = ""
                            formatted_text = ""
                        else:
                            formatted_text = format_transcript_with_speaker(
                                orig_text, session.speaker
                            )
                        event.data["text"] = formatted_text
                        logger.info(
                            f"Transcript formatted: '{orig_text}' -> '{formatted_text}' "
                            f"(speaker: {session.speaker})"
                        )
                    session.reset_audio()

                await async_write_event(event, writer_c)
        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            logger.error(f"Error in upstream_to_client: {e}", exc_info=True)
        finally:
            writer_c.close()
            try:
                await writer_c.wait_closed()
            except Exception:
                pass

    t1 = asyncio.create_task(client_to_upstream())
    t2 = asyncio.create_task(upstream_to_client())

    done, pending = await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    logger.info(f"Connection closed for {client_addr}")


async def run_proxy(args: argparse.Namespace) -> None:
    classifier = VoiceIdClassifier(
        model_path=args.model,
        profiles_path=args.profiles,
        model_url=args.model_url,
    )

    server = await asyncio.start_server(
        lambda r, w: handle_client(
            r, w, args.upstream_host, args.upstream_port, classifier
        ),
        args.host,
        args.port,
    )

    addrs = ", ".join(str(sock.getsockname()) for sock in server.sockets)
    logger.info(
        f"Wyoming Voice-ID Proxy listening on {addrs} -> upstream {args.upstream_host}:{args.upstream_port}"
    )

    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Wyoming Voice-ID Proxy")
    parser.add_argument("--host", default="0.0.0.0", help="Listen host")
    parser.add_argument("--port", type=int, default=10300, help="Listen port")
    parser.add_argument(
        "--upstream-host", default="127.0.0.1", help="Upstream Whisper host"
    )
    parser.add_argument(
        "--upstream-port", type=int, default=10301, help="Upstream Whisper port"
    )
    parser.add_argument(
        "--model", default="/data/model.onnx", help="Path to CAMP++ ONNX model"
    )
    parser.add_argument(
        "--profiles", default="/data/profiles.json", help="Path to profiles.json"
    )
    parser.add_argument(
        "--model-url",
        default="https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx",
        help="URL to download model if missing",
    )
    args = parser.parse_args()

    asyncio.run(run_proxy(args))


if __name__ == "__main__":
    main()
