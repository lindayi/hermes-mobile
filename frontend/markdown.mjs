// Intentionally small Markdown subset. Untrusted text never reaches an HTML parser.
export function renderMarkdown(doc, source) {
  const root=doc.createElement('div');root.className='markdown';
  const element=(name,text)=>{const el=doc.createElement(name);if(text!=null)el.textContent=text;return el;};
  function inline(parent,text) {
    const pattern=/(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\([^\s)]+\))/g;
    let offset=0;
    for(const match of text.matchAll(pattern)) {
      parent.append(doc.createTextNode(text.slice(offset,match.index)));
      const token=match[0];
      if(token.startsWith('`'))parent.append(element('code',token.slice(1,-1)));
      else if(token.startsWith('**'))parent.append(element('strong',token.slice(2,-2)));
      else {
        const parts=/^\[([^\]]+)\]\((.+)\)$/.exec(token);
        let safe=false;
        try {const url=new URL(parts[2]);safe=['https:','http:'].includes(url.protocol) && !url.username && !url.password;}catch{}
        if(safe){const a=element('a',parts[1]);a.href=parts[2];a.rel='noopener noreferrer';a.target='_blank';parent.append(a);}
        else parent.append(doc.createTextNode(token));
      }
      offset=match.index+token.length;
    }
    parent.append(doc.createTextNode(text.slice(offset)));
  }
  const lines=String(source ?? '').split('\n');
  for(let i=0;i<lines.length;i++) {
    const line=lines[i];
    if(line.startsWith('```')) {
      const code=[];for(i++;i<lines.length && !lines[i].startsWith('```');i++)code.push(lines[i]);
      const pre=element('pre');pre.append(element('code',code.join('\n')));
      const block=element('div');block.className='code-block';
      const copy=element('button','Copy code');copy.type='button';copy.className='copy-code';
      copy.addEventListener('click',async()=>{try{await doc.defaultView.navigator.clipboard.writeText(code.join('\n'));copy.textContent='Copied';}catch{copy.textContent='Copy unavailable';}});
      block.append(copy,pre);root.append(block);
    } else if(/^#{1,6} /.test(line)) {const heading=element('h2');inline(heading,line.replace(/^#{1,6} /,''));root.append(heading);}
    else if(/^[-*] /.test(line)) {let list=root.lastElementChild;if(list?.tagName!=='UL'){list=element('ul');root.append(list);}const item=element('li');inline(item,line.slice(2));list.append(item);}
    else if(line.trim()) {const p=element('p');inline(p,line);root.append(p);}
  }
  return root;
}
