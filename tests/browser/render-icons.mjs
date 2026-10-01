import {createRequire} from 'node:module';
import {readFile} from 'node:fs/promises';
const {chromium}=createRequire('/usr/local/lib/hermes-agent/package.json')('playwright');
const browser=await chromium.launch({executablePath:'/usr/bin/google-chrome',headless:true,args:['--no-sandbox']});
try {
  const page=await browser.newPage();
  const svg=await readFile('frontend/icons/hermes.svg','utf8');
  for(const [size,name] of [[192,'icon-192'],[512,'icon-512'],[180,'apple-touch-icon']]) {
    await page.setViewportSize({width:size,height:size});
    await page.setContent(`<style>html,body{margin:0;background:#9f4c35}svg{width:100%;height:100%}</style>${svg}`);
    await page.screenshot({path:`frontend/icons/${name}.png`});
  }
} finally {await browser.close();}
