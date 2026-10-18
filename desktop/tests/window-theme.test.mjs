import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
import {test} from 'node:test';

const source=await readFile(new URL('../src/assets/window-theme.js',import.meta.url),'utf8');
const handoff=await readFile(new URL('../src/assets/login-handoff.js',import.meta.url),'utf8');

function fixture(color='rgb(14, 14, 14)',rootColor='rgba(0, 0, 0, 0)') {
  const calls=[],observers=[],media=[];
  const window={_wails:{invoke:value=>calls.push(value)},matchMedia:()=>({addEventListener:(_,fn)=>media.push(fn)}),addEventListener(){}};
  const document={body:{},documentElement:{}};
  const context={window,document,getComputedStyle:el=>({backgroundColor:el===document.body?color:rootColor}),MutationObserver:class{constructor(fn){observers.push(fn);}observe(){}}};
  const install=()=>runInNewContext(source,context);
  install();
  return {calls,observers,media,install,setColor:value=>{color=value;}};
}

test('native resize background follows the rendered dark/light page and explicit preferences',()=>{
  const p=fixture();
  assert.deepEqual(p.calls,['wails:event:emit:desktop:background:14:14:14']);
  p.setColor('rgb(245, 246, 248)');
  p.observers[0]();
  assert.equal(p.calls.at(-1),'wails:event:emit:desktop:background:245:246:248');
  p.setColor('rgb(14, 14, 14)');
  p.media[0]();
  assert.equal(p.calls.at(-1),'wails:event:emit:desktop:background:14:14:14');
});

test('login pages with transparent bodies use the root background',()=>{
  const p=fixture('rgba(0, 0, 0, 0)','rgb(14, 14, 14)');
  assert.deepEqual(p.calls,['wails:event:emit:desktop:background:14:14:14']);
});

test('repeated installation and unchanged colors do not repeat native background updates',()=>{
  const p=fixture();
  p.install();p.observers[0]();p.media[0]();
  assert.equal(p.calls.length,1);
  assert.equal(p.observers.length,1);
  p.setColor('rgba(0, 0, 0, 0)');p.observers[0]();
  assert.equal(p.calls.length,1);
});

test('fresh sign-in cannot leave the handoff page before its native completion event is sent',()=>{
  const saved=new Map(),calls=[],navigation=[];
  const store={removeItem:key=>saved.delete(key),setItem:(key,value)=>saved.set(key,value)};
  const window={};
  const context={window,location:{origin:'https://company.test',pathname:'/desktop-login.html',hash:'#nonce',replace:path=>navigation.push(path)},localStorage:store,sessionStorage:store,document:{body:{}}};
  runInNewContext(handoff,context);
  const session={origin:'https://company.test',nonce:'nonce',token:'test-token',remember:true};
  context.finishEmployeeLogin(session);
  assert.equal(saved.size,0);
  assert.deepEqual(navigation,[]);
  window._wails={invoke:value=>calls.push(value)};
  context.finishEmployeeLogin(session);
  assert.equal(saved.get('auth_token'),'test-token');
  assert.deepEqual(calls,['wails:event:emit:desktop:login-complete:nonce']);
  assert.deepEqual(navigation,['/chat']);
});
