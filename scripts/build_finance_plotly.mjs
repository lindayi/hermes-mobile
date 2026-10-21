import {build} from 'esbuild';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {dirname,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const output=resolve(process.argv[2]||resolve(root,'frontend/finance'));
const plotly=JSON.parse(await readFile(resolve(root,'node_modules/plotly.js-dist-min/package.json'),'utf8'));
if(plotly.version!=='4.1.2')throw new Error(`Expected Plotly 4.1.2, found ${plotly.version}`);
await mkdir(output,{recursive:true});
const bundle=await build({
 entryPoints:[resolve(root,'node_modules/plotly.js-dist-min/plotly.min.js')],
 bundle:true,format:'iife',globalName:'Plotly',legalComments:'none',
 write:false,
});
let script=bundle.outputFiles[0]?.text;
if(!script)throw new Error('Plotly bundle output is missing');
function decodeLiteral(value){
 const quote=value[0],body=value.slice(1,-1);let result='';
 for(let i=0;i<body.length;i++){
  if(body[i]!=='\\'){result+=body[i];continue;}
  const next=body[++i];
  if(next==='n')result+='\n';else if(next==='r')result+='\r';else if(next==='t')result+='\t';else if(next==='b')result+='\b';else if(next==='f')result+='\f';else if(next==='v')result+='\v';
  else if(next==='x'){result+=String.fromCharCode(parseInt(body.slice(i+1,i+3),16));i+=2;}
  else if(next==='u'){const brace=body[i+1]==='{';if(brace){const end=body.indexOf('}',i+2);result+=String.fromCodePoint(parseInt(body.slice(i+2,end),16));i=end;}else{result+=String.fromCharCode(parseInt(body.slice(i+1,i+5),16));i+=4;}}
  else if(next==='\n')continue;else if(next==='\r'){if(body[i+1]==='\n')i++;}else result+=next;
 }
 return result;
}
const css=[],replacements=[];
const styleCreation=/document\.createElement\((['"])style\1\)/g;let created;
while((created=styleCreation.exec(script))){
 const assignmentMatch=script.slice(created.index).match(/\.textContent\s*=\s*/);
 const assignment=assignmentMatch?created.index+assignmentMatch.index:-1;
 const nextCreation=script.indexOf('document.createElement("style")',created.index+created[0].length);
 if(assignment<0||(nextCreation>=0&&assignment>nextCreation))continue;
 const start=assignment+assignmentMatch[0].length;let quote=script[start];
 if(!['\'','"'].includes(quote))continue;
 let end=start+1,escaped=false;
 for(;end<script.length;end++){if(escaped){escaped=false;continue;}if(script[end]==='\\'){escaped=true;continue;}if(script[end]===quote)break;}
 if(end>=script.length)throw new Error('Unterminated Plotly style string');
 const literal=script.slice(start,end+1),decoded=decodeLiteral(literal);
 if(!decoded.startsWith('.maplibregl-map'))continue;
 css.push(decoded);replacements.push([start,end+1,'""']);
 const elementStart=created.index+created[0].indexOf('style');replacements.push([elementStart,elementStart+5,'div']);
}
if(!css.length)throw new Error('Plotly global stylesheet extraction failed');
for(const [start,end,value] of replacements.sort((a,b)=>b[0]-a[0]))script=script.slice(0,start)+value+script.slice(end);
await Promise.all([
 writeFile(resolve(output,'plotly.bundle.js'),script),
 writeFile(resolve(output,'plotly.bundle.css'),[...new Set(css)].join('\n')),
]);
