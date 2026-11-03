"""Pinned downstream Console admission and installed npm byte provenance (no service I/O)."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import tarfile

COMPONENT_SHA = '72c286da0c53feb6820c54e4b283d2c752b3b4d7198db1b6f7d38e405b495612'
LOCK_SHA = '91200cca290e997ef56fe577516460982b9cb318fc47e8a519b2ccbaf401974f'
BINDING_SHA = 'de6278ed27fa042bc298e30afa40364bb1fb419ec3583fa0507958c3b8514ae7'
SOURCE_COMMIT = '91bf8b91a8bcb1cf2b822d3eb38e2e11d301ad3f'
COMPONENT_FILE = 'taki-console-0.1.0-candidate.1.tgz'
PACKAGE = 'node_modules/@koishijs/plugin-console'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def tar_files(data, identical_aliases=None):
    files = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or '\\' in member.name:
                raise ValueError('Unsafe npm member')
            if member.isdir():
                continue
            if len(path.parts) < 2:
                raise ValueError('Missing npm package root')
            name = '/'.join(path.parts[1:])
            if not member.isfile():
                raise ValueError('Nonregular or duplicate npm member')
            content = archive.extractfile(member).read()
            if name in files:
                # Some locked registry tars contain both package/./x and package/x.
                # Record only identical aliases after SRI verification; component tars
                # and conflicting duplicate bytes remain rejected.
                if identical_aliases is None or files[name] != content:
                    raise ValueError('Nonregular or duplicate npm member')
                identical_aliases.append(dict(member=member.name, path=name, sha256=digest(content)))
            files[name] = content
    return files


def verify_downstream(gateway):
    """Recheck fixed tar, lock, metadata and every installed component file; never patch."""
    binding = read(gateway / 'vendor/console-candidate.json')
    if digest(json.dumps(binding, sort_keys=True, separators=(',', ':')).encode()) != BINDING_SHA:
        raise ValueError('Unreviewed downstream source binding')
    data = (gateway / 'vendor' / COMPONENT_FILE).read_bytes()
    integrity = 'sha512-' + base64.b64encode(hashlib.sha512(data).digest()).decode()
    expected = dict(file=COMPONENT_FILE, version='0.1.0-candidate.1', package_version='5.30.10-taki.1',
                    sha256=COMPONENT_SHA, integrity=integrity, size=634221)
    if digest(data) != COMPONENT_SHA or binding['component'] != expected or binding['source_commit'] != SOURCE_COMMIT:
        raise ValueError('Unknown or altered downstream Console')
    if binding['legacy_byte_patch'] != 'forbidden':
        raise ValueError('Downstream Console must not be byte patched')
    lock = read(gateway / 'package-lock.json')
    if digest(json.dumps(lock, sort_keys=True, separators=(',', ':')).encode()) != LOCK_SHA:
        raise ValueError('Unreviewed downstream gateway lock')
    package_json = read(gateway / 'package.json')
    if any(package_json.get(key, {}) != lock['packages'][''].get(key, {})
           for key in ('dependencies', 'devDependencies')):
        raise ValueError('Gateway package and lock differ')
    spec = 'file:vendor/' + COMPONENT_FILE
    if (read(gateway / 'package.json')['dependencies']['@koishijs/plugin-console'] != spec
            or lock['packages']['']['dependencies']['@koishijs/plugin-console'] != spec
            or lock['packages'][PACKAGE]['resolved'] != spec
            or lock['packages'][PACKAGE]['integrity'] != integrity
            or lock['packages'][PACKAGE]['version'] != expected['package_version']):
        raise ValueError('Downstream Console lock binding mismatch')
    for name, row in binding['external_dependencies'].items():
        actual = lock['packages']['node_modules/' + name]
        if any(actual.get(key) != value for key, value in row.items()):
            raise ValueError('External Console dependency mismatch')
    files = tar_files(data)
    installed = gateway / PACKAGE
    actual = {p.relative_to(installed).as_posix() for p in installed.rglob('*') if p.is_file()}
    if actual != set(files) or any(p.is_symlink() for p in installed.rglob('*')):
        raise ValueError('Installed Console member set differs')
    for name, content in files.items():
        if (installed / name).read_bytes() != content:
            raise ValueError('Installed Console bytes differ: ' + name)
    identity = json.loads(files['TAKI-CONSOLE.json'])
    if (identity['legacy_byte_patch'] != 'forbidden; source patch already applied'
            or identity['reconnect_source_after'] != 'cf21a5e49c75deeda40a064e9dba2f65933a97c9357beda743337dee4be60e49'
            or identity['version'] != expected['version']):
        raise ValueError('Downstream source reconnect binding mismatch')
    for name in ('LICENSE', 'TAKI-MODIFICATIONS.txt', 'licenses/index.json', 'licenses/NOTICE.txt'):
        if name not in files:
            raise ValueError('Missing downstream materials')
    for row in json.loads(files['licenses/index.json']):
        if digest(files['licenses/' + row['path']]) != row['sha256']:
            raise ValueError('Downstream license hash mismatch')
    return dict(kind='taki-downstream', component_sha256=COMPONENT_SHA, source_commit=SOURCE_COMMIT,
                lock_canonical_sha256=LOCK_SHA, legacy_byte_patch='not applied',
                complete_corresponding_source=False, product_release=False)


def installed_npm_materials(gateway):
    """Verify registry tarballs from npm's integrity cache against the actual locked install.

    Cache only supplies bytes: each tarball is rehashed against the committed SRI.
    The record distinguishes absent notice text from a license declaration.
    """
    cache = Path(os.environ.get('npm_config_cache', Path.home() / '.npm')) / '_cacache/content-v2'
    result = []
    lock = read(gateway / 'package-lock.json')
    for location, row in sorted(lock['packages'].items()):
        if not location.startswith('node_modules/') or row.get('link') or location == PACKAGE:
            continue
        algorithm, encoded = row['integrity'].split('-', 1)
        if algorithm != 'sha512' or not row['resolved'].startswith('https://registry.npmjs.org/'):
            raise ValueError('Unsupported locked npm source')
        checksum = base64.b64decode(encoded, validate=True).hex()
        data = (cache / algorithm / checksum[:2] / checksum[2:4] / checksum[4:]).read_bytes()
        if hashlib.sha512(data).hexdigest() != checksum:
            raise ValueError('npm cache integrity mismatch')
        aliases = []
        try:
            files = tar_files(data, aliases)
        except ValueError as error:
            raise ValueError(location + ': ' + str(error)) from error
        package = gateway / location
        # npm may install nested dependencies and bin links; they are checked by their own owner.
        actual = {p.relative_to(package).as_posix() for p in package.rglob('*')
                  if p.is_file() and 'node_modules' not in p.relative_to(package).parts}
        if actual != set(files):
            raise ValueError('Installed npm member set differs: ' + location)
        for name, content in files.items():
            if (package / name).is_symlink() or (package / name).read_bytes() != content:
                raise ValueError('Installed npm bytes differ: ' + location + '/' + name)
        metadata = json.loads(files['package.json'])
        if metadata['version'] != row['version']:
            raise ValueError('Installed npm version mismatch')
        notices = {name: digest(content) for name, content in files.items()
                   if any(word in PurePosixPath(name).name.lower() for word in ('license', 'notice', 'copying', 'copyright'))}
        result.append(dict(location=location, name=metadata['name'], version=row['version'],
                           resolved=row['resolved'], integrity=row['integrity'], tar_sha256=digest(data),
                           repository=metadata.get('repository'), git_head=metadata.get('gitHead'),
                           license_declaration=metadata.get('license'), notices=notices,
                           source_sufficiency='not certified', dev=bool(row.get('dev')), identical_tar_aliases=aliases,
                           files={name: digest(content) for name, content in files.items()}))
    return dict(lock_canonical_sha256=LOCK_SHA, packages=result,
                missing_notice_packages=[r['name'] for r in result if not r['notices']],
                complete_corresponding_source=False, product_release=False)
