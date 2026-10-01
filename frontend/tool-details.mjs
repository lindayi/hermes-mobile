// Presentation only. Never stringify arbitrary arguments, code, or private fields.
const MISSING = 'Arguments/output not recorded';
const PRIVATE = 'Details omitted for privacy';
const SENSITIVE = /secret|token|password|passwd|authorization|bearer|credential|api[ _-]?key|private[ _-]?key|\.env|\.ssh|id_rsa|sk-|gh[pousr]_|eyJ|AKIA|[a-zA-Z0-9_+=-]{32,}/i;
const FIELDS = {read_file:['path'],write_file:['path'],patch:['path'],web_search:['query'],
  web_extract:['urls'],browser_navigate:['url'],search_files:['pattern','path'],skill_view:['name','file_path'],
  skill_manage:['name','file_path'],delegate_task:['goal']};
const PROGRAMS = new Set(['python','python3','pytest','node','npm','pip','pip3','uv','git','curl','systemctl','date','df','du','free']);
const bounded = (text, limit = 360) => text.length > limit ? text.slice(0,limit - 1) + '…' : text;
function safeText(value) {
  if(typeof value !== 'string' || value.length > 10000) return '';
  let text = value;
  try {for(let n=0;n<3;n++) {
    const decoded = decodeURIComponent(text);
    // Encoded whitespace must not create a new URL boundary and spill userinfo/query text.
    if(decoded.replace(/\S/g,'').length !== text.replace(/\S/g,'').length) return '';
    text = decoded;
  }} catch {return '';}
  if(/%[0-9a-fA-F]{2}/.test(text)) return '';
  text = text.replace(/\s+/g,' ').trim();
  let unsafeUrl = false;
  text = text.replace(/https?:\/\/[^\s]+/gi, value => {
    // WHATWG URL resolves dot segments; inspect the full path before that lossy step.
    const path = value.match(/^https?:\/\/[^/?#]*([^?#]*)/i)?.[1] || '';
    if(SENSITIVE.test(path)) {unsafeUrl = true; return '';}
    try {const url = new URL(value);return `${url.protocol}//${url.hostname}${url.pathname}`;} catch {return '';}
  });
  if(unsafeUrl || SENSITIVE.test(text) || /[{}\[\]<>`$=;\\]|[^\x20-\x7e\u00b7\u2026]/.test(text)) return '';
  return text;
}
function simpleCommandArgs(tokens, flags = '', {paths = false, numeric = [], selectors = false} = {}) {
  flags = flags.split(' ');
  let number = false;
  for(const [index,token] of tokens.entries()) {
    if(number) {
      if(!/^[0-9]+$/.test(token)) return false;
      number = false;
    } else if(numeric.includes(token)) number = true;
    else if(flags.includes(token)) continue;
    else if(paths) {
      let atom = selectors ? token.replaceAll('::','/') : token;
      if(index === tokens.length - 1) atom = atom.replace(/…$/,'');
      if(!/^[A-Za-z0-9_./~][A-Za-z0-9_./~-]*$/.test(atom)) return false;
    } else return false;
  }
  return !number;
}
function commandShape(program, tokens) {
  // Finite display grammar, not a shell parser or universal secret detector.
  // Exact shapes only: no unknown/abbreviated options or unchecked wrapper argv.
  if(program === 'curl') {
    const flags = new Set(['--fail','--silent','--show-error','--location','--head',
      '--include','--ipv4','--ipv6','--verbose','--version','--help']);
    let number = false;
    for(const token of tokens) {
      if(number) {
        if(!/^[0-9]+(?:\.[0-9]+)?$/.test(token)) return false;
        number = false;
      } else if(['--max-time','--connect-timeout','--retry'].includes(token)) number = true;
      else if(flags.has(token) || /^-[fIsSLivV46]+$/.test(token)) continue;
      else if(!/^https?:\/\/[^\s]+$/i.test(token)) return false;
    }
    return !number;
  }
  if(program === 'node') {
    if(!tokens.length || (tokens.length === 1 && ['--version','-v','--help','-h'].includes(tokens[0]))) return true;
    const paths = ['--test','--check'].includes(tokens[0]) ? tokens.slice(1) : tokens;
    if(tokens[0] !== '--test' && paths.length !== 1) return false;
    return paths.every((path,index) => !path.startsWith('-') && (
      /^[A-Za-z0-9_./-]+\.(?:js|mjs|cjs)$/.test(path) ||
      (index === paths.length - 1 && /^[A-Za-z0-9_./-]+…$/.test(path))
    ));
  }
  if(program === 'pytest') return simpleCommandArgs(tokens,'-q -qq -v -vv -s -x --collect-only --disable-warnings',{paths:true,numeric:['--maxfail'],selectors:true});
  if(['python','python3'].includes(program)) {
    if(tokens.length === 1 && ['--version','-V','--help','-h'].includes(tokens[0])) return true;
    if(tokens[0] === '-m' && ['pytest','pip'].includes(tokens[1])) return commandShape(tokens[1],tokens.slice(2));
    return tokens.length === 1 && /^[A-Za-z0-9_./-]+\.py$/.test(tokens[0]) && !tokens[0].startsWith('-');
  }
  if(program === 'git') {
    const commands = {
      status:'--short --branch --porcelain -s -b', diff:'--stat --name-only --cached --staged --check --',
      log:'--oneline --all --decorate --graph --no-decorate', show:'--stat --name-only --no-patch',
      branch:'--show-current --list --all --verbose -a -v', 'rev-parse':'--short --show-toplevel --abbrev-ref --verify',
      'ls-files':'--cached --others --exclude-standard',
    };
    return Object.hasOwn(commands,tokens[0]) && simpleCommandArgs(tokens.slice(1),commands[tokens[0]],{paths:true,numeric:tokens[0] === 'log' ? ['-n'] : []});
  }
  if(program === 'npm') {
    if(tokens[0] === 'run') tokens = tokens.slice(1);
    return ['test','build','lint','check','typecheck','start','--version','--help'].includes(tokens[0]) && simpleCommandArgs(tokens.slice(1),'--silent --if-present');
  }
  if(['pip','pip3'].includes(program)) return ['list','freeze','check','show','--version','--help'].includes(tokens[0]) && simpleCommandArgs(tokens.slice(1),'',{paths:tokens[0] === 'show'});
  if(program === 'uv') return tokens[0] === 'run' && ['pytest','python','python3','node','npm'].includes(tokens[1]) && commandShape(tokens[1],tokens.slice(2));
  if(program === 'systemctl') return ['status','is-active','is-enabled','list-units'].includes(tokens[0]) && simpleCommandArgs(tokens.slice(1),'--user --no-pager --all',{paths:true});
  if(['date','df','du','free'].includes(program)) {
    const flags = {date:'-u --utc --version --help',df:'-h -H -T -hT --human-readable',
      du:'-h -s -sh -hs --summarize --human-readable',free:'-h -m -g --human --mega --giga'};
    return simpleCommandArgs(tokens,flags[program],{paths:['df','du'].includes(program)});
  }
  return false;
}
function safeCommand(value) {
  if(typeof value !== 'string' || /[\r\n]/.test(value)) return '';
  const command = safeText(value);
  if(/[|&()'"·]/.test(command)) return '';
  const tokens = command.split(' ');
  if(!/^[A-Za-z0-9_./-]+$/.test(tokens[0])) return '';
  const program = tokens[0].split('/').at(-1);
  if(!PROGRAMS.has(program) || /(?:^|\s)(?:-[A-Za-z]*[cep]|--(?:eval|print))/.test(command)) return '';
  if(!commandShape(program,tokens.slice(1))) return '';
  return command;
}
function safeWorkdir(value) {
  // Screen display metadata independently, without interpreting quotes as shell.
  const text = safeText(value);
  return /^[A-Za-z0-9_./~][A-Za-z0-9_./~ ()'"-]*…?$/.test(text) ? text : '';
}
function terminalSummary(text) {
  if(text.length > 10000 || /[\r\n]/.test(text)) return '';
  const parts = text.split(' · ');
  const command = safeCommand(parts.shift());
  if(!command) return '';
  const result = [command];
  if(parts[0]?.startsWith('cwd ')) {
    const cwd = safeWorkdir(parts.shift().slice(4));
    if(!cwd) return '';
    result.push('cwd '+cwd);
  }
  if(parts[0] === 'background') result.push(parts.shift());
  return parts.length ? '' : result.join(' · ');
}
function selectionParts(name, args) {
  const parts = [];
  if(name === 'search_files') {
    if(['content','files'].includes(args.target)) parts.push('target '+args.target);
    const glob = safeText(args.file_glob);
    if(glob) parts.push('glob '+glob);
  }
  if(['read_file','search_files','process'].includes(name)) {
    for(const key of ['offset','limit']) {
      if(Number.isSafeInteger(args[key]) && args[key] >= (key === 'offset' && name === 'search_files' ? 0 : 1)) parts.push(`${key} ${args[key]}`);
    }
  }
  return parts;
}
function actionParts(name, args) {
  const fixed = {execute_code:'Run Python script',browser_exec:'Run browser script',browser_console:'Inspect browser console',browser_back:'Go back'};
  if(Object.hasOwn(fixed,name)) return [fixed[name]];
  if(name === 'browser_snapshot' && typeof args.full === 'boolean') return [args.full ? 'Full snapshot' : 'Compact snapshot'];
  if(['browser_click','browser_type'].includes(name) && typeof args.ref === 'string' && /^@e\d{1,9}$/.test(args.ref)) return [(name === 'browser_click' ? 'Click ' : 'Type into ')+args.ref];
  if(name === 'browser_press' && ['Enter','Tab','Escape','ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Backspace','Delete','Home','End','PageUp','PageDown','Space'].includes(args.key)) return ['Press '+args.key];
  if(name === 'browser_scroll' && ['up','down'].includes(args.direction)) return ['Scroll '+args.direction];
  if(name === 'skill_manage' && ['create','patch','edit','delete','write_file','remove_file'].includes(args.action)) return [args.action];
  if((name === 'process' && ['list','poll','log','wait','kill','write','submit','close'].includes(args.action)) || (name === 'delegate_task' && ['list','steer','stop'].includes(args.action))) {
    const parts = [args.action[0].toUpperCase()+args.action.slice(1)+(name === 'process' ? ' process' : ' subagents')];
    const id = safeText(args[name === 'process' ? 'session_id' : 'subagent_id']);
    if(id) parts.push(id);
    if(name === 'process' && args.action === 'wait' && Number.isSafeInteger(args.timeout) && args.timeout > 0) parts.push(`timeout ${args.timeout}s`);
    return parts;
  }
  return [];
}
function object(value) {
  if(typeof value === 'string') {
    if(value.length > 10000) return null;
    try {value = JSON.parse(value);} catch {return null;}
  }
  return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
}
function resultText(value) {
  if(typeof value === 'string') {
    if(!value.trim()) return '';
    if(/^[\s]*[\[{]/.test(value)) {
      const parsed = object(value);
      return parsed ? resultText(parsed) : PRIVATE;
    }
    return bounded(safeText(value)) || PRIVATE;
  }
  const result = object(value);
  if(result) {
    for(const key of ['output','content','text','message']) {
      if(typeof result[key] === 'string' && result[key].trim()) return resultText(result[key]);
    }
  }
  return '';
}
export function toolName(data = {}) {
  const name = data?.function?.name || data?.name || data?.tool || data?.tool_name || data?.recipient_name;
  return typeof name === 'string' && /^[A-Za-z][A-Za-z0-9_.:-]{0,63}$/.test(name) && !SENSITIVE.test(name) ? name.replace(/^functions\./,'') : 'tool';
}
// Native tool.completed.duration is seconds; absent duration is not zero.
export function toolDuration(data = {}) {
  const seconds = data?.duration;
  return typeof seconds === 'number' && Number.isFinite(seconds) && seconds >= 0 ? `${Number(seconds.toFixed(3))}s` : '';
}
export function toolDetail(data = {}) {
  return detail(data, 0);
}
function detail(data, depth) {
  if(!object(data) || depth > 3) return MISSING;
  const fn = object(data.function);
  const name = toolName(data);
  const args = object(fn?.arguments ?? data.arguments ?? data.args ?? data.parameters) || {};
  if(['multi_tool_use','multi_tool_use.parallel'].includes(name) && Array.isArray(args.tool_uses)) {
    const children = args.tool_uses.slice(0,4).filter(item => object(item)).map(item => {
      const text = detail(item, depth + 1);
      return bounded(text === MISSING ? toolName(item) : `${toolName(item)}: ${text}`,120);
    }).filter(Boolean);
    if(children.length) return bounded(children.join(' · '));
  }
  if(name === 'delegate_task' && Array.isArray(args.tasks)) {
    const goals = args.tasks.slice(0,4).map(item => bounded(safeText(item?.goal),96)).filter(Boolean);
    return bounded(`${args.tasks.length} tasks`+(goals.length ? ': '+goals.join(' · ') : ''));
  }
  if(name === 'terminal') {
    const command = safeCommand(args.command);
    if(command) {
      const cwd = safeWorkdir(args.workdir);
      return bounded([command,cwd ? 'cwd '+cwd : '',args.background === true ? 'background' : ''].filter(Boolean).join(' · '));
    }
    if(args.command) return PRIVATE;
  }
  const values = (Object.hasOwn(FIELDS,name) ? FIELDS[name] : []).map(key => Array.isArray(args[key]) ? args[key][0] : args[key]);
  const details = [...actionParts(name,args), ...values.map(safeText).filter(Boolean), ...selectionParts(name,args)];
  if(details.length) return bounded(details.join(' · '));
  if(values.some(value => value != null && value !== '')) return PRIVATE;
  let summaryDetail = '';
  if(typeof data.summary === 'string') {
    const summary = data.summary.trim().replace(/^functions\./,'');
    if(summary && summary !== name) {
      const text = summary.startsWith(name + ': ') ? summary.slice(name.length + 2) : summary;
      const legacy = name === 'terminal' && text.startsWith('Run: ');
      const command = legacy ? terminalSummary(text.slice(5)) : '';
      summaryDetail = (legacy ? (command ? 'Run: '+command : '') : name === 'terminal' && !/^Run \w+$/.test(text) ? terminalSummary(text) : safeText(text)) || PRIVATE;
      if(name === 'terminal' && summaryDetail === PRIVATE) return PRIVATE;
      if(!/^Run \w+$/.test(summaryDetail) && summaryDetail !== PRIVATE) return bounded(summaryDetail);
    }
  }
  return resultText(data.content ?? data.result ?? data.output) || summaryDetail || MISSING;
}
