"""Load the local source copy of Faster-Whisper with the FFmpeg audio adapter."""
import sys
from pathlib import Path
vendor=Path(__file__).resolve().parent/'vendor/faster-whisper-ffmpeg'
if not (vendor/'faster_whisper/audio.py').is_file():raise ImportError('Local Faster-Whisper FFmpeg adapter not installed')
sys.path.insert(0,str(vendor))
from faster_whisper import WhisperModel
