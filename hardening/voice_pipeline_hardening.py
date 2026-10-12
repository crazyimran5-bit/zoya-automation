"""Deterministic formatting; no model calls and no semantic transcript guesses."""
import hashlib
import json
import re
import unicodedata

MODEL_ID = 'eleven_multilingual_v2'
VOICE_SETTINGS = {
    'stability': 0.35,
    'similarity_boost': 0.85,
    'style': 0.20,
    'use_speaker_boost': True,
}

def normalize_stt(text):
    if not isinstance(text, str):
        raise ValueError('Transcript must be text')
    text = unicodedata.normalize('NFC', text)
    # Preserve words, script, digits, punctuation and command/confirmation tokens.
    text = ''.join(' ' if unicodedata.category(c) == 'Cc' else c for c in text)
    return re.sub(r'\s+', ' ', text).strip()

def format_tts_text(text):
    text = normalize_stt(text)
    # Preserve decimal numbers, URLs and existing meaningful punctuation.
    text = re.sub(r'\s+([,;!?])', r'\1', text)
    text = re.sub(r'(?<=[,;!?])(?=[A-Za-z\u0900-\u097f\u0600-\u06ff])', ' ', text)
    text = re.sub(r'^ji\s+boss\s*[,;:]?\s*', 'Ji boss, ', text, flags=re.I)
    if text and text[-1] not in '.!?।':
        text += '.'
    return text

def build_tts_payload(text):
    return {'text': format_tts_text(text), 'model_id': MODEL_ID,
            'voice_settings': dict(VOICE_SETTINGS)}

def voice_cache_key(voice_id, text):
    identity = {'voice_id': voice_id, 'payload': build_tts_payload(text)}
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()[:32]
