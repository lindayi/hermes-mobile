import test from 'node:test';
import assert from 'node:assert/strict';
const {toolDetail, toolName, toolDuration} = await import('../../frontend/tool-details.mjs').catch(error => {
  if(error.code !== 'ERR_MODULE_NOT_FOUND') throw error;
  return {};
});

test('canonical names and recorded seconds are shared by expanded and collapsed UI', () => {
  assert.equal(typeof toolName,'function');
  assert.equal(typeof toolDuration,'function');
  assert.equal(toolName({function:{name:'functions.read_file'}}),'read_file');
  assert.equal(toolName({recipient_name:'functions.browser_click'}),'browser_click');
  for(const data of [null, {}, {name:42}, {name:'bad name'}, {name:'password_HIDDEN'}]) assert.equal(toolName(data),'tool');
  for(const [duration, expected] of [[0,'0s'],[1.25,'1.25s'],[0.5,'0.5s'],[1.23456,'1.235s'],[-1,''],[NaN,''],[Infinity,''],['1.25',''],[null,''],[{},'']]) assert.equal(toolDuration({duration}),expected);
  assert.equal(toolDuration(null),'');
  assert.equal(toolDuration({}),'');
});

test('saved function arguments recover useful command instead of repeated bare name', () => {
  assert.equal(typeof toolDetail, 'function', 'toolDetail presentation helper must exist');
  assert.equal(toolDetail({summary:'functions.terminal',function:{name:'functions.terminal',arguments:JSON.stringify({command:'pytest /home/lindayi/projects/hermes-mobile/tests -q'})}}),
    'pytest /home/lindayi/projects/hermes-mobile/tests -q');
});

test('allowlisted primary arguments exclude unknown args, bodies and private reasoning', () => {
  for(const [name,args,expected] of [
    ['read_file',{path:'/home/lindayi/projects/hermes-mobile/frontend/ui.mjs'},'/home/lindayi/projects/hermes-mobile/frontend/ui.mjs'],
    ['write_file',{path:'src/main.py',content:'HIDDEN'},'src/main.py'],
    ['patch',{path:'src/main.py',new_string:'HIDDEN'},'src/main.py'],
    ['web_search',{query:'weather Toronto'},'weather Toronto'],
    ['web_extract',{urls:['https://u:p@example.com/docs?token=hidden']},'https://example.com/docs'],
    ['search_files',{pattern:'toolPreview',path:'frontend'},'toolPreview · frontend'],
    ['delegate_task',{goal:'Check saved tool previews',context:'HIDDEN'},'Check saved tool previews'],
    ['browser_type',{text:'HIDDEN'},'Arguments/output not recorded'],
    ['unknown',{query:'HIDDEN'},'Arguments/output not recorded'],
  ]) assert.equal(toolDetail({name,args,reasoning_content:'HIDDEN',api_content:'HIDDEN'}),expected,name);
});

test('bounded public result fallback never dumps object config or reasoning fields', () => {
  assert.equal(toolDetail({name:'terminal',summary:'terminal',content:JSON.stringify({output:'12 tests passed',exit_code:0,reasoning_content:'HIDDEN',config:{value:'HIDDEN'}})}), '12 tests passed');
  assert.equal(toolDetail({name:'unknown',content:'Found three matching files'}), 'Found three matching files');
  assert.equal(toolDetail({name:'unknown',content:{secret:'HIDDEN',reasoning_content:'HIDDEN'}}), 'Arguments/output not recorded');
  assert.equal(toolDetail({name:'terminal',content:'password=HIDDEN'}), 'Details omitted for privacy');
  assert.equal(toolDetail({name:'terminal',content:'{"output":"truncated'}), 'Details omitted for privacy');
  // Oversized untrusted results are omitted whole, never screened after slicing.
  assert.equal(toolDetail({name:'terminal',content:'Tests passed. '.repeat(2000)}), 'Details omitted for privacy');
  assert.equal(toolDetail({name:'terminal',content:'Tests passed. '.repeat(2000)+'password=HIDDEN'}), 'Details omitted for privacy');
  assert.equal(toolDetail({name:'terminal',summary:'terminal: Run pytest',content:'12 tests passed'}), '12 tests passed');
});

test('nested parallel wrappers show only bounded allowlisted children and delegation goals', () => {
  const wrapper={name:'multi_tool_use.parallel',arguments:{tool_uses:[
    {recipient_name:'functions.read_file',parameters:{path:'docs/guide.md'}},
    {recipient_name:'multi_tool_use.parallel',parameters:{tool_uses:[
      {recipient_name:'functions.terminal',parameters:{command:'git status --short'}},
      {function:{name:'web_search',arguments:'{"query":"weather Toronto"}'}},
    ]}},
  ]}};
  assert.equal(toolDetail(wrapper), 'read_file: docs/guide.md · multi_tool_use.parallel: terminal: git status --short · web_search: weather Toronto');
  assert.equal(toolDetail({name:'delegate_task',arguments:{tasks:[{goal:'Inspect UI',context:'HIDDEN'},{goal:'Run tests'}]}}),'2 tasks: Inspect UI · Run tests');
  let deep={name:'read_file',arguments:{path:'HIDDEN'}};
  for(let n=0;n<15;n++) deep={name:'multi_tool_use.parallel',arguments:{tool_uses:[deep]}};
  assert.ok(!toolDetail(deep).includes('HIDDEN'));
  assert.ok(toolDetail({name:'multi_tool_use.parallel',arguments:{tool_uses:Array.from({length:100},(_,n)=>({name:'web_search',arguments:{query:`query ${n}`}}))}}).length<=360);
  assert.ok(!toolDetail({name:'multi_tool_use.parallel',arguments:{tool_uses:Array.from({length:100},(_,n)=>({name:'web_search',arguments:{query:`query ${n}`}}))}}).includes('query 99'));
});

test('truncated native summaries retain ellipsis without weakening secret screening', () => {
  const command='pytest tests/ '.repeat(15);
  const summary=('terminal: '+command).slice(0,119)+'…';
  assert.equal(toolDetail({name:'terminal',summary}),summary.slice('terminal: '.length));
  for(const data of [null,undefined,{name:42},{function:{name:42}}, {name:'unknown',arguments:'{bad'}, {name:'terminal',args:{command:'python3 -c "print(HIDDEN)"'}}])
    assert.doesNotThrow(()=>toolDetail(data));
  assert.equal(toolDetail({name:'terminal',args:{command:'python3 -c "print(HIDDEN)"'}}),'Details omitted for privacy');
  assert.equal(toolDetail({name:'web_search',args:{query:'safe words '.repeat(80)+'password=HIDDEN'}}),'Details omitted for privacy');
  assert.equal(toolDetail({name:'read_file',args:{path:'/home/person/.ssh/id_rsa'}}),'Details omitted for privacy');
  assert.equal(toolDetail({name:'web_search',args:{query:'https://example.com/%70assword/hidden'}}),'Details omitted for privacy');
  assert.equal(toolDetail({name:'web_search',args:{query:'ghp_abcdefghijklmnopqrstuv'}}),'Details omitted for privacy');
  assert.equal(toolDetail({name:'terminal',args:{command:'git status',env:{PASSWORD:'HIDDEN'}},reasoning:'HIDDEN'}),'git status');
});

test('encoded URLs cannot retain hidden userinfo and inline evaluators never expose code', () => {
  for(const url of ['https%3A%2F%2Falice%3Ahunter2%40example.com%2Fdocs','https%3A%2F%2Falice%3Ahunter2%40example.com%2Fdocs%3Fx%3DHIDDEN'])
    assert.equal(toolDetail({name:'web_search',args:{query:url}}),'https://example.com/docs');
  for(const command of ['curl -u alice:hunter2 https://example.com','curl --user alice:hunter2 https://example.com','curl -Ualice:hunter2 https://example.com','curl --proxy-user alice:hunter2 https://example.com','curl -H "Cookie: abc" https://example.com','curl --header "Cookie: abc" https://example.com','curl -d HIDDEN https://example.com','curl --data-raw HIDDEN https://example.com','python3 -cprint(HIDDEN)','python3 -Ic "print(HIDDEN)"','node -e "console.log(HIDDEN)"','node --eval "console.log(HIDDEN)"','node -p "HIDDEN"']) {
    assert.equal(toolDetail({name:'terminal',args:{command}}),'Details omitted for privacy');
    assert.equal(toolDetail({name:'terminal',summary:'terminal: '+command}),'Details omitted for privacy');
  }
});

test('credential loaders fail closed on direct and untrusted summary paths', () => {
  const commands = [
    'curl --cert identity.pem:hunter2 https://example.com',
    'curl -E identity.pem:hunter2 https://example.com',
    'curl -Eidentity.pem:hunter2 https://example.com',
    'curl -sSEidentity.pem:hunter2 https://example.com',
    'curl --cert=identity.pem:hunter2 https://example.com',
    'curl --pass hunter2 https://example.com', 'curl --pass=hunter2 https://example.com',
    'curl --proxy-cert identity.pem:hunter2 https://example.com',
    'curl --cert identity.pem:hunt…', 'curl --cer…', 'curl --pa hunter2',
    'curl --config /tmp/public-file', 'curl -K/tmp/public-file', 'curl --unknown hunter2',
    'node --import data:text/javascript,throw/**/HIDDEN',
    'node --import=data:text/javascript,throw/**/HIDDEN',
    'node --loader data:text/javascript,throw/**/HIDDEN',
    'node --experimental-loader data:text/javascript,throw/**/HIDDEN',
    'node --require data:text/javascript,throw/**/HIDDEN',
    'node -rdata:text/javascript,throw/**/HIDDEN',
    'node --import data:text/javascript,HID…', 'node --imp…', 'node --unknown HIDDEN',
    'curl '+'https://example.com/public/ '.repeat(30)+'--cert identity.pem:hunter2',
    'node '+'tests/public.mjs '.repeat(40)+'--import data:text/javascript,HIDDEN',
  ];
  for(const command of commands) {
    for(const data of [
      {args:{command}}, {function:{name:'functions.terminal',arguments:JSON.stringify({command})}},
      {summary:'terminal: '+command}, {summary:'Run: '+command},
      {summary:'terminal: '+command,content:command},
      {args:{command},summary:'terminal: git status'},
    ]) assert.equal(toolDetail({name:'terminal',...data}), 'Details omitted for privacy', command);
    assert.equal(toolDetail({name:'multi_tool_use.parallel',args:{tool_uses:[{name:'terminal',args:{command}}]}}), 'terminal: Details omitted for privacy');
  }
});

test('simple commands stay useful with credential loader restrictions', () => {
  for(const command of ['curl -fsSL https://example.com/docs', 'curl --head https://example.com/docs',
    'curl --max-time 30 https://example.com/docs', 'node --test tests/browser/tool-detail.test.mjs',
    'node --check frontend/tool-details.mjs', 'node app.mjs', 'git status --short', '/usr/bin/python3 -m pytest tests -q']) {
    assert.equal(toolDetail({name:'terminal',args:{command}}),command);
    assert.equal(toolDetail({name:'terminal',summary:'terminal: '+command}),command);
  }
});

test('unknown command shapes and non-http credential URLs are not plain argv previews', () => {
  for(const command of [
    'curl ftp://alice:hunter2@example.com/file','curl ssh://alice:hunter2@example.com/file',
    'git clone ssh://alice:hunter2@example.com/repo','npm config set _auth dXNlcjpwYXNz',
    'npm config set auth dXNlcjpwYXNz','npm install ftp://alice:hunter2@example.com/pkg',
    'pip install ssh://alice:hunter2@example.com/pkg','python3 -m pip install ftp://alice:hunter2@example.com/pkg',
    'pytest tests --unknown hunter2','git --config foo hunter2','git status --unknown hunter2',
    'npm test --unknown hunter2','uv run node --import data:text/javascript,HIDDEN',
    'python3 -m unknown HIDDEN','date --unknown hunter2','df --unknown hunter2','du --unknown hunter2','free --unknown hunter2',
  ]) {
    assert.equal(toolDetail({name:'terminal',args:{command},summary:'terminal: git status'}),'Details omitted for privacy',command);
    assert.equal(toolDetail({name:'terminal',summary:'terminal: '+command,content:command}),'Details omitted for privacy',command);
  }
});

test('safe legacy Run summaries retain priority without bypassing command screening', () => {
  assert.equal(toolDetail({name:'terminal',summary:'Run: pytest tests',content:'private raw log'}),'Run: pytest tests');
  for(const command of ['python3 -c "print(HIDDEN)"','curl -u alice:hunter2 https://example.com'])
    assert.equal(toolDetail({name:'terminal',summary:'Run: '+command}),'Details omitted for privacy');
});

test('command screening sees flags beyond the display boundary and refuses shell scripts', () => {
  for(const command of ['python3 '+ 'tests/ '.repeat(80)+'-c HIDDEN', 'curl '+ 'https://example.com/ '.repeat(30)+'-u alice:hunter2', 'git status && printf HIDDEN', 'git status | printf HIDDEN', 'git status\nprintf HIDDEN', `python3 '-c' HIDDEN`, `node "--eval" HIDDEN`, `curl '--user' alice:hunter2 https://example.com`]) {
    assert.equal(toolDetail({name:'terminal',args:{command}}),'Details omitted for privacy');
    assert.equal(toolDetail({name:'terminal',summary:'terminal: '+command}),'Details omitted for privacy');
  }
});

test('read and search previews retain recorded location and selection context', () => {
  assert.equal(toolDetail({name:'read_file',args:{path:'src/app.py',offset:40,limit:25}}), 'src/app.py · offset 40 · limit 25');
  assert.equal(toolDetail({name:'search_files',args:{pattern:'render',path:'frontend',target:'content',file_glob:'*.mjs',limit:5}}), 'render · frontend · target content · glob *.mjs · limit 5');
  assert.equal(toolDetail({name:'read_file',args:{path:'docs/guide.md',offset:'password HIDDEN',limit:-1}}),'docs/guide.md');
  assert.equal(toolDetail({name:'search_files',args:{pattern:'password HIDDEN',path:'src',target:'HIDDEN',file_glob:'.env'}}),'src');
});

test('action and target previews omit script bodies and typed/submitted text', () => {
  for(const [name,args,expected] of [
    ['process',{action:'log',session_id:'proc_ab12',offset:10,limit:20,data:'HIDDEN'},'Log process · proc_ab12 · offset 10 · limit 20'],
    ['process',{action:'wait',session_id:'proc_ab12',timeout:30},'Wait process · proc_ab12 · timeout 30s'],
    ['browser_click',{ref:'@e5'},'Click @e5'],
    ['browser_type',{ref:'@e6',text:'HIDDEN'},'Type into @e6'],
    ['browser_press',{key:'Enter'},'Press Enter'],
    ['browser_scroll',{direction:'down'},'Scroll down'],
    ['browser_snapshot',{full:true},'Full snapshot'],
    ['browser_back',{},'Go back'],
    ['browser_console',{expression:'HIDDEN'},'Inspect browser console'],
    ['browser_exec',{code:'HIDDEN'},'Run browser script'],
    ['execute_code',{code:'HIDDEN'},'Run Python script'],
    ['skill_view',{name:'pdf',file_path:'references/forms.md'},'pdf · references/forms.md'],
    ['skill_manage',{action:'patch',name:'pdf',new_string:'HIDDEN'},'patch · pdf'],
    ['terminal',{command:'git status --short',workdir:'src',background:true},'git status --short · cwd src · background'],
    ['delegate_task',{action:'stop',subagent_id:'agent_1',message:'HIDDEN'},'Stop subagents · agent_1'],
  ]) assert.equal(toolDetail({name,args}),expected,name);
  assert.equal(toolDetail({name:'browser_type',args:{text:'HIDDEN'}}),'Arguments/output not recorded');
  assert.equal(toolDetail({name:'browser_press',args:{key:'HIDDEN'}}),'Arguments/output not recorded');
});

test('bounded parallel children retain multiple targets and safe summary separators round trip', () => {
  const result = toolDetail({name:'multi_tool_use.parallel',args:{tool_uses:[
    {name:'web_search',args:{query:'public search words '.repeat(80)}},
    {name:'read_file',args:{path:'docs/guide.md',offset:10,limit:5}},
    {name:'unknown_tool',args:{code:'HIDDEN'}},
  ]}});
  assert.ok(result.includes('read_file: docs/guide.md · offset 10 · limit 5'));
  assert.ok(result.includes('unknown_tool'));
  assert.ok(result.length <= 360);
  for(const [name,detail] of [['read_file','docs/guide.md · offset 10 · limit 5'],['terminal','git status --short · cwd src · background'],['delegate_task','2 tasks: Inspect UI · Run tests'],['process','Log process · proc_ab12 · offset 10 · limit 20']]) {
    assert.equal(toolDetail({name,summary:name+': '+detail}),detail);
  }
  assert.equal(toolDetail({name:'delegate_task',args:{tasks:[{goal:'safe words '.repeat(60)+'password HIDDEN'},{goal:'Run tests'}]}}),'2 tasks: Run tests');
});

test('terminal metadata is parsed only on summary paths with independent screening', () => {
  for(const workdir of ["/tmp/project's working tree", '/tmp/project (copy)']) {
    const detail = `git status · cwd ${workdir} · background`;
    assert.equal(toolDetail({name:'terminal',summary:'terminal: '+detail,content:'unrelated raw log'}),detail);
    assert.equal(toolDetail({name:'terminal',args:{command:'git status',workdir,background:true}}),detail);
  }
  for(const text of [
    'git status · unexpected hunter2', 'git status · background · cwd src',
    'git status · cwd src · cwd other', 'git status · cwd /tmp/password-hunter2',
    'git status · cwd src · background hunter2', 'git status · cwd src && printf HIDDEN',
    'git status · cwd --import data:text/javascript,HIDDEN',
    'curl --cert identity.pem:hunter2 · cwd src',
    'node --import data:text/javascript,HIDDEN · cwd src',
  ]) assert.equal(toolDetail({name:'terminal',summary:'terminal: '+text}),'Details omitted for privacy',text);
  for(const workdir of ['/tmp/password-hunter2','src && printf HIDDEN','--import data:text/javascript,HIDDEN','src · background']) {
    assert.equal(toolDetail({name:'terminal',args:{command:'git status',workdir,background:true}}),'git status · background');
  }
  for(const command of ['git status · cwd /tmp/public', 'git status · cwd /tmp/public · background']) {
    assert.equal(toolDetail({name:'terminal',args:{command},summary:'terminal: git status'}),'Details omitted for privacy');
  }
});

test('URL decoding cannot spill hidden URL suffixes or normalize away sensitive paths', () => {
  for(const url of ['https://example.com/docs?x=alice%20HIDDEN','https://alice%20HIDDEN:pwd@example.com/docs','https://example.com/password/../public','https://example.com/%2570assword/../public','https://example.com/%2525252570assword/HIDDEN','https://example.com/docs?x=alice%2520HIDDEN']) {
    assert.equal(toolDetail({name:'web_search',args:{query:url}}),'Details omitted for privacy',url);
  }
  assert.equal(toolDetail({name:'web_search',args:{query:'https%3A%2F%2Fu%3Ap%40example.com%2Fdocs%3Fx%3DHIDDEN'}}),'https://example.com/docs');
});

test('saved results and native live summary share a useful sanitized detail', () => {
  assert.equal(toolDetail({tool:'terminal',summary:'terminal: pytest tests -q'}), 'pytest tests -q');
  assert.equal(toolDetail({name:'read_file',summary:'read_file: /home/lindayi/projects/hermes-mobile/frontend/ui.mjs'}), '/home/lindayi/projects/hermes-mobile/frontend/ui.mjs');
  assert.equal(toolDetail({name:'terminal',summary:'terminal'}), 'Arguments/output not recorded');
  assert.equal(toolDetail({name:'terminal',summary:'terminal: curl https://user:pass@example.com/docs?token=hidden#hidden'}), 'curl https://example.com/docs');
  assert.equal(toolDetail({name:'terminal',summary:'terminal: password=hidden'}), 'Details omitted for privacy');
});
