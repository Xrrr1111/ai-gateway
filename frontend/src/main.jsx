import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Activity, KeyRound, Plus, RefreshCw, ShieldCheck } from 'lucide-react';
import './styles.css';

function App() {
  const [token, setToken] = useState('');
  const [clients, setClients] = useState([]);
  const [runs, setRuns] = useState([]);
  const [name, setName] = useState('');
  const [budget, setBudget] = useState('1000000');
  const [newKey, setNewKey] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}`, ...options.headers },
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    return data;
  }

  async function refresh() {
    if (!token) return;
    setLoading(true);
    setError('');
    try {
      const [clientData, runData] = await Promise.all([api('/admin/clients'), api('/admin/runs')]);
      setClients(clientData.clients);
      setRuns(runData.runs);
    } catch (cause) { setError(cause.message); }
    finally { setLoading(false); }
  }

  async function create(event) {
    event.preventDefault();
    setError('');
    try {
      const data = await api('/admin/clients', { method: 'POST', body: JSON.stringify({ name, budget_microusd: Number(budget) }) });
      setNewKey(data.api_key);
      setName('');
      await refresh();
    } catch (cause) { setError(cause.message); }
  }

  const totalSpend = clients.reduce((sum, client) => sum + client.spent_microusd, 0);
  return <div className="shell">
    <header><div className="brand"><span className="brand-icon"><Activity size={20}/></span><div><strong>AI Gateway</strong><small>Usage & access console</small></div></div><div className="status"><span/>Local service</div></header>
    <main>
      <div className="heading"><div><h1>调用管理</h1><p>客户端额度、密钥与最近请求</p></div><button className="icon-button" onClick={refresh} disabled={!token || loading} title="刷新数据" aria-label="刷新数据"><RefreshCw size={18}/></button></div>
      <section className="auth-bar"><KeyRound size={18}/><label htmlFor="admin-token">管理员令牌</label><input id="admin-token" type="password" value={token} onChange={event => setToken(event.target.value)} placeholder="输入后点击连接" autoComplete="off"/><button onClick={refresh} disabled={!token || loading}>连接</button></section>
      {error && <div className="error" role="alert">{error}</div>}
      <div className="metrics"><div><span>客户端</span><strong>{clients.length}</strong></div><div><span>累计成本</span><strong>${(totalSpend / 1_000_000).toFixed(4)}</strong></div><div><span>最近请求</span><strong>{runs.length}</strong></div></div>
      <div className="columns"><section><div className="section-head"><h2>客户端</h2><ShieldCheck size={18}/></div><form onSubmit={create} className="create-form"><input value={name} onChange={event => setName(event.target.value)} placeholder="客户端名称" required maxLength={80}/><input value={budget} onChange={event => setBudget(event.target.value)} type="number" min="1" placeholder="预算（微美元）" required/><button disabled={!token}><Plus size={16}/>新建</button></form>
        {newKey && <div className="key-note"><strong>新密钥只显示一次</strong><code>{newKey}</code><button onClick={() => navigator.clipboard.writeText(newKey)}>复制</button></div>}
        <div className="table-wrap"><table><thead><tr><th>名称</th><th>预算</th><th>已用</th></tr></thead><tbody>{clients.map(client => <tr key={client.id}><td>{client.name}</td><td>${(client.budget_microusd / 1_000_000).toFixed(2)}</td><td>${(client.spent_microusd / 1_000_000).toFixed(4)}</td></tr>)}</tbody></table>{!clients.length && <p className="empty">连接后显示客户端</p>}</div>
      </section><section><div className="section-head"><h2>最近请求</h2><span>最新 100 条</span></div><div className="table-wrap"><table><thead><tr><th>编号</th><th>客户端</th><th>状态</th><th>成本</th></tr></thead><tbody>{runs.map(run => <tr key={run.id}><td>#{run.id}</td><td>{run.client_id}</td><td><span className={`pill ${run.status}`}>{run.status}</span></td><td>${(run.cost_microusd / 1_000_000).toFixed(4)}</td></tr>)}</tbody></table>{!runs.length && <p className="empty">暂无请求记录</p>}</div></section></div>
    </main>
  </div>;
}

createRoot(document.getElementById('root')).render(<App/>);
