"""Read-only Zoya audit; fresh secret-backed checks execute only on the selected desktop."""
import argparse
import hashlib
import importlib
import json
import sys
import time
import uuid
from pathlib import Path


def assess_health(health, now=None, max_age=15):
    now = time.time() if now is None else now
    heartbeat = health.get('heartbeat')
    fresh = isinstance(heartbeat, (int, float)) and 0 <= now - heartbeat <= max_age
    return {
        'heartbeat_fresh': fresh,
        'microphone_active': fresh and health.get('microphone_open') is True,
        'stt_backend': health.get('stt_backend', 'unknown'),
        'voice_state': health.get('voice_state', 'unknown'),
        'stt_error': health.get('last_stt_error'),
        'voice_error': health.get('last_voice_error'),
        'physical_microphone_accuracy_verified': False,
        'human_voice_quality_verified': False,
    }


def assess_tool_response(response):
    result = response.get('result') or {}
    observed = (response.get('status') == 'done'
                and result.get('toolName') == 'run_subagent'
                and result.get('success') is True)
    return {
        'subagent_invocation_observed': observed,
        'approval_pending': response.get('status') == 'pending',
        'business_outcome_verified': False,
        'all_workers_verified': False,
        'automatic_retry': False,
    }


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def hashes_match(root, entries):
    root = Path(root).resolve()
    if not isinstance(entries, dict) or not entries:
        return False
    for name, digest in entries.items():
        if not isinstance(name, str) or not isinstance(digest, str):
            return False
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return False
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            return False
    return True


def audit(root):
    root = Path(root).resolve()
    if not (root / 'zoya_desktop.py').is_file() or not (root / 'app.py').is_file():
        raise ValueError('Selected root is not a Zoya installation')
    sys.path.insert(0, str(root))
    report = {'started_at': time.time(), 'read_only': True, 'fresh_tts_tested': False,
              'new_agent_workloads_submitted': False, 'copilot_invocation_verified': False,
              'codex_cli_invocation_verified': False}
    for label, file in [('desktop', 'data/desktop-health.json'),
                        ('deployment', 'data/vision-live-deployment.json')]:
        try:
            data = read_json(root / file)
            if label == 'desktop':
                report[label] = assess_health(data)
            else:
                report['source_hashes_match'] = hashes_match(root, data.get('files'))
        except (OSError, ValueError) as error:
            report[label] = {'verified': False, 'error_class': type(error).__name__}
    try:
        desktop = importlib.import_module('zoya_desktop')
        response = read_json_bytes(desktop.http('/api/conversation', {
            'message': 'Zoya tumhara naam kya hai?',
            'request_id': str(uuid.uuid4()), 'voice': False}))
        report['conversation'] = {'reply_received': bool(response.get('reply')),
                                  'voice_requested': False}
    except Exception as error:
        report['conversation'] = {'verified': False, 'error_class': type(error).__name__}
    try:
        bridge = importlib.import_module('skales_bridge')
        with bridge.request('/api/cli/status') as response:
            status = json.load(response)
        report['skales'] = {key: status.get(key) for key in ('provider', 'model', 'version')}
        report['skales']['subagent_execution_verified'] = False
    except Exception as error:
        report['skales'] = {'verified': False, 'error_class': type(error).__name__}
    report['complete_end_to_end_verified'] = False
    report['finished_at'] = time.time()
    return report


def read_json_bytes(raw):
    return json.loads(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, help='Exact live Zoya directory')
    parser.add_argument('--output', help='Optional report inside that installation')
    args = parser.parse_args()
    result = audit(args.root)
    if args.output:
        output = Path(args.output).resolve()
        if not output.is_relative_to(Path(args.root).resolve()):
            raise ValueError('Report output must stay inside the selected installation')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
