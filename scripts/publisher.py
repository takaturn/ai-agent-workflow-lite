#!/usr/bin/env python3
"""Publisher prototype: a protected policy/approval key, typed IPC and fixed transports.
Run the requester through publisher-sandbox.py. An unrestricted host requester can
still bypass this service; installation alone does not resolve that boundary.
"""
import argparse
import base64
import contextlib
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

MAX_REQUEST = 24 * 1024 * 1024
MAX_TEXT = 128 * 1024
TOOL_ENV = None
SCANNER = 'gitleaks'
LAST_FAILURE = None
OID = re.compile(r'[0-9a-f]{40}|[0-9a-f]{64}')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def decode(raw):
    return json.loads(raw, object_pairs_hook=unique)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def run(command, **kwargs):
    global LAST_FAILURE
    if TOOL_ENV is not None and 'env' not in kwargs:
        kwargs['env'] = TOOL_ENV
    try:
        return subprocess.run(command, capture_output=True, check=True, timeout=60, **kwargs)
    except subprocess.CalledProcessError as error:
        operations = {'push','fetch','init','ls-remote','merge-base','stdin','api','bundle','fsck','cat-file','rev-list'}
        operation = next((item for item in command if item in operations), 'other')
        diagnostic = (error.stderr or b'').lower()
        categories = [name for name in ('workflow','authentication','publickey','memory','timeout') if name.encode() in diagnostic]
        LAST_FAILURE = {'tool':Path(command[0]).name, 'operation':operation, 'exit':error.returncode, 'categories':categories}
        raise


def scan(raw):
    raw.decode('utf-8')
    if b'\0' in raw or len(raw) > MAX_REQUEST:
        raise ValueError('unsupported content')
    with tempfile.TemporaryDirectory() as directory:
        env = {k: v for k, v in (TOOL_ENV or os.environ).items() if k not in ('GITLEAKS_CONFIG', 'GITLEAKS_CONFIG_TOML')}
        run([SCANNER, 'stdin', '--no-banner', '--redact=100', '--ignore-gitleaks-allow',
             '--gitleaks-ignore-path', directory+'/none', '--timeout', '30'],
            input=raw, cwd=directory, env=env)


def policy_read(path):
    path = Path(path).resolve(strict=True)
    raw = path.read_bytes()
    data = decode(raw)
    if set(data) != {'target', 'git_remote', 'git_ref', 'api', 'subject'}:
        raise ValueError('invalid policy')
    if (not isinstance(data['target'], str) or not re.fullmatch(r'[A-Za-z0-9-]+/[A-Za-z0-9_.-]+', data['target'])
            or data['target'].split('/')[-1] in ('.', '..')):
        raise ValueError('invalid repository')
    if not re.fullmatch(r'refs/heads/[A-Za-z0-9_/#.-]+', data['git_ref']):
        raise ValueError('invalid branch')
    if not isinstance(data['subject'], str) or not data['subject']:
        raise ValueError('invalid subject')
    # No caller-chosen path/method. Updates without server-side CAS are disabled.
    for operation, rule in data['api'].items():
        if (not re.fullmatch(r'[a-z_]+', operation) or set(rule) != {'method', 'path', 'fields'} or
                rule['method'] != 'POST' or not rule['path'].startswith('repos/'+data['target']+'/') or
                not isinstance(rule['fields'], list) or set(rule['fields']) != {'body'} or
                not re.fullmatch(r'repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/issues/[1-9][0-9]*/comments', rule['path'])):
            raise ValueError('unsupported API policy')
    return data, digest(raw)


def validate(request, policy, policy_hash):
    if set(request) != {'version', 'operation', 'target', 'subject', 'nonce', 'expires', 'policy', 'payload'}:
        raise ValueError('unknown request fields')
    if (type(request['version']) is not int or request['version'] != 1 or request['target'] != policy['target'] or
            request['subject'] != policy['subject'] or request['policy'] != policy_hash or
            not isinstance(request['nonce'], str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,100}', request['nonce']) or
            type(request['expires']) is not int or not time.time() < request['expires'] <= time.time()+300):
        raise ValueError('scope/expiry mismatch')
    payload = request['payload']
    if not isinstance(payload, dict):
        raise ValueError('invalid payload')
    if request['operation'] == 'git_push':
        if (set(payload) != {'oid', 'expected_oid', 'bundle'} or
                not OID.fullmatch(payload['oid']) or not OID.fullmatch(payload['expected_oid']) or
                len(payload['oid']) != len(payload['expected_oid']) or set(payload['oid']) == {'0'}):
            raise ValueError('invalid Git payload')
        bundle = base64.b64decode(payload['bundle'], validate=True)
        if not bundle or len(bundle) > 16*1024*1024:
            raise ValueError('bundle size')
    elif request['operation'] in policy['api']:
        if set(payload) != {'body'} or not isinstance(payload['body'], str) or len(payload['body'].encode()) > MAX_TEXT:
            raise ValueError('invalid API fields')
    else:
        raise ValueError('operation disabled')


IMPORT_TOOL = Path(__file__).resolve()
TRANSPORT_INSTALLER = Path(__file__).resolve().with_name('install-gitleaks-hook.py')


def import_bundle(bundle, policy, oid, directory):
    """Untrusted Git input and scanner execute without host home/network/credentials."""
    source = Path(directory)/'input.bundle'
    output = Path(directory)/'output'; output.mkdir()
    source.write_bytes(bundle)
    tool = IMPORT_TOOL
    scanner = SCANNER if Path(SCANNER).is_absolute() else run(['which', 'gitleaks']).stdout.decode().strip()
    command = ['bwrap', '--unshare-all', '--die-with-parent', '--new-session', '--clearenv',
        '--ro-bind', '/usr', '/usr', '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/lib', '/lib',
        '--symlink', 'usr/lib64', '/lib64', '--proc', '/proc', '--dev', '/dev',
        '--size', str(64*1024*1024), '--tmpfs', '/tmp', '--dir', '/home/agent',
        '--setenv', 'HOME', '/home/agent', '--setenv', 'PATH', '/usr/bin:/bin',
        '--setenv', 'GIT_CONFIG_NOSYSTEM', '1', '--setenv', 'GIT_CONFIG_GLOBAL', '/dev/null',
        '--setenv', 'GIT_NO_REPLACE_OBJECTS', '1', '--setenv', 'GIT_GRAFT_FILE', '/dev/null',
        '--ro-bind', str(source), '/input.bundle', '--ro-bind', str(tool), '/tool.py',
        '--ro-bind', scanner, '/gitleaks', '--bind', str(output), '/output',
        '--cap-drop', 'ALL', '--', '/usr/bin/python3', '/tool.py', 'import',
        '--ref', policy['git_ref'], '--oid', oid]
    run(command)
    clean = output/'checked.bundle'
    if not clean.is_file() or clean.stat().st_size > 16*1024*1024:
        raise ValueError('invalid checked bundle')
    return clean


def importer(ref, oid):
    # Namespace-only entry point. Never accepts remote URLs, shell or Git options.
    import resource
    resource.setrlimit(resource.RLIMIT_FSIZE, (32*1024*1024, 32*1024*1024))
    resource.setrlimit(resource.RLIMIT_AS, (512*1024*1024, 16*1024*1024*1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    if not re.fullmatch(r'refs/heads/[A-Za-z0-9_/#.-]+', ref) or not OID.fullmatch(oid):
        raise ValueError('invalid import identity')
    def git(*args):
        return run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'protocol.allow=never',
                    '-c', 'protocol.file.allow=always', *args]).stdout
    heads = git('bundle', 'list-heads', '/input.bundle').decode().splitlines()
    if heads != [oid+' '+ref]:
        raise ValueError('unexpected bundle references')
    git('init', '-q', '--bare', '--template=', '/tmp/repo.git')
    git('-C', '/tmp/repo.git', 'fetch', '-q', '--no-tags', '/input.bundle', ref+':refs/heads/submission')
    git('-C', '/tmp/repo.git', 'fsck', '--strict', '--no-reflogs')
    objects = git('-C', '/tmp/repo.git', 'rev-list', '--objects', '--no-object-names', oid).decode().splitlines()
    if len(objects) > 10000:
        raise ValueError('object limit')
    total = 0; payload = bytearray()
    for obj in objects:
        kind = git('-C', '/tmp/repo.git', 'cat-file', '-t', obj).strip()
        if kind not in (b'commit', b'tree', b'blob'):
            raise ValueError('unsupported object')
        raw = (git('-C', '/tmp/repo.git', 'ls-tree', '-r', '--full-tree', obj)
               if kind == b'tree' else git('-C', '/tmp/repo.git', 'cat-file', kind.decode(), obj))
        if kind == b'tree' and any(row.startswith(b'160000 ') for row in raw.splitlines()):
            raise ValueError('submodule unsupported')
        raw.decode('utf-8')
        if b'\0' in raw:
            raise ValueError('binary unsupported')
        total += len(raw)
        if total > 16*1024*1024:
            raise ValueError('content limit')
        payload.extend(raw); payload.extend(b'\n')
    def scanner_limit():
        # Go reserves address space during startup. Keep the Git importer at
        # 512 MiB, while the fixed scanner gets a 16 GiB virtual-address cap.
        resource.setrlimit(resource.RLIMIT_AS, (16*1024*1024*1024, 16*1024*1024*1024))
    run(['/gitleaks', 'stdin', '--no-banner', '--redact=100', '--ignore-gitleaks-allow',
         '--gitleaks-ignore-path', '/tmp/none', '--timeout', '30'], input=bytes(payload), cwd='/tmp', preexec_fn=scanner_limit,
        env=dict(os.environ, GOMEMLIMIT='256MiB'))
    git('-C', '/tmp/repo.git', 'bundle', 'create', '/output/checked.bundle', 'refs/heads/submission')


def dispatch(request, policy):
    if time.time() >= request['expires']:
        raise ValueError('approval expired')
    payload = request['payload']
    if request['operation'] != 'git_push':
        rule = policy['api'][request['operation']]
        scan(canonical(payload))
        # Fixed POST endpoint; no caller-selected command or environment.
        if time.time() >= request['expires']:
            raise ValueError('approval expired')
        api_env = dict(TOOL_ENV or os.environ, GH_HOST='github.com', GH_PROMPT_DISABLED='1')
        result = run(['gh', 'api', rule['path'], '--method', 'POST', '--input', '-'],
                     input=canonical(payload), env=api_env)
        response = decode(result.stdout)
        if response.get('body') != payload['body'] or not isinstance(response.get('id'), int):
            raise ValueError('publication read-back mismatch')
        read = decode(run(['gh', 'api', 'repos/'+policy['target']+'/issues/comments/'+str(response['id'])],
                          env=api_env).stdout)
        if read.get('body') != payload['body']:
            raise ValueError('publication read-back mismatch')
        return {'id': response['id']}
    with tempfile.TemporaryDirectory(prefix='publisher-import-') as directory:
        checked = import_bundle(base64.b64decode(payload['bundle'], validate=True), policy, payload['oid'], directory)
        # Only the validated canonical bundle is imported into a clean sender.
        sender = str(Path(directory)/'sender.git')
        clean_env = {k: v for k, v in (TOOL_ENV or os.environ).items() if not k.startswith('GIT_')}
        clean_env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                         GIT_NO_REPLACE_OBJECTS='1', GIT_GRAFT_FILE=os.devnull,
                         GIT_TERMINAL_PROMPT='0', GH_PROMPT_DISABLED='1',
                         GH_HOST='github.com', GIT_EXEC_PATH=(TOOL_ENV or os.environ).get('GIT_EXEC_PATH', '/usr/lib/git-core'))
        def git(*args):
            return run(['git', '-c', 'protocol.allow=never',
                        '-c', 'protocol.file.allow=always', '-c', 'protocol.https.allow=always',
                        '-c', 'protocol.ssh.allow=always',
                        '-c', 'core.sshCommand=ssh -F /dev/null -oBatchMode=yes -oStrictHostKeyChecking=yes -oForwardAgent=no',
                        '-c', 'http.followRedirects=false',
                        '-c', 'credential.helper=!gh auth git-credential', *args], env=clean_env).stdout
        git('init', '-q', '--bare', '--template=', sender)
        git('-C', sender, 'fetch', '-q', '--no-tags', str(checked), 'refs/heads/submission:refs/heads/submission')
        remote = policy['git_remote']
        if not (remote in ('https://github.com/'+policy['target'], 'https://github.com/'+policy['target']+'.git',
                           'ssh://git@github.com/'+policy['target']+'.git') or remote.startswith('file:///')):
            raise ValueError('unsupported configured remote')
        # force-with-lease supplies the expected old OID but is not permission
        # to force: ancestry must also hold when the old ref exists.
        previous = git('ls-remote', remote, policy['git_ref']).decode().strip().split()
        actual = previous[0] if previous else '0'*len(payload['oid'])
        if actual != payload['expected_oid']:
            raise ValueError('old ref changed')
        if set(actual) != {'0'}:
            git('-C', sender, 'merge-base', '--is-ancestor', actual, payload['oid'])
        run([sys.executable, str(TRANSPORT_INSTALLER)], cwd=sender, env=clean_env)
        if time.time() >= request['expires']:
            raise ValueError('approval expired')
        git('-C', sender, 'push', '--porcelain', '--force-with-lease='+policy['git_ref']+':'+actual,
            remote, payload['oid']+':'+policy['git_ref'])
        after = git('ls-remote', remote, policy['git_ref']).decode().strip().split()
        if not after or after[0] != payload['oid']:
            raise ValueError('read-back mismatch')
        return {'oid': payload['oid']}


def serve(policy_path, key_path, state_path, socket_path, workspace_path):
    global IMPORT_TOOL, TRANSPORT_INSTALLER, TOOL_ENV, SCANNER, LAST_FAILURE
    workspace = Path(workspace_path).resolve(strict=True)
    policy, fingerprint = policy_read(policy_path)
    key = Path(key_path).read_bytes()
    if len(key) < 32:
        raise ValueError('invalid approval key')
    protected = Path(socket_path).absolute().parent.resolve()
    if workspace == protected or workspace in protected.parents or protected in workspace.parents:
        raise ValueError('overlapping requester and trusted paths')
    for candidate in (Path(policy_path), Path(key_path), Path(state_path)):
        if protected not in candidate.resolve().parents:
            raise ValueError('policy/key/state must be in protected socket directory')
    state = Path(state_path); state.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Namespace requesters must not mount this trusted state directory. Keep
    # importer code outside their mutable repository, including after edits.
    # Copy the complete trusted transport set once, outside requester mounts.
    for name in ('publisher.py', 'install-gitleaks-hook.py', 'pre-push-gitleaks.py'):
        source = Path(__file__).resolve().with_name(name).read_bytes()
        destination = state/name
        if destination.exists():
            if destination.is_symlink() or destination.read_bytes() != source:
                raise ValueError('trusted code differs; operator intervention required')
        else:
            with destination.open('xb') as stream:
                stream.write(source)
    IMPORT_TOOL = state/'publisher.py'
    TRANSPORT_INSTALLER = state/'install-gitleaks-hook.py'
    # Freeze trusted tools before accepting requests. A requester-controlled
    # workspace binary/config must not run with publisher credentials.
    tools = state/'bin'; tools.mkdir(mode=0o700, exist_ok=True)
    import shutil
    for name in ('git', 'gh', 'gitleaks'):
        located = shutil.which(name)
        if not located:
            raise ValueError('trusted tool unavailable')
        source = Path(located).resolve(strict=True)
        if source == workspace or workspace in source.parents:
            raise ValueError('workspace-controlled tool refused')
        destination = tools/name
        content = source.read_bytes()
        if destination.exists():
            if destination.is_symlink() or destination.read_bytes() != content:
                raise ValueError('trusted tool changed')
        else:
            with destination.open('xb') as stream:
                stream.write(content)
            destination.chmod(0o700)
    for variable in ('GH_CONFIG_DIR', 'XDG_CONFIG_HOME'):
        if os.environ.get(variable) and workspace in Path(os.environ[variable]).resolve().parents:
            raise ValueError('workspace credential configuration refused')
    original_env = {k: v for k, v in os.environ.items() if k != 'GIT_EXEC_PATH'}
    exec_path = run([shutil.which('git'), '--exec-path'], env=original_env).stdout.decode().strip()
    if workspace == Path(exec_path).resolve() or workspace in Path(exec_path).resolve().parents:
        raise ValueError('workspace Git helper directory refused')
    TOOL_ENV = dict(os.environ, PATH=str(tools)+os.pathsep+'/usr/bin:/bin', GIT_EXEC_PATH=exec_path)
    SCANNER = str(tools/'gitleaks')
    connection = sqlite3.connect(state/'requests.sqlite')
    connection.execute('create table if not exists requests(nonce text primary key, digest text, status text)')
    if 'failure' not in {column[1] for column in connection.execute('pragma table_info(requests)')}:
        connection.execute('alter table requests add column failure text')
        connection.commit()
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(socket_path); os.chmod(socket_path, 0o600); listener.listen(8)
        while True:
            client, _ = listener.accept()
            with client:
                reserved = None
                LAST_FAILURE = None
                try:
                    client.settimeout(15)
                    with client.makefile('rb') as stream:
                        raw = stream.readline(MAX_REQUEST+1)
                    if len(raw) > MAX_REQUEST:
                        raise ValueError('request limit')
                    envelope = decode(raw)
                    if set(envelope) != {'request', 'approval'}:
                        raise ValueError('invalid envelope')
                    request = envelope['request']
                    validate(request, policy, fingerprint)
                    expected = hmac.new(key, canonical(request), hashlib.sha256).hexdigest()
                    if not isinstance(envelope['approval'], str) or not hmac.compare_digest(expected, envelope['approval']):
                        raise ValueError('invalid approval')
                    with connection:
                        connection.execute('insert into requests(nonce,digest,status) values(?,?,?)',
                            (request['nonce'], digest(canonical(request)), 'attempted'))
                    reserved = request['nonce']
                    result = dispatch(request, policy)
                    with connection:
                        connection.execute('update requests set status=? where nonce=?', ('published', request['nonce']))
                    response = {'status': 'published', 'result': result}
                except Exception:
                    # Consumed request IDs remain consumed even after an uncertain
                    # transport result; neither secrets nor raw errors are returned.
                    if reserved is not None:
                        with connection:
                            connection.execute('update requests set status=?,failure=? where nonce=?',
                                ('uncertain', canonical(LAST_FAILURE or {'stage':'validation-or-readback'}).decode(), reserved))
                    response = {'status': 'blocked'}
                with contextlib.suppress(OSError):
                    client.sendall(canonical(response)+b'\n')


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    server = sub.add_parser('serve')
    for name in ('policy', 'key', 'state', 'socket', 'workspace'):
        server.add_argument('--'+name, required=True)
    approve = sub.add_parser('approve')
    for name in ('policy', 'key', 'request', 'out'):
        approve.add_argument('--'+name, required=True)
    approve.add_argument('--context-reviewed', action='store_true')
    client = sub.add_parser('submit')
    client.add_argument('--socket', required=True); client.add_argument('--envelope', required=True)
    importer_args = sub.add_parser('import')
    importer_args.add_argument('--ref', required=True); importer_args.add_argument('--oid', required=True)
    args = parser.parse_args()
    try:
        if args.mode == 'serve':
            serve(args.policy, args.key, args.state, args.socket, args.workspace)
        elif args.mode == 'approve':
            if not args.context_reviewed:
                raise ValueError('review required')
            policy, fingerprint = policy_read(args.policy)
            request = decode(Path(args.request).read_bytes()); validate(request, policy, fingerprint)
            key = Path(args.key).read_bytes()
            if len(key) < 32:
                raise ValueError('invalid key')
            result = {'request': request, 'approval': hmac.new(key, canonical(request), hashlib.sha256).hexdigest()}
            with open(args.out, 'x') as stream:
                stream.write(canonical(result).decode()+'\n')
        elif args.mode == 'submit':
            data = Path(args.envelope).read_bytes()
            if len(data) > MAX_REQUEST or b'\n' in data.rstrip(b'\n'):
                raise ValueError('request limit')
            with socket.socket(socket.AF_UNIX) as stream:
                stream.settimeout(90); stream.connect(args.socket); stream.sendall(data.rstrip(b'\n')+b'\n')
                result = decode(stream.makefile('rb').readline(4096))
                print(canonical(result).decode())
                return 0 if result.get('status') == 'published' else 2
        else:
            importer(args.ref, args.oid)
    except Exception:
        print('publisher: operation blocked; inspect trusted audit state', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
