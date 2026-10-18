import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
const require=createRequire(new URL('../../dashboard/package.json',import.meta.url));
const {build}=require('esbuild');const {JSDOM}=require('jsdom');
const output=await build({entryPoints:[fileURLToPath(new URL('../login/main.tsx',import.meta.url))],bundle:true,write:false,format:'iife',jsx:'automatic',nodePaths:[fileURLToPath(new URL('../../dashboard/node_modules',import.meta.url))],loader:{'.css':'empty'},define:{'process.env.NODE_ENV':'"production"'},plugins:[{name:'native-test',setup(b){
 b.onResolve({filter:/^\/wails\/runtime\.js$/},()=>({path:'runtime',namespace:'test'}));
 b.onLoad({filter:/.*/,namespace:'test'},()=>({contents:'export const Call={ByName:(...args)=>window.nativeCall(...args)}'}));
 b.onResolve({filter:/\/CaptchaField$/},()=>({path:'captcha',namespace:'captcha'}));
 b.onLoad({filter:/.*/,namespace:'captcha'},()=>({contents:'import {forwardRef,useEffect,useImperativeHandle} from "react";export default forwardRef(function(props,ref){useImperativeHandle(ref,()=>({getToken:async()=>"test-verification"}));useEffect(()=>props.onReadyChange(true),[props.resetKey]);return null;});',resolveDir:fileURLToPath(new URL('../../dashboard',import.meta.url))}));
}}]});
const source=output.outputFiles[0].text;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function fixture(address=''){
 const dom=new JSDOM('<body><div id="login-root"></div></body>',{runScripts:'outside-only',url:'http://wails.localhost/login.html'});const calls=[];
 dom.window.nativeCall=async(name,...args)=>{calls.push({name,args});if(name.endsWith('GetCompanyMode'))return address;if(name.endsWith('GetSettings'))return {locale:'zh'};if(name.endsWith('GetLoginConfig'))return {provider:'slider'};if(name.endsWith('LoginCompany'))throw Error('账号或密码错误');};
 dom.window.eval(source);await sleep(30);return {dom,calls,q:s=>dom.window.document.querySelector(s)};
}
function type(p,id,value){const el=p.q(id);Object.getOwnPropertyDescriptor(p.dom.window.HTMLInputElement.prototype,'value').set.call(el,value);el.dispatchEvent(new p.dom.window.Event('input',{bubbles:true}));}
test('startup is an employee form; server accordion starts collapsed and preserves entered credentials',async()=>{
 const p=await fixture();try{
  assert.equal(p.q('h1').textContent,'员工登录');assert.ok(p.q('#username'));assert.ok(p.q('#password'));assert.equal(p.q('#server-fields').hidden,true);
  type(p,'#username','employee');type(p,'#password','sample-password');p.q('.server-toggle').click();await sleep(10);
  assert.equal(p.q('#server-fields').hidden,false);assert.equal(p.q('#password').value,'sample-password');
  assert.equal(p.q('form').textContent.includes('连接并登录'),false);assert.equal(p.calls.some(x=>x.name.includes('LoginCompany')),false);
 }finally{p.dom.window.close();}
});
test('one submit contains address and credentials; failure stays on this form',async()=>{
 const p=await fixture();try{
  p.q('.server-toggle').click();await sleep(10);type(p,'#server-address','192.0.2.28:8088');type(p,'#username','employee');type(p,'#password','sample-password');await sleep(550);
  assert.equal(p.q('.login').disabled,false);p.q('form').dispatchEvent(new p.dom.window.Event('submit',{bubbles:true,cancelable:true}));await sleep(20);
  const login=p.calls.find(x=>x.name.endsWith('LoginCompany'));assert.equal(login.args[0].address,'192.0.2.28:8088');assert.equal(login.args[0].username,'employee');assert.equal(login.args[0].password,'sample-password');assert.equal(login.args[0].captcha_token,'test-verification');
  assert.match(p.q('[role=alert]').textContent,/账号或密码错误/);assert.equal(p.dom.window.location.pathname,'/login.html');assert.equal(p.q('#username').value,'employee');
 }finally{p.dom.window.close();}
});
test('saved server is loaded without requiring a separate connect action',async()=>{
 const p=await fixture('http://company.internal:8088');try{await sleep(500);assert.equal(p.q('#server-address').value,'http://company.internal:8088');assert.equal(p.q('#server-fields').hidden,true);assert.equal(p.q('.login').disabled,false);}finally{p.dom.window.close();}
});
