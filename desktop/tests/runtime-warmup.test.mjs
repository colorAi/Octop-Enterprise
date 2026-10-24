import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
import {test} from 'node:test';

const source=await readFile(new URL('../src/assets/connection-panel.js',import.meta.url),'utf8');

test('an authenticated company page prepares tools once without starting a task',()=>{
  const calls=[];
  const window={_wails:{invoke:value=>calls.push(value)},dispatchEvent(){}};
  const context={window,document:{body:{}},location:{origin:'http://company.test',pathname:'/chat'},Event:class{},MutationObserver:class{observe(){}},setTimeout:()=>1,clearTimeout(){}};
  runInNewContext(source,context);
  const config={address:'http://company.test',nonce:'account'};
  context.mountConnectionPanel(config);
  context.mountConnectionPanel(config);
  assert.equal(calls.length,1);
  assert.match(calls[0],/:runtime:account:1:status:/);
  assert.ok(!calls.some(call=>call.includes(':start:')));
  void window.__octopRuntime.status({verify_tools:true});
  assert.equal(calls.length,2);
  assert.match(decodeURIComponent(calls[1]), /:status:\{"verify_tools":true\}$/);
});
