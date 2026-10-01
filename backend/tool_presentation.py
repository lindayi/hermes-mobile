"""Presentation-only, fail-closed previews; no native imports or execution edits."""
import json
import re
from urllib.parse import unquote, urlsplit, urlunsplit

_FIELDS = {'read_file': ('path',), 'write_file': ('path',), 'web_search': ('query',),
           'web_extract': ('urls',), 'browser_navigate': ('url',), 'patch': ('path',),
           'search_files': ('pattern', 'path'), 'skill_view': ('name', 'file_path'),
           'skill_manage': ('name', 'file_path'), 'delegate_task': ('goal',)}
_SENSITIVE = re.compile(r'(?i)secret|token|password|passwd|authorization|bearer|credential|api[ _-]?key|private[ _-]?key|\.env|\.ssh|id_rsa|sk-|gh[pousr]_|eyJ|AKIA|[a-zA-Z0-9_+=-]{32,}')


def _safe_detail(value):
    if not isinstance(value, str) or len(value) > 10000:
        return ''
    # Canonicalize before URL removal: encoded userinfo must not survive display.
    for _ in range(3):
        decoded = unquote(value)
        if sum(char.isspace() for char in decoded) != sum(char.isspace() for char in value):
            return ''
        value = decoded
    if re.search(r'%[0-9a-fA-F]{2}', value):
        return ''
    value = ' '.join(value.split())
    def clean_url(match):
        try:
            url = urlsplit(match.group())
            return urlunsplit((url.scheme, url.hostname or '', url.path, '', ''))
        except ValueError:
            return ''
    value = re.sub(r'https?://[^\s]+', clean_url, value, flags=re.IGNORECASE)
    if _SENSITIVE.search(value) or re.search(r'[{}\[\]<>`$=;\\]|[^\x20-\x7e\u00b7\u2026]', value):
        return ''
    return value


def _safe_workdir(value):
    # Metadata is not shell syntax, but must not impersonate composed fields.
    value = _safe_detail(value)
    return value if re.fullmatch(r"[A-Za-z0-9_./~][A-Za-z0-9_./~ ()'\"-]*…?", value) else ''


def tool_summary(name, arguments=None, *, preview=None):
    return _summary(name, arguments, preview=preview, depth=0)


def _bounded(text, limit=360):
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _selection_parts(name, args):
    parts = []
    if name == 'search_files':
        if args.get('target') in ('content', 'files'):
            parts.append('target ' + args['target'])
        glob = _safe_detail(args.get('file_glob'))
        if glob:
            parts.append('glob ' + glob)
    if name in ('read_file', 'search_files', 'process'):
        for key in ('offset', 'limit'):
            value = args.get(key)
            minimum = 0 if key == 'offset' and name == 'search_files' else 1
            if type(value) is int and minimum <= value <= 9007199254740991:
                parts.append(f'{key} {value}')
    return parts


def _action_parts(name, args):
    fixed = {'execute_code': 'Run Python script', 'browser_exec': 'Run browser script',
             'browser_console': 'Inspect browser console', 'browser_back': 'Go back'}
    if name in fixed:
        return [fixed[name]]
    if name == 'browser_snapshot' and type(args.get('full')) is bool:
        return ['Full snapshot' if args['full'] else 'Compact snapshot']
    ref = args.get('ref')
    if name in ('browser_click', 'browser_type') and isinstance(ref, str) and re.fullmatch(r'@e\d{1,9}', ref):
        return [('Click ' if name == 'browser_click' else 'Type into ') + ref]
    if name == 'browser_press' and args.get('key') in ('Enter', 'Tab', 'Escape', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Backspace', 'Delete', 'Home', 'End', 'PageUp', 'PageDown', 'Space'):
        return ['Press ' + args['key']]
    if name == 'browser_scroll' and args.get('direction') in ('up', 'down'):
        return ['Scroll ' + args['direction']]
    if name == 'skill_manage' and args.get('action') in ('create', 'patch', 'edit', 'delete', 'write_file', 'remove_file'):
        return [args['action']]
    if (name == 'process' and args.get('action') in ('list', 'poll', 'log', 'wait', 'kill', 'write', 'submit', 'close')) or (name == 'delegate_task' and args.get('action') in ('list', 'steer', 'stop')):
        parts = [args['action'].capitalize() + (' process' if name == 'process' else ' subagents')]
        sid = _safe_detail(args.get('session_id' if name == 'process' else 'subagent_id'))
        if sid:
            parts.append(sid)
        timeout = args.get('timeout')
        if name == 'process' and args['action'] == 'wait' and type(timeout) is int and 0 < timeout <= 9007199254740991:
            parts.append(f'timeout {timeout}s')
        return parts
    return []


def _preview_args(name, preview):
    """Decode only known native display shapes, never treat previews as raw args.

    In particular browser_type's native preview is *text*, not its target ref.
    Process write/submit previews can contain stdin; only the action survives.
    """
    value = _safe_detail(preview)
    if name == 'process':
        action = value.split(' ', 1)[0]
        if action not in ('list', 'poll', 'log', 'wait', 'kill', 'write', 'submit', 'close'):
            return {}
        args = {'action': action}
        match = re.fullmatch(r'(\w+) ([-\w]{1,64})(?: (\d{1,9})s)?', value, flags=re.ASCII)
        if match:
            args['session_id'] = match[2]
            if match[3] and action == 'wait':
                args['timeout'] = int(match[3])
        return args
    key = {'browser_click': 'ref', 'browser_press': 'key', 'browser_scroll': 'direction'}.get(name)
    return {key: value} if key else {}


def _simple_command_args(tokens, flags='', *, paths=False, numeric=(), selectors=False):
    flags = flags.split()
    number = False
    for index, token in enumerate(tokens):
        if number:
            if not re.fullmatch(r'[0-9]+', token):
                return False
            number = False
        elif token in numeric:
            number = True
        elif token in flags:
            continue
        elif paths:
            atom = token.replace('::', '/') if selectors else token
            if index == len(tokens) - 1:
                atom = atom.removesuffix('…')
            if not re.fullmatch(r'[A-Za-z0-9_./~][A-Za-z0-9_./~-]*', atom):
                return False
        else:
            return False
    return not number


def _command_shape(program, tokens):
    """Finite display grammar, not a shell parser or universal secret detector.

    No prefix matching: compact, abbreviated, truncated and unknown options fail
    closed. Inspect every token before the caller applies any display budget.
    """
    if program == 'curl':
        flags = {'--fail', '--silent', '--show-error', '--location', '--head',
                 '--include', '--ipv4', '--ipv6', '--verbose', '--version', '--help'}
        number = False
        for token in tokens:
            if number:
                if not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', token):
                    return False
                number = False
            elif token in ('--max-time', '--connect-timeout', '--retry'):
                number = True
            elif token in flags or re.fullmatch(r'-[fIsSLivV46]+', token):
                continue
            elif not re.fullmatch(r'https?://[^\s]+', token, flags=re.IGNORECASE):
                return False
        return not number
    if program == 'node':
        if not tokens or tokens in (['--version'], ['-v'], ['--help'], ['-h']):
            return True
        paths = tokens[1:] if tokens[0] in ('--test', '--check') else tokens
        if tokens[0] != '--test' and len(paths) != 1:
            return False
        return all(not path.startswith('-') and (
            re.fullmatch(r'[A-Za-z0-9_./-]+\.(?:js|mjs|cjs)', path)
            or (index == len(paths) - 1 and re.fullmatch(r'[A-Za-z0-9_./-]+…', path))
        ) for index, path in enumerate(paths))
    if program == 'pytest':
        return _simple_command_args(tokens, '-q -qq -v -vv -s -x --collect-only --disable-warnings', paths=True, numeric=('--maxfail',), selectors=True)
    if program in ('python', 'python3'):
        if tokens in (['--version'], ['-V'], ['--help'], ['-h']):
            return True
        if len(tokens) >= 2 and tokens[0] == '-m' and tokens[1] in ('pytest', 'pip'):
            return _command_shape(tokens[1], tokens[2:])
        return len(tokens) == 1 and bool(re.fullmatch(r'[A-Za-z0-9_./-]+\.py', tokens[0])) and not tokens[0].startswith('-')
    if program == 'git':
        commands = {
            'status': '--short --branch --porcelain -s -b',
            'diff': '--stat --name-only --cached --staged --check --',
            'log': '--oneline --all --decorate --graph --no-decorate',
            'show': '--stat --name-only --no-patch',
            'branch': '--show-current --list --all --verbose -a -v',
            'rev-parse': '--short --show-toplevel --abbrev-ref --verify',
            'ls-files': '--cached --others --exclude-standard',
        }
        return bool(tokens) and tokens[0] in commands and _simple_command_args(tokens[1:], commands[tokens[0]], paths=True, numeric=('-n',) if tokens[0] == 'log' else ())
    if program == 'npm':
        if tokens[:1] == ['run']:
            tokens = tokens[1:]
        return bool(tokens) and tokens[0] in ('test', 'build', 'lint', 'check', 'typecheck', 'start', '--version', '--help') and _simple_command_args(tokens[1:], '--silent --if-present')
    if program in ('pip', 'pip3'):
        return bool(tokens) and tokens[0] in ('list', 'freeze', 'check', 'show', '--version', '--help') and _simple_command_args(tokens[1:], paths=tokens[0] == 'show')
    if program == 'uv':
        return len(tokens) >= 2 and tokens[0] == 'run' and tokens[1] in ('pytest', 'python', 'python3', 'node', 'npm') and _command_shape(tokens[1], tokens[2:])
    if program == 'systemctl':
        return bool(tokens) and tokens[0] in ('status', 'is-active', 'is-enabled', 'list-units') and _simple_command_args(tokens[1:], '--user --no-pager --all', paths=True)
    if program in ('date', 'df', 'du', 'free'):
        flags = {'date': '-u --utc --version --help', 'df': '-h -H -T -hT --human-readable',
                 'du': '-h -s -sh -hs --summarize --human-readable', 'free': '-h -m -g --human --mega --giga'}
        return _simple_command_args(tokens, flags[program], paths=program in ('df', 'du'))
    return False


def _summary(name, arguments=None, *, preview=None, depth=0):
    name = name if isinstance(name, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_.:-]{0,63}', name) and not _SENSITIVE.search(name) else 'tool'
    if depth > 3:
        return name
    try:
        args = (json.loads(arguments) if len(arguments) <= 10000 else {}) if isinstance(arguments, str) else arguments
    except (ValueError, TypeError, RecursionError):
        args = {}
    args = args if isinstance(args, dict) else {}
    base_name = name.removeprefix('functions.')
    if arguments is None:
        args = _preview_args(base_name, preview)
    parts = []
    for index, key in enumerate(_FIELDS.get(base_name, ())):
        value = args.get(key, preview if index == 0 else None)
        if isinstance(value, list):
            value = value[0] if value else None
        value = _safe_detail(value)
        if value:
            parts.append(value)
    detail = ' · '.join(_action_parts(base_name, args) + parts + _selection_parts(base_name, args))
    if base_name == 'terminal':
        # Reveal only screened commands from the program allowlist, never raw code.
        raw_command = args.get('command', preview)
        command = _safe_detail(raw_command)
        if (isinstance(raw_command, str) and re.search(r'[\r\n]', raw_command)) or re.search(r"[|&()'\"·]", command):
            command = ''
        tokens = command.split()
        program = tokens[0].rsplit('/', 1)[-1] if tokens else ''
        executable = bool(tokens) and bool(re.fullmatch(r'[A-Za-z0-9_./-]+', tokens[0]))
        unsafe_flags = re.search(r'(?:^|\s)(?:-[A-Za-z]*[cep]|--(?:eval|print))', command)
        allowed_shape = executable and _command_shape(program, tokens[1:])
        if not unsafe_flags and allowed_shape and program in ('python', 'python3', 'pytest', 'node', 'npm', 'pip', 'pip3',
                       'uv', 'git', 'curl', 'systemctl', 'date', 'df', 'du', 'free'):
            cwd = _safe_workdir(args.get('workdir'))
            detail = ' · '.join(filter(None, (command, 'cwd ' + cwd if cwd else '', 'background' if args.get('background') is True else '')))
    elif base_name == 'delegate_task':
        if isinstance(args.get('tasks'), list) and args['tasks']:
            goals = [_bounded(_safe_detail(task.get('goal')), 96) for task in args['tasks'][:4] if isinstance(task, dict)]
            goals = list(filter(None, goals))
            detail = str(len(args['tasks'])) + ' tasks' + (': ' + ' · '.join(goals) if goals else '')

    if base_name in ('multi_tool_use', 'multi_tool_use.parallel') and isinstance(args.get('tool_uses'), list):
        children = []
        for child in args['tool_uses'][:4]:
            if not isinstance(child, dict):
                continue
            function = child.get('function')
            function = function if isinstance(function, dict) else child
            child_name = function.get('name') or function.get('recipient_name')
            child_args = function.get('arguments', function.get('parameters', function.get('args')))
            text = _summary(child_name, child_args, depth=depth + 1)
            children.append(_bounded(text.removeprefix('functions.'), 120))
        detail = ' · '.join(children)
    text = f'{name}: {detail}' if detail else name
    return _bounded(text)


def normalize_tool_event(event):
    """Retain lifecycle metadata, not argument/result/preview payloads."""
    allowed = ('event', 'tool', 'name', 'tool_name', 'run_id', 'id', 'call_id',
               'tool_call_id', 'status', 'is_error', 'error', 'duration')
    result = {key: event[key] for key in allowed if key in event}
    if event.get('event') == 'tool.started':
        name = event.get('tool') or event.get('name') or event.get('tool_name')
        result['summary'] = tool_summary(name, preview=event.get('preview'))
    return result
