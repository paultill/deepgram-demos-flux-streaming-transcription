import asyncio
import subprocess
from typing import Any, Union
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType
from deepgram.listen.v2.types import (
    ListenV2Connected,
    ListenV2ConfigureFailure,
    ListenV2FatalError,
    ListenV2TurnInfo,
)

# The SDK's internal V2SocketClientResponse union includes typing.Any, which
# causes construct_type to yield raw dicts for TurnInfo messages rather than
# typed models. Accept both shapes here.
ListenV2SocketClientResponse = Union[
    ListenV2Connected,
    ListenV2TurnInfo,
    ListenV2ConfigureFailure,
    ListenV2FatalError,
    dict,
]


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """Read a field whether the message is a pydantic model or a plain dict."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)

# URL for the realtime streaming audio to transcribe
STREAM_URL = "http://stream.live.vc.bbcmedia.co.uk/bbc_world_service"

# Terminal color codes
class Colors:
    GREEN = '\033[92m'    # 0.90-1.00
    YELLOW = '\033[93m'   # 0.80-0.90
    ORANGE = '\033[91m'   # 0.70-0.80 (using red as orange isn't standard)
    RED = '\033[31m'      # <=0.69
    RESET = '\033[0m'     # Reset to default

def get_confidence_color(confidence: float) -> str:
    """Return the appropriate color code based on confidence score"""
    if confidence >= 0.90:
        return Colors.GREEN
    elif confidence >= 0.80:
        return Colors.YELLOW
    elif confidence >= 0.70:
        return Colors.ORANGE
    else:
        return Colors.RED

async def main():
    """Main async function to handle URL streaming to Deepgram Flux"""

    # Create the Deepgram async client
    client = AsyncDeepgramClient() # The API key retrieval happens automatically in the constructor

    try:
        # Connect to Flux with auto-detection for streaming audio
        async with client.listen.v2.connect(
            model="flux-general-en",
            encoding="linear16",
            sample_rate="16000"
        ) as connection:

            # Define message handler function
            def on_message(message: ListenV2SocketClientResponse) -> None:
                msg_type = _field(message, "type", "Unknown")

                # Show transcription results
                transcript = _field(message, "transcript")
                if transcript:
                    print(f"🎤 {transcript}")

                    # Show word-level confidence with color coding
                    words = _field(message, "words") or []
                    if words:
                        colored_words = []
                        for word in words:
                            confidence = _field(word, "confidence", 0.0)
                            text = _field(word, "word", "")
                            color = get_confidence_color(confidence)
                            colored_words.append(f"{color}{text}({confidence:.2f}){Colors.RESET}")
                        words_info = " | ".join(colored_words)
                        print(f"   📝 {words_info}")
                elif msg_type == "Connected":
                    print(f"✅ Connected to Deepgram Flux - Ready for audio!")

            # Set up event handlers
            connection.on(EventType.OPEN, lambda _: print("Connection opened"))
            connection.on(EventType.MESSAGE, on_message)
            connection.on(EventType.CLOSE, lambda _: print("Connection closed"))
            connection.on(EventType.ERROR, lambda error: print(f"Caught: {error}"))

            # Start the connection listening in background (it's already async)
            deepgram_task = asyncio.create_task(connection.start_listening())

            # Convert BBC stream to linear16 PCM using ffmpeg
            print(f"Starting to stream and convert audio from: {STREAM_URL}")

            # Use ffmpeg to convert the compressed BBC stream to linear16 PCM at 16kHz
            ffmpeg_cmd = [
                'ffmpeg',
                '-i', STREAM_URL,           # Input: BBC World Service stream
                '-f', 's16le',              # Output format: 16-bit little-endian PCM (linear16)
                '-ar', '16000',             # Sample rate: 16kHz
                '-ac', '1',                 # Channels: mono
                '-'                         # Output to stdout
            ]

            try:
                # Start ffmpeg process
                process = await asyncio.create_subprocess_exec(
                    *ffmpeg_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )

                print(f"✅ Audio conversion started (BBC → linear16 PCM)")

                # Read converted PCM data and send to Deepgram
                while True:
                    chunk = await process.stdout.read(1024)
                    if not chunk:
                        break

                    # Send converted linear16 PCM data to Flux
                    await connection.send_media(chunk)

                await process.wait()

            except Exception as e:
                print(f"Error during audio conversion: {e}")
                if 'process' in locals():
                    stderr = await process.stderr.read()
                    print(f"FFmpeg error: {stderr.decode()}")

            # Wait for Deepgram task to complete (or cancel after timeout)
            try:
                await asyncio.wait_for(deepgram_task, timeout=60)
            except asyncio.TimeoutError:
                print("Stream timeout after 60 seconds")
                deepgram_task.cancel()

    except Exception as e:
        print(f"Caught: {e}")

if __name__ == "__main__":
    asyncio.run(main())