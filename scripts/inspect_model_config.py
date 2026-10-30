# L3: Read-only, allowlisted diagnostics for protected model configuration files.
# Effects: Reads one bounded JSON file; no dotenv, imports of the bot, network or writes.
"""Inspect model metadata without printing credentials, bindings or raw errors."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat


MAX_BYTES = 131072
SCHEMAS = {'taki-model-config/1', 'taki-model-config/2', 'taki-model-config/3'}
PROVIDERS = {'deepseek', 'openai', 'openrouter'}


class InspectionError(ValueError):
    pass


def inspect(path):
    nofollow = getattr(os, 'O_NOFOLLOW', 0)
    nonblock = getattr(os, 'O_NONBLOCK', 0)
    if not nofollow or not nonblock:
        raise InspectionError('safe_open_unsupported')
    flags = (os.O_RDONLY | nofollow
             | nonblock | getattr(os, 'O_BINARY', 0))
    # Reject special files before opening (including named pipes).
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
        raise InspectionError('configuration_file_invalid')
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES
                or (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino)):
            raise InspectionError('configuration_file_invalid')
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise InspectionError('configuration_file_too_large')
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise InspectionError('configuration_json_invalid') from None
    if not isinstance(data, dict) or not isinstance(data.get('schema'), str) or data['schema'] not in SCHEMAS:
        raise InspectionError('configuration_schema_invalid')
    profile = data.get('profile')
    if not isinstance(profile, dict):
        raise InspectionError('configuration_profile_invalid')
    secret = data.get('secret')

    return {
        'schema': data['schema'],
        'fingerprint': hashlib.sha256(raw).hexdigest(),
        'provider': profile.get('provider') if isinstance(profile.get('provider'), str) and profile['provider'] in PROVIDERS else None,
        'model_present': isinstance(profile.get('model'), str) and bool(profile['model']),
        'config_version_present': isinstance(profile.get('config_version'), str) and bool(profile['config_version']),
        'inline_key_present': isinstance(secret, str) and bool(secret),
        'group_override_count': len(data['groups']) if isinstance(data.get('groups'), dict) else 0,
        'binding_count': len(data['bindings']) if isinstance(data.get('bindings'), list) else 0,
        'posix_group_other_mode_clear': info.st_mode & 0o077 == 0 if os.name == 'posix' else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = inspect(args.config)
    except InspectionError as exc:
        result = {'error': str(exc)}
    except (OSError, ValueError, TypeError, RecursionError):
        # Exception text can contain file contents or sensitive path fragments.
        result = {'error': 'configuration_read_failed'}
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return int('error' in result)


if __name__ == '__main__':
    raise SystemExit(main())
