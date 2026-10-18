import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
import {test} from 'node:test';
const source=await readFile(new URL('../src/assets/login-handoff.js',import.meta.url),'utf8');
function transfer(overrides={},remember=true){
 const local=new Map([['auth_token','old'],['octop:active-agent','old-agent']]),temporary=new Map([['auth_token','old']]);const events=[],navigation=[];
 const storage=map=>({removeItem:key=>map.delete(key),setItem:(key,value)=>map.set(key,value)});
 runInNewContext('('+source+')(session)',{location:{origin:'http://company.test',pathname:'/desktop-login.html',hash:'#nonce',replace:value=>navigation.push(value),...overrides},localStorage:storage(local),sessionStorage:storage(temporary),window:{_wails:{invoke:value=>events.push(value)}},document:{body:{}},session:{origin:'http://company.test',nonce:'nonce',token:'test-session',remember}});
 return {local,temporary,events,navigation};
}
test('session is delivered only to selected server and nonce; never placed in URL or event',()=>{
 for(const remember of [true,false]){const r=transfer({},remember);assert.equal((remember?r.local:r.temporary).get('auth_token'),'test-session');assert.equal((remember?r.temporary:r.local).has('auth_token'),false);assert.deepEqual(r.navigation,['/chat']);assert.deepEqual(r.events,['wails:event:emit:desktop:login-complete:nonce']);assert.equal(r.local.has('octop:active-agent'),false);}
});
test('redirected origins, other pages and stale navigation cannot receive the session',()=>{
 for(const overrides of [{origin:'http://other.test'},{origin:'https://company.test'},{pathname:'/login'},{hash:'#stale'}]){const r=transfer(overrides);assert.equal(r.local.get('auth_token'),'old');assert.deepEqual(r.events,[]);assert.deepEqual(r.navigation,[]);}
});
