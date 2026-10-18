import { createRoot } from 'react-dom/client';
import { useEffect, useRef, useState } from 'react';
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
// Served by the native Wails runtime, never by the selected remote server.
// @ts-expect-error Native runtime module
import { Call } from '/wails/runtime.js';
import CaptchaField, { type CaptchaFieldHandle } from '../../dashboard/src/pages/Login/CaptchaField';
import type { PublicCaptchaConfig } from '../../dashboard/src/pages/Login/captchaAdapters';
import { useDesktopChrome } from '../../dashboard/src/hooks/useDesktopChrome';
import { loginErrorMessage } from './errorMessage';
import './style.css';

const call = (name: string, ...args: unknown[]) => Call.ByName('main.App.'+name,...args);
void i18n.use(initReactI18next).init({showSupportNotice:false,lng:'zh',fallbackLng:'zh',resources:{zh:{translation:{}},en:{translation:{}}},interpolation:{escapeValue:false}});

export function EmployeeLogin() {
  const chrome = useDesktopChrome();
  const [address,setAddress]=useState('');
  const [username,setUsername]=useState('');
  const [password,setPassword]=useState('');
  const [remember,setRemember]=useState(true);
  const [expanded,setExpanded]=useState(new URLSearchParams(location.search).has('server'));
  const [ready,setReady]=useState(false);
  const [loaded,setLoaded]=useState(false);
  const [en,setEn]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState<unknown>('');
  const [checking,setChecking]=useState(false);
  const [configAddress,setConfigAddress]=useState('');
  const [captcha,setCaptcha]=useState<PublicCaptchaConfig>({provider:'slider'});
  const [reset,setReset]=useState(0);
  const captchaRef=useRef<CaptchaFieldHandle>(null);
  const addressRef=useRef<HTMLInputElement>(null);
  const text=(zh:string,english:string)=>en?english:zh;

  useEffect(()=>{
    if(!loaded)return;
    document.title=en?'Octop Enterprise':'Octop 企业版';
    document.documentElement.lang=en?'en':'zh-CN';
  },[en,loaded]);

  useEffect(()=>{ void Promise.all([call('GetCompanyMode'),call('GetSettings')]).then(([url,settings])=>{
    setAddress(settings.companyUrl || url || '');setEn(settings.locale==='en');void i18n.changeLanguage(settings.locale || 'zh');setLoaded(true);
  }).catch(()=>setLoaded(true)); },[]);

  useEffect(()=>{
    if(!loaded)return;
    let cancelled=false;
    setConfigAddress('');setReady(false);setReset(n=>n+1);setError('');
    if(!address.trim()){setChecking(false);return;}
    setChecking(true);
    const timer=setTimeout(()=>{
      void call('GetLoginConfig',address.trim()).then((config:PublicCaptchaConfig)=>{
        if(cancelled)return;setCaptcha(config);setConfigAddress(address.trim());setChecking(false);
      }).catch((err:unknown)=>{if(!cancelled){setChecking(false);setError(err);}});
    },450);
    return ()=>{cancelled=true;clearTimeout(timer);};
  },[address,loaded]);

  async function chooseLanguage(locale: 'zh' | 'en') {
    try {
      await call('SaveLoginLocale', locale);
      setEn(locale === 'en');
      await i18n.changeLanguage(locale);
    } catch(err) { setError(err); }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if(busy)return;
    if(!address.trim()){setExpanded(true);setError(text('请填写管理员提供的服务器地址','Enter the server address from your administrator'));setTimeout(()=>addressRef.current?.focus(),0);return;}
    if(!configAddress || configAddress!==address.trim() || !ready)return;
    setBusy(true);setError('');
    try {
      const token=await captchaRef.current?.getToken();
      await call('LoginCompany',{address:address.trim(),username,password,captcha_token:token || '',remember});
      setPassword('');
    } catch(err) {setError(err);setReady(false);setReset(n=>n+1);setBusy(false);}
  }

  return <>{chrome && <div className="window-controls" aria-label={text("窗口控制","Window controls")}>{[["minimise","−",text("最小化","Minimize")],["toggle-maximise","□",text("最大化","Maximize")],["close","×",text("关闭","Close")]].map(([action,icon,label])=><button key={action} type="button" aria-label={label} onClick={()=>{(window as unknown as {_wails?:{invoke:(value:string)=>void}})._wails?.invoke("wails:event:emit:desktop:"+action);}}>{icon}</button>)}</div>}<main className="login-page"><form className="login-card" onSubmit={submit}>
    <div className="login-language" role="group" aria-label={text('界面语言', 'Language')}>
      <button type="button" aria-pressed={!en} onClick={()=>void chooseLanguage('zh')}>中文</button>
      <button type="button" aria-pressed={en} onClick={()=>void chooseLanguage('en')}>English</button>
    </div>
    <img className="logo" src="/octop-mascot-peek.webp" alt={text('Octop 企业版','Octop Enterprise')}/><div className="brand">Octop <span className="edition">{text('企业版','Enterprise')}</span></div>
    <h1>{text('员工登录','Employee sign in')}</h1><p className="intro">{text('使用管理员分配的账号登录','Sign in with the account provided by your administrator')}</p>
    <label htmlFor="username">{text('账号','Account')}</label><input id="username" autoComplete="username" placeholder={text('用户名或邮箱','Username or email')} required maxLength={320} value={username} onChange={e=>setUsername(e.target.value)} disabled={busy}/>
    <label htmlFor="password">{text('密码','Password')}</label><input id="password" type="password" autoComplete="current-password" placeholder={text('请输入密码','Password')} required maxLength={4096} value={password} onChange={e=>setPassword(e.target.value)} disabled={busy}/>
    {configAddress && <div className="captcha"><CaptchaField key={configAddress} ref={captchaRef} config={captcha} resetKey={reset} slideHint={text('向右滑动完成验证','Slide right to verify')} slideVerifiedLabel={text('验证通过','Verified')} unsupportedLabel={text('不支持的验证方式，请联系管理员','Unsupported verification; contact your administrator')} onReadyChange={setReady}/></div>}
    <label className="remember"><input type="checkbox" checked={remember} onChange={e=>setRemember(e.target.checked)} disabled={busy}/>{text('记住登录状态','Remember me')}</label>
    <p className="error" role="alert">{loginErrorMessage(error,en)}</p>
    <button className="login" type="submit" disabled={busy || checking || (!!address.trim() && (!ready || !configAddress))}>{busy?text('正在登录…','Signing in…'):text('登录','Sign in')}</button>
    <div className="server-settings"><button className="server-toggle" type="button" aria-expanded={expanded} aria-controls="server-fields" onClick={()=>setExpanded(!expanded)} disabled={busy}><span>{text('服务器地址','Server address')}</span><span aria-hidden="true">{expanded?'⌃':'⌄'}</span></button>
    <div id="server-fields" hidden={!expanded}><label className="sr-only" htmlFor="server-address">{text('服务器地址','Server address')}</label><input ref={addressRef} id="server-address" value={address} onChange={e=>setAddress(e.target.value)} onBlur={()=>{if(address.trim())void call('SaveLoginAddress',address.trim()).catch((err:unknown)=>setError(err));}} placeholder={text('IP 或域名:端口','IP or hostname:port')} autoComplete="off" spellCheck={false} maxLength={2048} disabled={busy}/><p className="hint">{text('地址自动保存，默认使用 HTTPS。内网 HTTP 请填写完整的 http:// 地址。','Address is saved automatically and defaults to HTTPS. For intranet HTTP, enter the full http:// address.')}</p></div>
    {!expanded && address && <p className="saved-address">{address}</p>}
    {checking && <p className="hint" role="status">{text('正在检查服务器…','Checking server…')}</p>}</div>
  </form></main></>;
}

createRoot(document.getElementById('login-root')!).render(<EmployeeLogin/>);
