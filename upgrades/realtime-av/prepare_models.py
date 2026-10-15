"""Prepare local models explicitly; not run by listener startup.

Silero's v6 sequence ONNX graph ships inside the pinned Faster-Whisper wheel.
The speech model can be an existing Zoya directory or an explicit opt-in download.
"""
import argparse
import hashlib
import importlib.metadata
import json
import shutil
import os
from pathlib import Path


def prepare_vad(destination: Path, existing_source: Path | None = None):
    if existing_source is None:
        dist = importlib.metadata.distribution('faster-whisper')
        source = Path(dist.locate_file('faster_whisper/assets/silero_vad_v6.onnx'))
        origin = f'faster-whisper=={dist.version}'
    else:
        source = existing_source.resolve()
        origin = 'existing-local-Silero-asset'
    if not source.is_file():
        raise RuntimeError('Pinned Faster-Whisper ONNX asset missing')
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = source.read_bytes()
    expected = hashlib.sha256(data).hexdigest()
    if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() != expected:
        raise FileExistsError('Different VAD model already exists; use a new destination')
    shutil.copyfile(source, destination)
    from voice_listener import SileroOnnx
    import numpy as np
    vad = SileroOnnx(str(destination))
    probability = vad.probability(np.zeros(512, dtype=np.float32))
    return {'path': str(destination.resolve()), 'sha256': expected,
            'source_distribution': origin,
            'silence_probability': probability}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--vad-output', type=Path, default=Path('models/silero_vad_v6.onnx'))
    p.add_argument('--vad-source',type=Path,help='Reuse an explicitly selected existing local Silero ONNX graph')
    p.add_argument('--whisper-source', type=Path,
                   help='Reuse an existing local model; no copy/download is performed')
    p.add_argument('--download-model', choices=['base.en', 'base', 'small'],
                   help='Explicitly download a public Systran model from Hugging Face')
    p.add_argument('--model-output', type=Path, default=Path('models/whisper'))
    args = p.parse_args()
    if args.whisper_source and args.download_model:
        p.error('Choose existing local model OR explicit download')
    result = {'vad': prepare_vad(args.vad_output,args.vad_source)}
    if args.whisper_source:
        if not (args.whisper_source/'model.bin').is_file():
            raise ValueError('Existing Whisper directory has no model.bin')
        result['whisper_path'] = str(args.whisper_source.resolve())
    if args.download_model:
        os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
        from huggingface_hub import snapshot_download
        result['whisper_path'] = snapshot_download(repo_id='Systran/faster-whisper-'+args.download_model,
            local_dir=str(args.model_output), allow_patterns=['config.json','model.bin','tokenizer.json',
                                                             'preprocessor_config.json','vocabulary.*'])
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
