"""Explicitly approved ElevenLabs TTS; no native fallback or paid retries."""
from dataclasses import dataclass
from pathlib import Path
import hashlib
import http.client
import json
import os
import re
import tempfile


class VoiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class VoiceConfig:
    role: str
    voice_id: str
    model_id: str
    language: str = "mixed-hi-ur-en"

    @classmethod
    def from_env(cls, role="zoya"):
        if role not in ("zoya", "narration"):
            raise ValueError("Unknown voice role")
        prefix = "ZOYA" if role == "zoya" else "NARRATION"
        return cls(role, os.environ.get(prefix + "_VOICE_ID", ""),
                   os.environ.get(prefix + "_VOICE_MODEL_ID", ""),
                   "mixed-hi-ur-en" if role == "zoya" else "en-US")

    def validate(self):
        if self.role not in ("zoya", "narration"):
            raise ValueError("Unknown voice role")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", self.voice_id):
            raise ValueError("Select an explicit voice ID")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", self.model_id):
            raise ValueError("Select an explicit model ID")
        expected = "mixed-hi-ur-en" if self.role == "zoya" else "en-US"
        if self.language != expected:
            raise ValueError("Voice role language mismatch")


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


class ElevenLabsVoice:
    """Caller must obtain one-time exact task approval before generate()."""
    def __init__(self, root, config, connection_factory=None):
        self.root = Path(root).resolve()
        self.config = config
        self.connection_factory = connection_factory or http.client.HTTPSConnection

    def readiness(self):
        missing = []
        if not os.environ.get("ELEVENLABS_API_KEY", "").strip():
            missing.append("ELEVENLABS_API_KEY")
        try:
            self.config.validate()
        except ValueError:
            missing.append("selected_voice_and_model")
        return {"configured": not missing, "missing": missing,
                "provider_verified": False, "human_quality_verified": False,
                "role": self.config.role, "language": self.config.language,
                "automatic_fallback": False}

    def _directory(self):
        path = self.root / "data" / "voice"
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise VoiceError("Voice output path escapes project")
        return path

    def plan(self, text, output_name):
        self.config.validate()
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError("Voice text must contain 1–4000 characters")
        if re.search(r"(?:api[_-]?key|bearer|password)\s*[:=]|sk-[A-Za-z0-9]{16}", text, re.I):
            raise ValueError("Secrets must not enter voice requests")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,90}\.mp3", output_name):
            raise ValueError("Output must be a scoped MP3 filename")
        self._directory()
        body = {"text": text, "model_id": self.config.model_id}
        # Do not force one language on code-switching Zoya, nor unsupported v2 language enforcement.
        if self.config.role == "narration" and self.config.model_id != "eleven_multilingual_v2":
            body["language_code"] = "en"
        payload = {"provider": "elevenlabs", "host": "api.elevenlabs.io",
                   "role": self.config.role, "voice_id": self.config.voice_id,
                   "language": self.config.language, "body": body,
                   "output_format": "mp3_44100_128", "output_name": output_name}
        return {"payload": payload, "hash": hashlib.sha256(_canonical(payload)).hexdigest(),
                "paid_request": True, "publishing_allowed": False,
                "quality_verified": False}

    def generate(self, text, output_name, approved_hash):
        plan = self.plan(text, output_name)
        if not isinstance(approved_hash, str) or approved_hash != plan["hash"]:
            raise VoiceError("Exact voice payload approval required")
        key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
        if not key:
            raise VoiceError("ELEVENLABS_API_KEY not configured")
        directory = self._directory()
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / output_name
        if destination.exists():
            raise VoiceError("Voice output already exists; choose a new reviewed filename")
        attempt = directory / (plan["hash"] + ".attempt.json")
        try:
            with attempt.open("x", encoding="utf-8") as f:
                json.dump({"hash": plan["hash"], "state": "attempted_no_automatic_retry"}, f)
                f.flush()
                os.fsync(f.fileno())
        except FileExistsError:
            raise VoiceError("Voice request already attempted; reconcile before a new approval") from None
        connection = None
        temporary = None
        try:
            connection = self.connection_factory("api.elevenlabs.io", timeout=45)
            endpoint = "/v1/text-to-speech/" + self.config.voice_id + "?output_format=mp3_44100_128"
            connection.request("POST", endpoint, body=_canonical(plan["payload"]["body"]),
                               headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"})
            response = connection.getresponse()
            if response.status != 200:
                # Never follow redirects or log provider bodies/credentials.
                raise VoiceError("Voice provider returned HTTP " + str(response.status) + "; no automatic retry")
            if response.getheader("Content-Type", "").split(";")[0].lower() not in ("audio/mpeg", "audio/mp3"):
                raise VoiceError("Voice provider did not return MP3 audio")
            audio = response.read(20 * 1024 * 1024 + 1)
            if len(audio) > 20 * 1024 * 1024 or len(audio) < 4:
                raise VoiceError("Voice response size outside allowed bounds")
            if not (audio.startswith(b"ID3") or (audio[0] == 255 and audio[1] & 224 == 224)):
                raise VoiceError("Voice response has invalid MP3 header")
            fd, temporary = tempfile.mkstemp(prefix="voice-", suffix=".partial", dir=directory)
            with os.fdopen(fd, "wb") as f:
                f.write(audio)
                f.flush()
                os.fsync(f.fileno())
            # Atomic no-overwrite publish; another request cannot silently replace audio.
            os.link(temporary, destination)
            os.unlink(temporary)
            temporary = None
            attempt.write_text(json.dumps({"hash": plan["hash"], "state": "completed",
                                           "output": output_name}), encoding="utf-8")
            return {"status": "generated", "path": str(destination), "payload_hash": plan["hash"],
                    "relative_path": destination.relative_to(self.root).as_posix(),
                    "bytes": len(audio), "quality_verified": False}
        except VoiceError:
            raise
        except Exception:
            raise VoiceError("Voice request or local save failed; reconcile provider usage, do not retry automatically") from None
        finally:
            if connection is not None:
                connection.close()
            if temporary:
                Path(temporary).unlink(missing_ok=True)


ZOYA_SAMPLE = "Ji boss, main Zoya hoon. Aapka task samajh gayi. Pehle plan dikhaungi, phir aapki approval ke baad kaam shuru karungi."
ZOYA_DELIVERY = "Warm, sweet, respectful Hindi/Urdu with familiar English words; address owner as Ji boss. Short sentences, calm pace, no exaggerated American accent."
