function finishEmployeeLogin(session) {
  if (location.origin !== session.origin || location.pathname !== '/desktop-login.html' || location.hash !== '#'+session.nonce) return;
  if (!window._wails || typeof window._wails.invoke !== 'function') return;
  try {
    localStorage.removeItem('auth_token');
    sessionStorage.removeItem('auth_token');
    localStorage.removeItem('octop:active-agent');
    (session.remember ? localStorage : sessionStorage).setItem('auth_token',session.token);
    window._wails.invoke('wails:event:emit:desktop:login-complete:'+session.nonce);
    location.replace('/chat');
  } catch {
    document.body.textContent='无法保存登录状态，请重新打开客户端 / Could not save the login session';
  }
}
