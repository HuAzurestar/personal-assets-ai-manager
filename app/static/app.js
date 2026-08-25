const money = new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY" });
const now = () => new Date().toISOString().slice(0, 16);
document.querySelectorAll('input[type="datetime-local"]').forEach(input => input.value = now());

async function request(url, options) { const response = await fetch(url, options); if (!response.ok) throw new Error(await response.text()); return response.status === 204 ? null : response.json(); }
async function refresh() {
  const [summary, bills] = await Promise.all([request('/api/dashboard'), request('/api/bills')]);
  const values = [summary.total_assets, summary.income, summary.spending, summary.bill_count];
  document.querySelectorAll('#summary strong').forEach((node, index) => node.textContent = index === 3 ? values[index] : money.format(values[index]));
  document.querySelector('#bills').innerHTML = bills.map(bill => `<tr><td>${new Date(bill.occurred_at).toLocaleString()}</td><td>${bill.merchant}</td><td class="${bill.amount < 0 ? 'negative' : 'positive'}">${money.format(bill.amount)}</td><td>${bill.category}</td><td>${bill.tags.join('、')}</td><td><button class="delete" data-id="${bill.id}">删除</button></td></tr>`).join('') || '<tr><td colspan="6">尚无账单</td></tr>';
}
function bindForm(id, url) { document.querySelector(id).addEventListener('submit', async event => { event.preventDefault(); const form = event.currentTarget; const body = Object.fromEntries(new FormData(form)); body.amount &&= Number(body.amount); body.balance &&= Number(body.balance); try { const item = await request(url, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) }); if (id === '#bill-form') document.querySelector('#tag-result').textContent = `已归类为「${item.category}」：${item.tags.join('、')}`; form.reset(); document.querySelectorAll('input[type="datetime-local"]').forEach(input => input.value = now()); refresh(); } catch (error) { alert(`保存失败：${error.message}`); } }); }
bindForm('#bill-form', '/api/bills'); bindForm('#asset-form', '/api/assets');
document.querySelector('#bills').addEventListener('click', async event => { if (event.target.matches('.delete')) { await request(`/api/bills/${event.target.dataset.id}`, {method:'DELETE'}); refresh(); } });
refresh();
