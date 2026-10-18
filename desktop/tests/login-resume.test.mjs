import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
import {test} from 'node:test';

const html=await readFile(new URL('../../dashboard/public/desktop-login.html',import.meta.url),'utf8');
const source=html.match(/<script id="octop-resume">([\s\S]*?)<\/script>/)[1];

async function fixture({token='saved-session',hash='#resume',status=200,renewed=null,error=false,bridge='wails'}={}) {
  const local=new Map(token?[['auth_token',token]]:[]),temporary=new Map();
  const events=[],navigation=[],requests=[];
  const elements={status:{textContent:''},retry:{hidden:true,onclick:null}};
  const timers=[];
  const window={};
  const connect=()=>{window._wails={invoke:value=>events.push(value)};};
  if(bridge==='wails')connect();
  if(bridge==='webview2'){
    const webview={postMessage(value){assert.equal(this,webview);events.push(value);}};
    window.chrome={webview};
  }
  const storage=map=>({getItem:key=>map.get(key)??null,setItem:(key,value)=>map.set(key,value),removeItem:key=>map.delete(key)});
  const fetch=async(path,options)=>{requests.push({path,options});if(error)throw Error('offline');return {status,ok:status>=200&&status<300,headers:{get:()=>renewed}};};
  runInNewContext(source,{location:{hash,replace:path=>navigation.push(path)},window,localStorage:storage(local),sessionStorage:storage(temporary),document:{documentElement:{},getElementById:id=>elements[id]},fetch,setTimeout:fn=>timers.push(fn)});
  await new Promise(resolve=>setImmediate(resolve));
  return {local,temporary,events,navigation,requests,elements,connect,tick:async()=>{timers.shift()?.();await new Promise(resolve=>setImmediate(resolve));},recover:()=>{status=200;error=false;}};
}

test('Windows acknowledges through WebView2 before Wails injects its runtime',async()=>{
  const p=await fixture({bridge:'webview2'});
  assert.equal(p.events[0],'wails:event:emit:desktop:login-resumed');
  assert.deepEqual(p.navigation,['/chat']);
  assert.equal(p.events.at(-1),'wails:runtime:ready');
  assert.equal(p.local.get('auth_token'),'saved-session');
});

test('navigation waits for a delayed native bridge so startup cannot lose its acknowledgement',async()=>{
  const p=await fixture({bridge:'delayed'});
  assert.deepEqual(p.navigation,[]);
  assert.deepEqual(p.requests,[]);
  p.connect();
  await p.tick();
  assert.equal(p.events[0],'wails:event:emit:desktop:login-resumed');
  assert.deepEqual(p.navigation,['/chat']);
});

test('remembered login restores directly from the page without native ExecJS',async()=>{
  const p=await fixture();
  assert.deepEqual(p.navigation,['/chat']);
  assert.deepEqual(p.events,['wails:event:emit:desktop:login-resumed','wails:runtime:ready']);
  assert.equal(p.requests[0].path,'/api/auth/me');
  assert.equal(p.requests[0].options.headers.Authorization,'Bearer saved-session');
});

test('renewed sessions are persisted before entering the dashboard',async()=>{
  const p=await fixture({renewed:'renewed-session'});
  assert.equal(p.local.get('auth_token'),'renewed-session');
  assert.deepEqual(p.navigation,['/chat']);
});

test('expired sessions return to the bundled sign-in form',async()=>{
  const p=await fixture({status:401});
  assert.equal(p.local.has('auth_token'),false);
  assert.equal(p.events.at(-1),'wails:event:emit:desktop:show-login');
  assert.deepEqual(p.navigation,[]);
});

test('a server restart preserves the session and permits retry',async()=>{
  for(const options of [{status:503},{error:true}]) {
    const p=await fixture(options);
    assert.equal(p.local.get('auth_token'),'saved-session');
    assert.equal(p.elements.retry.hidden,false);
    assert.deepEqual(p.events,['wails:event:emit:desktop:login-resumed']);
    assert.deepEqual(p.navigation,[]);
    p.recover();
    await p.elements.retry.onclick();
    assert.equal(p.requests.length,2);
    assert.equal(p.local.get('auth_token'),'saved-session');
    assert.deepEqual(p.navigation,['/chat']);
  }
});

test('missing sessions request sign-in and nonce handoffs remain untouched',async()=>{
  const missing=await fixture({token:null});
  assert.deepEqual(missing.events,['wails:event:emit:desktop:show-login']);
  assert.equal(missing.requests.length,0);
  const fresh=await fixture({hash:'#fresh-login-nonce'});
  assert.deepEqual(fresh.events,[]);
  assert.equal(fresh.requests.length,0);
  assert.equal(fresh.local.get('auth_token'),'saved-session');
});
