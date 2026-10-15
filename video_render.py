"""Local, approved-media-only renderer. Never uploads or launches paid providers.

Contract: render_video(root, clips, narration, output, *, approved=False,
                      subtitles=None, config=None) -> artifact metadata.
All filenames are relative to the assigned root. Visuals repeat to narration
length; original clip audio is removed. Call only after exact task approval.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class RenderError(RuntimeError):
    pass


def scoped_path(root, filename, *, must_exist=True):
    base = Path(root).resolve()
    name = Path(filename)
    if name.is_absolute():
        raise ValueError('Use a workspace-relative media path')
    path = (base / name).resolve()
    if path == base or base not in path.parents:
        raise ValueError('Media path outside assigned workspace')
    if must_exist and not path.is_file():
        raise ValueError('Media file missing')
    return path


@dataclass(frozen=True)
class RenderConfig:
    width: int = 1920
    height: int = 1080
    fps: int = 30
    crf: int = 20
    timeout_seconds: int = 1800
    max_duration_seconds: int = 3600

    def validate(self):
        if not (128 <= self.width <= 3840 and 128 <= self.height <= 3840):
            raise ValueError('Dimensions outside supported range')
        if self.width % 2 or self.height % 2:
            raise ValueError('Dimensions must be even')
        if self.fps not in (24, 25, 30, 50, 60):
            raise ValueError('Unsupported frame rate')
        if not 16 <= self.crf <= 28:
            raise ValueError('CRF outside quality range')
        if not 1 <= self.timeout_seconds <= 7200 or not 1 <= self.max_duration_seconds <= 7200:
            raise ValueError('Invalid render resource limit')


def _tool(name):
    executable = shutil.which(name)
    if not executable:
        raise RenderError(f'{name} is not installed; install FFmpeg before rendering')
    return executable


def _run(argv, *, cwd=None, timeout=60):
    try:
        result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                                timeout=timeout, shell=False)
    except subprocess.TimeoutExpired as exc:
        raise RenderError('Media operation exceeded its time limit') from exc
    if result.returncode:
        # Do not expose arbitrary stderr paths/content in owner-facing errors.
        raise RenderError(f'{Path(argv[0]).name} failed (exit {result.returncode})')
    return result


def probe_media(path):
    """Probe a trusted local path. External URLs are never accepted."""
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError('Local media file missing')
    result = _run([_tool('ffprobe'), '-v', 'error', '-show_format', '-show_streams',
                   '-of', 'json', str(path)])
    info = json.loads(result.stdout)
    duration = float(info.get('format', {}).get('duration', 0))
    if not math.isfinite(duration) or duration <= 0:
        raise RenderError('Media has no finite positive duration')
    return {'duration': duration, 'streams': info.get('streams', []),
            'size_bytes': path.stat().st_size}


def qa_video(path, *, width, height, duration, fps=None, require_audible=True):
    """Verify exported dimensions, narration duration, audio presence and volume.

    This is technical QA, not copyright, factual, visual or editorial review.
    """
    info = probe_media(path)
    video = next((s for s in info['streams'] if s.get('codec_type') == 'video'), None)
    audio = next((s for s in info['streams'] if s.get('codec_type') == 'audio'), None)
    failures = []
    if not video or (video.get('width'), video.get('height')) != (width, height):
        failures.append('dimensions')
    if abs(info['duration'] - duration) > max(.25, duration * .005):
        failures.append('duration')
    if not audio:
        failures.append('missing_audio')
    if fps and video:
        try:
            num, den = map(float, video.get('avg_frame_rate', '0/1').split('/'))
            if not den or abs(num / den - fps) > .05:
                failures.append('frame_rate')
        except (ValueError, ZeroDivisionError):
            failures.append('frame_rate')
    mean_db = None
    if audio and require_audible:
        volume = _run([_tool('ffmpeg'), '-nostdin', '-hide_banner', '-i', str(Path(path).resolve()),
                       '-vn', '-af', 'volumedetect', '-f', 'null', '-'], timeout=max(60, int(duration * 2)))
        match = re.search(r'mean_volume:\s*(-?[\d.]+|-inf) dB', volume.stderr)
        if match:
            mean_db = float(match.group(1))
        if mean_db is None or not math.isfinite(mean_db) or mean_db < -60:
            failures.append('silent_audio')
    return {'passed': not failures, 'failures': failures,
            'duration_seconds': info['duration'], 'width': video.get('width') if video else None,
            'height': video.get('height') if video else None, 'has_audio': bool(audio),
            'mean_volume_db': mean_db if mean_db is not None and math.isfinite(mean_db) else None,
            'size_bytes': info['size_bytes']}


def _validate_subtitles(path, duration):
    text = path.read_text(encoding='utf-8-sig')
    if len(text) > 1_000_000 or '\x00' in text:
        raise ValueError('Invalid subtitle file')
    pattern = r'(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})'
    timings = re.findall(pattern, text)
    if not timings:
        raise ValueError('Expected UTF-8 SRT subtitles')
    last = 0
    for values in timings:
        h, m, s, ms, eh, em, es, ems = map(int, values)
        if max(m, s, em, es) > 59:
            raise ValueError('Invalid subtitle timestamp')
        start = h * 3600 + m * 60 + s + ms / 1000
        end = eh * 3600 + em * 60 + es + ems / 1000
        if start < last or end <= start or end > duration + .25:
            raise ValueError('Subtitle timing outside narration or overlapping')
        last = end
    return text


def render_video(root, clips, narration, output, *, approved=False, subtitles=None, config=None):
    if approved is not True:
        raise PermissionError('Exact media render approval required')
    cfg = config or RenderConfig()
    cfg.validate()
    if not isinstance(clips, (list, tuple)) or not 1 <= len(clips) <= 50:
        raise ValueError('Supply 1 to 50 approved local video clips')
    inputs = [scoped_path(root, c) for c in clips]
    voice = scoped_path(root, narration)
    destination = scoped_path(root, output, must_exist=False)
    if destination.suffix.lower() != '.mp4':
        raise ValueError('Output must be MP4')
    if destination.exists():
        raise FileExistsError('Refusing to overwrite an existing artifact')
    voice_info = probe_media(voice)
    if not any(s.get('codec_type') == 'audio' for s in voice_info['streams']):
        raise RenderError('Narration has no audio stream')
    duration = voice_info['duration']
    if duration > cfg.max_duration_seconds:
        raise RenderError('Narration exceeds render duration limit')
    for clip in inputs:
        if not any(s.get('codec_type') == 'video' for s in probe_media(clip)['streams']):
            raise RenderError('Approved clip has no video stream')
    caption = _validate_subtitles(scoped_path(root, subtitles), duration) if subtitles else None
    ffmpeg = _tool('ffmpeg')
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Intermediates stay in the output directory and never leak into published artifacts.
    with tempfile.TemporaryDirectory(prefix='.zoya-render-', dir=destination.parent) as tmp:
        tmp = Path(tmp)
        normalized = []
        vf = (f'scale={cfg.width}:{cfg.height}:force_original_aspect_ratio=decrease,'
              f'pad={cfg.width}:{cfg.height}:(ow-iw)/2:(oh-ih)/2:color=black,'
              f'setsar=1,fps={cfg.fps},format=yuv420p')
        for index, clip in enumerate(inputs):
            name = f'clip-{index:03}.mp4'
            _run([ffmpeg, '-nostdin', '-v', 'error', '-i', str(clip), '-map', '0:v:0',
                  '-an', '-vf', vf, '-c:v', 'libx264', '-preset', 'fast', '-crf', str(cfg.crf),
                  '-threads', '2', name], cwd=tmp, timeout=cfg.timeout_seconds)
            normalized.append(name)
        (tmp / 'clips.txt').write_text(''.join(f"file '{n}'\n" for n in normalized), encoding='utf-8')
        args = [ffmpeg, '-nostdin', '-v', 'error', '-stream_loop', '-1', '-f', 'concat',
                '-safe', '1', '-i', 'clips.txt', '-i', str(voice), '-map', '0:v:0', '-map', '1:a:0']
        if caption:
            (tmp / 'subtitle.srt').write_text(caption, encoding='utf-8')
            # Libass font size uses its reference canvas. Avoid mobile UI edges with 12% bottom margin.
            margin = 35  # 12% of libass default 288px reference height
            args += ['-vf', f"subtitles=subtitle.srt:original_size={cfg.width}x{cfg.height}:force_style='FontName=Arial,FontSize=18,Outline=2,Shadow=0,Alignment=2,MarginV={margin},MarginL=20,MarginR=20'"]
        args += ['-t', str(duration), '-c:v', 'libx264', '-preset', 'fast', '-crf', str(cfg.crf),
                 '-pix_fmt', 'yuv420p', '-r', str(cfg.fps), '-threads', '2', '-c:a', 'aac',
                 '-b:a', '192k', '-ar', '48000', '-movflags', '+faststart', 'render.mp4']
        _run(args, cwd=tmp, timeout=cfg.timeout_seconds)
        rendered = tmp / 'render.mp4'
        quality = qa_video(rendered, width=cfg.width, height=cfg.height, duration=duration, fps=cfg.fps)
        if not quality['passed']:
            raise RenderError('Export QA failed: ' + ', '.join(quality['failures']))
        # Exclusive create prevents overwriting an artifact created while render was running.
        with destination.open('xb') as target, rendered.open('rb') as source:
            try:
                shutil.copyfileobj(source, target)
                target.flush()
                os.fsync(target.fileno())
            except BaseException:
                target.close()
                destination.unlink(missing_ok=True)
                raise
    return {'output': str(destination.relative_to(Path(root).resolve())), 'qa': quality,
            'subtitles_burned': bool(caption), 'clip_count': len(inputs),
            'visuals_looped_to_narration': True, 'uploaded': False}
