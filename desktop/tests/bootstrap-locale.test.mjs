import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
import {test} from 'node:test';

const go=await readFile(new URL('../src/internal/companyui/server.go',import.meta.url),'utf8');
const script=go.match(/const bootstrapDocument = `[\s\S]*?<script>([\s\S]*?)<\/script>/)[1];

function bootstrap(locale,{blocked=false}={}) {
  const saved=new Map([['octop:ui-locale','zh']]),navigation=[];
  const status={textContent:''};
  const document={documentElement:{},querySelector:()=>status};
  runInNewContext(script,{
    URLSearchParams,document,
    location:{search:'?ui_locale='+encodeURIComponent(locale),hash:'#fresh-login-nonce',replace:value=>navigation.push(value)},
    localStorage:{setItem(key,value){if(blocked)throw Error('disabled');saved.set(key,value);}},
  });
  return {saved,navigation,document};
}

test('bootstrap applies the native locale before the dashboard reads old browser preferences',()=>{
  for(const locale of ['en','zh']) {
    const p=bootstrap(locale);
    assert.equal(p.saved.get('octop:ui-locale'),locale);
    assert.equal(p.document.title,locale==='en'?'Octop Enterprise':'Octop 企业版');
    assert.deepEqual(p.navigation,['/desktop-login.html#fresh-login-nonce']);
  }
});

test('invalid locale and unavailable storage cannot interrupt the protected login handoff',()=>{
  const invalid=bootstrap('not-a-locale');
  assert.equal(invalid.saved.get('octop:ui-locale'),'zh');
  assert.deepEqual(invalid.navigation,['/desktop-login.html#fresh-login-nonce']);
  assert.deepEqual(bootstrap('en',{blocked:true}).navigation,['/desktop-login.html#fresh-login-nonce']);
});
