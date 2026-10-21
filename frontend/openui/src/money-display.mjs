// Formatting only: never round or convert the core's decimal amount strings.
const symbols = new Map();
export function currencySymbol(currency) {
  const code = String(currency || '').toUpperCase();
  if (symbols.has(code)) return symbols.get(code);
  let symbol = '¤';
  try {
    const part = new Intl.NumberFormat('zh-CN', { style: 'currency', currency: code, currencyDisplay: 'narrowSymbol' })
      .formatToParts(0).find(item => item.type === 'currency');
    if (part && part.value !== code) symbol = part.value;
  } catch { /* Unknown codes keep an explicit generic currency mark. */ }
  symbols.set(code, symbol);
  return symbol;
}

export function currencyName(currency) {
  try {
    const code = String(currency || '').toUpperCase();
    const name = new Intl.DisplayNames(['zh-CN'], { type: 'currency' }).of(code);
    return name && name !== code ? name : '其他货币';
  } catch { return '其他货币'; }
}

export function displayMoney(amount, currency) {
  if (amount == null || amount === '') return '—';
  const value = String(amount);
  const sign = /^[+-]/.test(value) ? value[0] : '';
  return `${sign}${currencySymbol(currency)}${sign ? value.slice(1) : value}`;
}
