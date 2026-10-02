// Exact metadata comes from the backend code registry, never a second whitelist.
let unitCatalog = null;
let unitCatalogRead = null;

function unitCatalogError(message, code = 'UNIT_DICTIONARY_INVALID') {
  return Object.assign(new Error(message), {code, status: 422});
}

export function installUnitDictionary(items) {
  if (!Array.isArray(items) || !items.length || items.length > 128
      || new TextEncoder().encode(JSON.stringify(items)).length > 32768) {
    throw unitCatalogError('单位字典为空或超出容量；请重新加载');
  }
  const next = new Map();
  for (const item of items) {
    if (!item || Object.keys(item).sort().join(',') !== 'code,dimension,is_default,label,precision,quantum'
        || typeof item.code !== 'string' || !/^[A-Z][A-Z0-9]{1,11}(?:_[0-8])?$/.test(item.code)
        || typeof item.label !== 'string' || !item.label.length || item.label.length > 128
        || !['CURRENCY', 'MASS', 'COUNT'].includes(item.dimension)
        || !Number.isInteger(item.precision) || item.precision < 0 || item.precision > 8
        || typeof item.is_default !== 'boolean' || typeof item.quantum !== 'string'
        || next.has(item.code)) throw unitCatalogError('单位字典格式不完整；请重新加载');
    const quantum = item.precision ? `0.${'0'.repeat(item.precision - 1)}1` : '1';
    if (item.quantum !== quantum && !(item.precision > 0 && item.quantum === `1E-${item.precision}`)) {
      throw unitCatalogError('单位字典精度与最小单位不一致');
    }
    if (item.is_default && (item.dimension !== 'CURRENCY' || item.code.includes('_'))) {
      throw unitCatalogError('单位字典默认币种无效');
    }
    next.set(item.code, Object.freeze({...item}));
  }
  // A suffix belongs to a registered default currency, with its exact scale.
  for (const item of next.values()) {
    if (item.dimension !== 'CURRENCY') continue;
    const [base, suffix] = item.code.split('_');
    if (!next.get(base)?.is_default || (suffix === undefined ? !item.is_default : item.precision !== Number(suffix))) {
      throw unitCatalogError('单位字典币种定义不完整');
    }
    if (item.is_default) for (let precision = 0; precision <= 8; precision++) {
      if (next.get(`${base}_${precision}`)?.dimension !== 'CURRENCY') throw unitCatalogError('单位字典缺少精确币种单位');
    }
  }
  // Do not replace a complete in-memory dictionary with a partial response.
  if (unitCatalog && (next.size !== unitCatalog.size || [...unitCatalog.keys()].some(code => !next.has(code)))) {
    throw unitCatalogError('单位字典缺少已加载单位；请重新加载页面');
  }
  unitCatalog = next;
  return unitChoices();
}

export function unitDefinition(code) {
  if (!unitCatalog) throw unitCatalogError('单位字典尚未加载；请重新加载', 'UNIT_DICTIONARY_UNAVAILABLE');
  const item = unitCatalog.get(code);
  if (!item) throw unitCatalogError(`不支持的单位：${code}`, 'INVALID_UNIT');
  return item;
}

export function currencyPrecision(value) {
  const code = String(value || '').trim().toUpperCase();
  const item = unitDefinition(code);
  if (item.dimension !== 'CURRENCY') throw unitCatalogError(`不是现金币种单位：${code}`, 'INVALID_CURRENCY');
  return item.precision;
}

export function unitPrecision(code) { return unitDefinition(code).precision; }

export function unitChoices(dimension) {
  if (!unitCatalog) throw unitCatalogError('单位字典尚未加载；请重新加载', 'UNIT_DICTIONARY_UNAVAILABLE');
  return [...unitCatalog.values()].filter(item => !dimension || item.dimension === dimension);
}

export function unitLabel(code) {
  const item = unitDefinition(code);
  if (item.dimension === 'CURRENCY') {
    try { return new Intl.DisplayNames(['zh-CN'], {type: 'currency'}).of(code.split('_')[0]); }
    catch { /* The registry label remains available without Intl.DisplayNames. */ }
  }
  return item.label;
}

export async function loadUnitDictionary() {
  if (unitCatalog) return unitChoices();
  if (!unitCatalogRead) {
    unitCatalogRead = (async () => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 10000);
      try {
        // core imports this module; importing the API lazily avoids a static cycle.
        const {request} = await import('../api/client.js');
        const body = await request('/paam/ledger/v1/unit', {cache: 'no-store', signal: controller.signal});
        if (!body || Object.keys(body).join(',') !== 'items') throw unitCatalogError('单位字典响应不完整');
        return installUnitDictionary(body.items);
      } catch (error) {
        if (error.name === 'AbortError') throw unitCatalogError('读取单位字典超时；请重新加载', 'UNIT_DICTIONARY_UNAVAILABLE');
        throw error;
      } finally { clearTimeout(timer); unitCatalogRead = null; }
    })();
  }
  return unitCatalogRead;
}
