"""Timestamp-driven local UHD renderer; no network calls or provider billing.

Assets must cover timestamp units in order, using text or [start_index,end_index).
Indices address supplied timestamp units (words OR whole sentences); no invented
word timing inside sentence timestamps. Source audio is the master clock.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence


class VideoEngineError(RuntimeError):
    pass


@dataclass(frozen=True)
class Timing:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Scene:
    path: str
    start_frame: int
    end_frame: int
    start_index: int
    end_index: int
    loop: bool = False


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('A finite timestamp in seconds is required')
    return float(value)


def _normalize(text: str) -> str:
    # Strip punctuation, keep Unicode letters/numbers and their word boundaries.
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in
                            unicodedata.normalize('NFKC', text).casefold()).split())


def _local(path: str | Path, root: Path | None = None) -> Path:
    p = Path(path).expanduser()
    if root is not None:
        p = p if p.is_absolute() else root / p
    p = p.resolve()
    if root is not None and (p == root or root not in p.parents):
        raise ValueError('Media/output path is outside the assigned workspace')
    return p


def _run(args: Sequence[str], *, cwd: Path | None = None, timeout: int = 1800) -> subprocess.CompletedProcess:
    try:
        p = subprocess.run(list(args), shell=False, cwd=cwd, capture_output=True,
                           text=True, encoding='utf-8', errors='replace', timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise VideoEngineError('FFmpeg operation exceeded its time limit') from exc
    if p.returncode:
        raise VideoEngineError(f'{Path(args[0]).name} exited {p.returncode}: {p.stderr[-1800:]}')
    return p


def _tool(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise VideoEngineError(f'{name} is missing from PATH')
    return executable


def probe(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f'Media file not found: {path.name}')
    return json.loads(_run([_tool('ffprobe'), '-v', 'error', '-show_streams',
                           '-show_format', '-of', 'json', str(path)], timeout=60).stdout)


def _duration(info: dict) -> float:
    d = float(info.get('format', {}).get('duration', 0))
    if not math.isfinite(d) or d <= 0:
        raise ValueError('Audio/video needs a positive finite duration')
    return d


def alignment_to_timestamps(alignment: dict) -> list[dict]:
    """Accept ElevenLabs alignment or a convert-with-timestamps response.

    Audio is not decoded here. Keep audio and alignment from the SAME response.
    """
    a = alignment.get('normalized_alignment') or alignment.get('alignment') or alignment
    chars = a.get('characters')
    starts, ends = a.get('character_start_times_seconds'), a.get('character_end_times_seconds')
    if not isinstance(chars, list) or not isinstance(starts, list) or not isinstance(ends, list):
        raise ValueError('Expected ElevenLabs character alignment arrays')
    if not (len(chars) == len(starts) == len(ends)) or not chars:
        raise ValueError('Character alignment arrays have different lengths')
    if any(not isinstance(c, str) or len(c) != 1 for c in chars):
        raise ValueError('Alignment entries must each be one character')
    text = ''.join(chars)
    result = []
    for m in re.finditer(r'\S+', text):
        result.append({'start': _number(starts[m.start()]),
                       'end': _number(ends[m.end()-1]), 'text': m.group()})
    return result


def normalize_timestamps(script_text: str, timestamps: list | dict, duration: float) -> list[Timing]:
    if not isinstance(script_text, str) or not script_text.strip() or len(script_text) > 100_000:
        raise ValueError('Nonempty script_text is required (maximum 100000 characters)')
    if isinstance(timestamps, dict):
        timestamps = alignment_to_timestamps(timestamps)
    if not isinstance(timestamps, list) or not timestamps or len(timestamps) > 20000:
        raise ValueError('Supply timestamped words/sentences or ElevenLabs alignment')
    units, last = [], 0.0
    for entry in timestamps:
        if not isinstance(entry, dict):
            raise ValueError('Each timestamp must be an object')
        a, b = _number(entry.get('start')), _number(entry.get('end'))
        text = entry.get('text', entry.get('word'))
        if not isinstance(text, str) or not text.strip() or '\x00' in text:
            raise ValueError('Each timestamp needs text or word')
        if a < last - 1e-6 or b <= a or a < 0 or a >= duration or b > duration + .05:
            raise ValueError('Timestamps overlap, are reversed, or exceed audio')
        units.append(Timing(a, min(b, duration), text.strip()))
        last = b
    if _normalize(' '.join(t.text for t in units)) != _normalize(script_text):
        raise ValueError('Alignment/transcript does not match script; review before rendering')
    return units


def extract_timestamps(script_text: str, audio_path: str | Path, *, model_path: str | Path,
                       language: str | None = None, cpu_threads: int = 2) -> list[dict]:
    """Local Faster-Whisper. No implicit download; mismatch fails closed."""
    model_path = Path(model_path).resolve()
    if not (model_path / 'model.bin').is_file():
        raise ValueError('Supply an installed local Faster-Whisper model directory')
    try:
        from whisper_backend import WhisperModel  # Existing Zoya FFmpeg adapter.
    except ImportError:
        from faster_whisper import WhisperModel
    model = WhisperModel(str(model_path), device='cpu', compute_type='int8',
                         cpu_threads=cpu_threads, local_files_only=True)
    segments, _ = model.transcribe(str(Path(audio_path).resolve()), language=language,
                                  beam_size=1, word_timestamps=True,
                                  condition_on_previous_text=False, vad_filter=False)
    words = [{'start': float(w.start), 'end': float(w.end), 'text': w.word.strip()}
             for s in segments for w in (s.words or []) if w.word.strip()]
    normalize_timestamps(script_text, words, _duration(probe(Path(audio_path).resolve())))
    return words


def plan_scenes(units: list[Timing], media_assets: list[dict], audio_duration: float,
                fps: int = 30) -> list[Scene]:
    if fps not in (30, 60):
        raise ValueError('UHD output supports 30 or 60 fps')
    if not isinstance(media_assets, list) or not 1 <= len(media_assets) <= 100:
        raise ValueError('Supply 1-100 local media assets with exact spoken text or unit indices')
    cursor, scenes = 0, []
    total_frames = math.ceil(audio_duration * fps - 1e-8)
    for asset in media_assets:
        if not isinstance(asset, dict) or not isinstance(asset.get('path'), (str, Path)):
            raise ValueError('Asset needs a local path')
        a = asset.get('start_index', cursor)
        if type(a) is not int or a != cursor:
            raise ValueError('Assets must cover every timestamp unit exactly once, in order')
        b = asset.get('end_index')
        if 'text' in asset:
            target = _normalize(asset['text']) if isinstance(asset['text'], str) else ''
            if not target:
                raise ValueError('Asset text must be nonempty')
            found = next((i for i in range(a+1, len(units)+1)
                          if _normalize(' '.join(t.text for t in units[a:i])) == target), None)
            if found is None or (b is not None and b != found):
                raise ValueError('Asset text must match complete adjacent timestamp units')
            b = found
        if type(b) is not int or not a < b <= len(units):
            raise ValueError('Asset needs matching text or a valid exclusive end_index')
        first = 0 if a == 0 else round(units[a].start * fps)
        last = total_frames if b == len(units) else round(units[b].start * fps)
        if last <= first:
            raise ValueError('Scene is shorter than one output frame')
        if 'loop' in asset and type(asset['loop']) is not bool:
            raise ValueError('loop must be an explicit boolean')
        scenes.append(Scene(str(asset['path']), first, last, a, b, asset.get('loop', False)))
        cursor = b
    if cursor != len(units):
        raise ValueError('Assets do not cover the entire spoken script')
    return scenes


def _srt(units: list[Timing]) -> str:
    def stamp(v: float) -> str:
        n = round(v*1000); h, n = divmod(n, 3600000); m, n = divmod(n, 60000); s, ms = divmod(n, 1000)
        return f'{h:02}:{m:02}:{s:02},{ms:03}'
    lines, group = [], []
    for i, unit in enumerate(units):
        group.append(unit)
        text = ' '.join(x.text for x in group)
        if len(text) >= 36 or len(group) >= 5 or i == len(units)-1 or unit.text[-1:] in '.!?':
            clean = text.replace('\n', ' ').replace('\r', ' ').replace('-->', '→')
            if len(clean) > 160:
                raise ValueError('Caption too long; supply word timestamps or shorter measured sentence units')
            if any(c in clean for c in '{}'):
                raise ValueError('Subtitle text cannot contain ASS override braces')
            lines.append(f'{len(lines)+1}\n{stamp(group[0].start)} --> {stamp(group[-1].end)}\n{clean}\n')
            group = []
    return '\n'.join(lines)


def render_video(script_text: str, audio_path: str | Path, timestamps: list | dict | None,
                 media_assets: list[dict], output_path: str | Path, *, fps: int = 30,
                 codec: str = 'h264', model_path: str | Path | None = None,
                 language: str | None = None, workspace: str | Path | None = None,
                 require_native_4k: bool = True, burn_subtitles: bool = True,
                 crf: int = 18, threads: int = 2, timeout_seconds: int = 1800) -> dict:
    """Render frame-accurate visuals against an unchanged narration master.

    No claim is made that B-roll depicts the narration: selection is a separate
    editorial task. Talking-character clips must already match this master audio.
    Never replace a character's dialogue audio without redoing lip-sync.
    """
    if fps not in (30, 60) or codec not in ('h264', 'h265'):
        raise ValueError('Use 30/60 fps and h264/h265')
    if type(crf) is not int or not 14 <= crf <= 23 or not 1 <= threads <= 16:
        raise ValueError('Use CRF 14-23 and 1-16 encoder threads')
    if not 1 <= timeout_seconds <= 7200:
        raise ValueError('Invalid subprocess timeout')
    if type(require_native_4k) is not bool or type(burn_subtitles) is not bool:
        raise ValueError('Resolution and subtitle switches must be booleans')
    root = Path(workspace).resolve() if workspace else None
    audio, output = _local(audio_path, root), _local(output_path, root)
    if output.suffix.lower() != '.mp4' or output.exists():
        raise ValueError('Use a new .mp4 output path; existing outputs are never overwritten')
    audio_info = probe(audio)
    if not any(s.get('codec_type') == 'audio' for s in audio_info.get('streams', [])):
        raise ValueError('audio_path has no audio stream')
    duration = _duration(audio_info)
    if duration > 3600:
        raise ValueError('Maximum render length is 60 minutes')
    if timestamps is None:
        if model_path is None:
            raise ValueError('Supply timestamps or a local Whisper model')
        timestamps = extract_timestamps(script_text, audio, model_path=model_path, language=language)
    units = normalize_timestamps(script_text, timestamps, duration)
    scenes = plan_scenes(units, media_assets, duration, fps)
    source_meta = []
    for scene in scenes:
        p = _local(scene.path, root)
        info = probe(p)
        v = next((s for s in info.get('streams', []) if s.get('codec_type') == 'video'), None)
        if not v:
            raise ValueError('Asset has no video/image stream')
        if require_native_4k and (v.get('width', 0) < 3840 or v.get('height', 0) < 2160):
            raise ValueError(f'Native UHD source required: {p.name}')
        image = p.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff')
        scene_duration = (scene.end_frame-scene.start_frame)/fps
        if not image and not scene.loop and _duration(info) + 1/fps < scene_duration:
            raise ValueError('Clip is too short: supply a longer clip or explicitly allow looping')
        source_meta.append((p, image, v.get('width'), v.get('height')))
    ffmpeg = _tool('ffmpeg')
    output.parent.mkdir(parents=True, exist_ok=True)
    lock_path = output.with_suffix('.mp4.lock')
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise VideoEngineError('Output is locked. Reconcile interrupted worker before removing its lock.') from exc
    os.close(lock_fd)
    encoder = 'libx264' if codec == 'h264' else 'libx265'
    try:
        with tempfile.TemporaryDirectory(prefix='.zoya-video-', dir=output.parent) as temp:
            temp = Path(temp)
            for i, (scene, (source, image, _, _)) in enumerate(zip(scenes, source_meta)):
                args = [ffmpeg, '-nostdin', '-v', 'error']
                if image:
                    args += ['-loop', '1', '-framerate', str(fps)]
                elif scene.loop:
                    args += ['-stream_loop', '-1']
                args += ['-i', str(source), '-map', '0:v:0', '-an', '-vf',
                         f'scale=3840:2160:force_original_aspect_ratio=increase,crop=3840:2160,setsar=1,fps={fps},format=yuv420p',
                         '-frames:v', str(scene.end_frame-scene.start_frame), '-c:v', encoder,
                         '-preset', 'fast', '-crf', str(crf), '-threads', str(threads)]
                if codec == 'h265':
                    args += ['-x265-params', f'pools={threads}:frame-threads=1', '-tag:v', 'hvc1']
                _run(args + [f'scene-{i:03}.mp4'], cwd=temp, timeout=timeout_seconds)
            (temp/'scenes.txt').write_text(''.join(f"file 'scene-{i:03}.mp4'\n" for i in range(len(scenes))), encoding='utf-8')
            render_duration = scenes[-1].end_frame/fps
            args = [ffmpeg, '-nostdin', '-v', 'error', '-f', 'concat', '-safe', '1', '-i', 'scenes.txt',
                    '-i', str(audio), '-map', '0:v:0', '-map', '1:a:0']
            captions = _srt(units)
            (temp/'captions.srt').write_text(captions, encoding='utf-8')
            if burn_subtitles:
                args += ['-vf', "subtitles=captions.srt:force_style='FontName=Arial,FontSize=18,Outline=2,Shadow=0,Alignment=2,MarginV=34,MarginL=22,MarginR=22'",
                         '-c:v', encoder, '-preset', 'fast', '-crf', str(crf), '-threads', str(threads)]
                if codec == 'h265':
                    args += ['-x265-params', f'pools={threads}:frame-threads=1', '-tag:v', 'hvc1']
            else:
                args += ['-c:v', 'copy']
            args += ['-af', f'apad,atrim=duration={render_duration}', '-c:a', 'aac', '-b:a', '192k',
                     '-ar', '48000', '-t', str(render_duration), '-movflags', '+faststart', 'final.mp4']
            _run(args, cwd=temp, timeout=timeout_seconds)
            final = temp/'final.mp4'
            info = probe(final)
            video = next(s for s in info['streams'] if s['codec_type'] == 'video')
            audio_stream = next((s for s in info['streams'] if s['codec_type'] == 'audio'), None)
            num, den = map(int, video['avg_frame_rate'].split('/'))
            if ((video['width'], video['height']) != (3840, 2160) or den == 0
                    or abs(num/den-fps) > .01 or not audio_stream
                    or abs(_duration(info)-duration) > 1/fps+.05):
                raise VideoEngineError('Final dimensions/frame-rate/audio/duration QA failed')
            _run([ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-i', str(final), '-f', 'null', '-'], timeout=timeout_seconds)
            volume = _run([ffmpeg, '-nostdin', '-hide_banner', '-i', str(final), '-vn', '-af', 'volumedetect', '-f', 'null', '-'], timeout=timeout_seconds)
            match = re.search(r'mean_volume:\s*(-?[\d.]+|-inf) dB', volume.stderr)
            if not match or not math.isfinite(float(match[1])) or float(match[1]) < -60:
                raise VideoEngineError('Narration is silent or inaudible')
            if output.exists():
                raise FileExistsError('Output appeared during render')
            os.replace(final, output)
            report = {'output': str(output), 'width': 3840, 'height': 2160, 'fps': fps, 'codec': codec,
                      'audio_duration': duration, 'output_duration': _duration(info),
                      'timing_error_seconds': abs(_duration(info)-duration),
                      'sha256': _sha(output), 'native_4k_required': require_native_4k,
                      'subtitles_burned': burn_subtitles, 'technical_qa_passed': True,
                      'semantic_visual_match_verified': False,
                      'scenes': [asdict(s) for s in scenes],
                      'sources': [{'path': str(p), 'width': w, 'height': h} for p, _, w, h in source_meta]}
            output.with_suffix('.render.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            output.with_suffix('.srt').write_text(captions, encoding='utf-8')
            return report
    finally:
        lock_path.unlink(missing_ok=True)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1048576), b''):
            h.update(b)
    return h.hexdigest()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--workspace', type=Path)
    args = p.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding='utf-8-sig'))
    allowed = {'script_text', 'audio_path', 'timestamps', 'media_assets', 'fps', 'codec',
               'model_path', 'language', 'require_native_4k', 'burn_subtitles', 'crf', 'threads'}
    if set(manifest)-allowed:
        p.error('Unknown manifest fields')
    print(json.dumps(render_video(output_path=args.output, workspace=args.workspace, **manifest), indent=2))


if __name__ == '__main__':
    main()
