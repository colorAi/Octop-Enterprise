const conciseMessages: Record<string, [string, string]> = {
  '服务器未就绪或地址不是 Octop 服务入口': [
    '无法连接 Octop，请检查服务器地址',
    'Cannot reach Octop. Check the server address.',
  ],
  '地址未返回有效的 Octop 服务信息': [
    '服务器地址无效，请检查后重试',
    'Invalid server address. Check it and try again.',
  ],
};

export function loginErrorMessage(error: unknown, english: boolean): string {
  if (error == null || error === '') return '';
  const raw = typeof error === 'string'
    ? error
    : typeof error === 'object' && 'message' in error && typeof error.message === 'string'
      ? error.message
      : '';
  const message = raw.replace(/^(?:(?:RuntimeError|Error):\s*)+/, '').trim();
  if (!message) return english ? 'Something went wrong. Try again.' : '操作失败，请重试';
  const parts = message.split(' / ');
  const concise = conciseMessages[parts[0]];
  if (concise) return concise[english ? 1 : 0];
  return parts.length === 2 ? parts[english ? 1 : 0] : message;
}
