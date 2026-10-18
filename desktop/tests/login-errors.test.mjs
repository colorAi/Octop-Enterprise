import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../dashboard/package.json', import.meta.url));
const { buildSync } = require('esbuild');
const compiled = buildSync({
  entryPoints: [new URL('../login/errorMessage.ts', import.meta.url).pathname],
  bundle: true, format: 'cjs', write: false, platform: 'node',
}).outputFiles[0].text;
const module = { exports: {} };
runInNewContext(compiled, { module, exports: module.exports });
const { loginErrorMessage } = module.exports;

test('native login errors show only the selected language without exception names', () => {
  const message = '账号或密码错误 / Incorrect username or password';
  for (const error of [new Error(message), { message }, `RuntimeError: ${message}`, `Error: RuntimeError: ${message}`]) {
    assert.equal(loginErrorMessage(error, false), '账号或密码错误');
    assert.equal(loginErrorMessage(error, true), 'Incorrect username or password');
  }
});

test('server discovery errors provide a short actionable message', () => {
  const error = new Error('服务器未就绪或地址不是 Octop 服务入口 / Server unavailable or not an Octop origin');
  assert.equal(loginErrorMessage(error, false), '无法连接 Octop，请检查服务器地址');
  assert.equal(loginErrorMessage(error, true), 'Cannot reach Octop. Check the server address.');
});

test('plain messages and URLs retain useful details', () => {
  for (const message of ['请填写服务器地址', 'Connection failed: https://company.test:8443/api/setup/status']) {
    assert.equal(loginErrorMessage(new Error(message), false), message);
    assert.equal(loginErrorMessage(new Error(message), true), message);
  }
});

test('empty state stays empty and unknown error shapes get a readable fallback', () => {
  for (const error of [null, undefined, '']) assert.equal(loginErrorMessage(error, false), '');
  assert.equal(loginErrorMessage({}, false), '操作失败，请重试');
  assert.equal(loginErrorMessage({}, true), 'Something went wrong. Try again.');
});
