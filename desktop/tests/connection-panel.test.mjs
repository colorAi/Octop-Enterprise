import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { test } from 'node:test';
const require = createRequire(new URL('../../dashboard/package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const script = await readFile(new URL('../src/assets/connection-panel.js', import.meta.url), 'utf8');
function page(path, origin = 'http://127.0.0.1:41000') {
 const dom = new JSDOM('<body></body>', {runScripts:'outside-only',url:origin+path});
 const events=[];dom.window._wails={invoke:value=>{
  if(value.startsWith('wails:event:emit:desktop:runtime:secret-page-nonce:1:status:')) {
   dom.window.__octopRuntimeReply('secret-page-nonce',{id:'1',result:{ready:true}});
  } else events.push(value);
 }};
 const install=()=>dom.window.eval('('+script+')')({address:'http://127.0.0.1:41000',nonce:'secret-page-nonce'});
 install();
 return {dom,events,install,host:dom.window.document.getElementById('octop-connection-settings')};
}
test('no extra buttons or dialogs on the employee workspace',()=>{
 const p=page('/chat');assert.equal(p.host,null);assert.equal(p.dom.window.document.body.children.length,0);
 assert.ok(p.dom.window.octopDesktopWorkspace);assert.deepEqual(p.events,[]);p.dom.window.close();
});
test('login handoff and unrelated origins receive no native workspace bridge',()=>{
 for(const [path,origin] of [['/desktop-login.html','http://127.0.0.1:41000'],['/chat','http://external.test'],['/login.html','http://wails.localhost']]){
 const p=page(path,origin);assert.equal(p.dom.window.octopDesktopWorkspace,undefined);assert.deepEqual(p.events,[]);p.dom.window.close();}
});
test('expired login returns to the bundled employee form',()=>{
 const p=page('/login');assert.equal(p.host,null);assert.deepEqual(p.events,['wails:event:emit:desktop:show-login']);p.dom.window.close();
});
test('native response must match the request nonce, reinstall preserves pending requests',async()=>{
 const p=page('/chat');const bridge=p.dom.window.octopDesktopWorkspace;
 const result=bridge.status();p.install();assert.equal(bridge,p.dom.window.octopDesktopWorkspace);
 assert.deepEqual(p.events,['wails:event:emit:desktop:workspace:secret-page-nonce:2:status:']);
 p.dom.window.__octopWorkspaceReply('wrong',{id:'2',state:{state:'bad'}});
 p.dom.window.__octopWorkspaceReply('secret-page-nonce',{id:'2',state:{state:'online'}});
 assert.equal((await result).state,'online');p.dom.window.close();
});
test('folder choice has no script supplied path; pairing accepts a one-use code only',async()=>{
 const p=page('/chat');const bridge=p.dom.window.octopDesktopWorkspace;
 const chosen=bridge.choose('/etc');assert.match(p.events[0],/:choose:$/);
 p.dom.window.__octopWorkspaceReply('secret-page-nonce',{id:'2',state:{path:'/chosen'}});await chosen;
 const pair=bridge.pair('octop-pair.once');assert.match(p.events[1],/:pair:octop-pair.once$/);
 p.dom.window.__octopWorkspaceReply('secret-page-nonce',{id:'3',error:'connection_failed'});
 await assert.rejects(pair,/connection_failed/);p.dom.window.close();
});
test('runtime IPC keeps credentials and initialization native and scopes events to the nonce',async()=>{
 const p=page('/chat');const bridge=p.dom.window.__octopRuntime;
 assert.ok(bridge);assert.equal(bridge.initialize,undefined);assert.equal(bridge.credential,undefined);
 const values=[];const unsubscribe=bridge.subscribe(value=>values.push(value));
 const started=bridge.start({task_id:'task',thread_id:'thread',text:'你好'});
 assert.match(p.events[0],/:runtime:secret-page-nonce:2:start:/);
 p.dom.window.__octopRuntimeReply('wrong',{id:'2',result:{task_id:'foreign'}});
 p.dom.window.__octopRuntimeReply('secret-page-nonce',{id:'2',result:{task_id:'task'}});
 assert.equal((await started).task_id,'task');
 p.dom.window.__octopRuntimeEvent('wrong',{event:'done'});
 p.dom.window.__octopRuntimeEvent('secret-page-nonce',{event:'chunk'});
 assert.equal(values.length,1);unsubscribe();
 p.dom.window.__octopRuntimeEvent('secret-page-nonce',{event:'done'});assert.equal(values.length,1);
 p.dom.window.close();
});
test('configuration refresh checks native work without requesting tool initialization',async()=>{
 const p=page('/chat');const bridge=p.dom.window.__octopRuntime;
 const checked=bridge.refreshStatus();
 assert.match(p.events[0],/:runtime:secret-page-nonce:2:refresh_status:/);
 p.dom.window.__octopRuntimeReply('secret-page-nonce',{id:'2',result:{active_tasks:[]}});
 assert.deepEqual(Array.from((await checked).active_tasks),[]);
 p.dom.window.close();
});
test('image preview passes only receipt fields through nonce IPC without remote fetch',async()=>{
 const p=page('/chat');const bridge=p.dom.window.__octopRuntime;
 p.dom.window.fetch=()=>{throw new Error('preview must not upload/fetch')};
 const result=bridge.previewImage({path:'/work/image.png',sha256:'a'.repeat(64)});
 assert.match(p.events[0],/:runtime:secret-page-nonce:2:preview_image:/);
 const params=JSON.parse(decodeURIComponent(p.events[0].split(':').at(-1)));
 assert.deepEqual(params,{path:'/work/image.png',sha256:'a'.repeat(64)});
 p.dom.window.__octopRuntimeReply('wrong',{id:'2',result:{data_url:'foreign'}});
 p.dom.window.__octopRuntimeReply('secret-page-nonce',{id:'2',result:{data_url:'data:image/png;base64,AQ==',width:1,height:1}});
 assert.equal((await result).data_url,'data:image/png;base64,AQ==');p.dom.window.close();
});
