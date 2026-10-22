import React, { useEffect, useMemo, useState } from 'react';
import {
  Globe,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
  Wifi,
  WifiOff,
  X,
} from 'lucide-react';

import {
  checkProxy,
  createProxy,
  deleteProxy,
  getProxies,
  updateProxy,
} from '../services/api';
import { confirmAction, notify } from '../services/feedback';
import { ProxyItem } from '../types';
import { PageHeader, PageLoading } from './ui';

/**
 * 代理管理（fork:proxy）。
 *
 * 账号不再手填代理地址，而是从这里选一条：改代理时只需改这一处，所有引用它的
 * 账号下次重连就生效，也不会出现「同一个代理在不同账号里写法不一致」。
 */
/** 后端错误优先展示 detail，其次才是 axios 的通用 message。 */
const errText = (err: any, fallback: string): string =>
  err?.response?.data?.detail || err?.message || fallback;

const ProxyManager: React.FC = () => {
  const [proxies, setProxies] = useState<ProxyItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<ProxyItem | null>(null);
  const [form, setForm] = useState({ name: '', url: '', remark: '', enabled: true });
  const [testingId, setTestingId] = useState<string>('');
  const [testResults, setTestResults] = useState<Record<string, { ok: boolean; message: string }>>({});

  const load = () => {
    setLoading(true);
    getProxies()
      .then(list => setProxies(Array.isArray(list) ? list : []))
      .catch(() => notify('获取代理列表失败', 'error'))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []);

  const openCreate = () => {
    setEditing(null);
    setForm({ name: '', url: '', remark: '', enabled: true });
    setModalOpen(true);
  };

  const openEdit = (item: ProxyItem) => {
    setEditing(item);
    setForm({
      name: item.name || '',
      url: item.url || '',
      remark: item.remark || '',
      enabled: item.enabled !== false,
    });
    setModalOpen(true);
  };

  const save = async () => {
    const url = form.url.trim();
    if (!url) {
      notify('请填写代理地址', 'warning');
      return;
    }
    setSaving(true);
    try {
      if (editing) {
        await updateProxy(editing.id, {
          name: form.name.trim(),
          url,
          remark: form.remark.trim(),
          enabled: form.enabled,
        });
        notify('代理已更新，引用它的账号重连后生效');
      } else {
        await createProxy({
          name: form.name.trim(),
          url,
          remark: form.remark.trim(),
          enabled: form.enabled,
        });
        notify('代理已添加');
      }
      setModalOpen(false);
      load();
    } catch (err: any) {
      notify(errText(err, '保存失败'), 'error');
    } finally {
      setSaving(false);
    }
  };

  const remove = async (item: ProxyItem) => {
    const used = item.account_count || 0;
    if (used > 0) {
      // 严格模式：删掉正在被引用的代理会让这些账号失去出口，
      // 后端也会拒绝，这里先给出可执行的提示
      notify(`该代理正被 ${used} 个账号使用，请先到「账号管理」把它们切换为其它代理或直连`, 'warning');
      return;
    }
    const ok = await confirmAction(`确定删除代理「${item.name || item.url}」吗？`, {
      title: '删除代理',
      confirmLabel: '删除',
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteProxy(item.id);
      notify('代理已删除');
      load();
    } catch (err: any) {
      notify(errText(err, '删除失败'), 'error');
    }
  };

  const test = async (item: ProxyItem) => {
    setTestingId(item.id);
    try {
      const res = await checkProxy(item.url);
      setTestResults(prev => ({
        ...prev,
        [item.id]: { ok: !!res.ok, message: res.message || (res.ok ? '代理连通' : '代理不可用') },
      }));
      notify(res.ok ? `代理连通（${res.elapsed_ms}ms）` : res.message || '代理不可用', res.ok ? 'success' : 'error');
    } catch (err: any) {
      setTestResults(prev => ({
        ...prev,
        [item.id]: { ok: false, message: err?.message || '检测失败' },
      }));
      notify(errText(err, '检测失败'), 'error');
    } finally {
      setTestingId('');
    }
  };

  const summary = useMemo(() => {
    const total = proxies.length;
    const used = proxies.filter(p => (p.account_count || 0) > 0).length;
    return { total, used };
  }, [proxies]);

  return (
    <div className="page-stack">
      <PageHeader
        title="代理管理"
        description="给每个闲鱼账号分配独立出口 IP。账号配置代理后，聊天、登录、滑块验证、商品与订单操作全部走该代理，不会回退直连。"
        icon={Globe}
        badge={
          <span className="status-badge status-badge-info">
            {summary.total} 个代理 · {summary.used} 个在用
          </span>
        }
        actions={
          <>
            <button type="button" className="ios-btn-secondary flex items-center justify-center gap-2 rounded-md px-4 py-2 text-sm" onClick={load} disabled={loading}>
              <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
              刷新
            </button>
            <button type="button" className="ios-btn-primary flex items-center justify-center gap-2 rounded-md px-4 py-2 text-sm" onClick={openCreate}>
              <Plus className="h-4 w-4" />
              新增代理
            </button>
          </>
        }
      />

      <div className="notice-banner notice-banner--info">
        建议使用国内住宅 / 4G 代理，并让同一账号长期固定一个出口。机房 IP、海外 IP
        在闲鱼侧更容易触发风控；带账号密码的 SOCKS 代理无法用于浏览器操作，请改用
        HTTP 代理或免认证 SOCKS5。
      </div>

      <section className="section-panel">
        {loading ? (
          <PageLoading />
        ) : proxies.length === 0 ? (
          <div className="empty-state">
            <Globe className="h-8 w-8" />
            <p className="mt-2 text-sm font-semibold">还没有代理</p>
            <p className="text-xs text-gray-500">新增一条代理后，即可在「账号管理」里给账号选择。</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>名称</th>
                  <th>地址</th>
                  <th>备注</th>
                  <th>使用账号</th>
                  <th>状态</th>
                  <th>连通性</th>
                  <th className="text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {proxies.map(item => {
                  const result = testResults[item.id];
                  return (
                    <tr key={item.id}>
                      <td className="font-semibold">{item.name || '未命名'}</td>
                      <td className="font-mono text-xs">{item.url_display || item.url}</td>
                      <td className="text-xs text-gray-500">{item.remark || '-'}</td>
                      <td>{item.account_count || 0}</td>
                      <td>
                        {item.enabled ? (
                          <span className="status-badge status-badge-success">启用</span>
                        ) : (
                          <span className="status-badge status-badge-warning">停用</span>
                        )}
                      </td>
                      <td>
                        {result ? (
                          <span className={`inline-flex items-center gap-1 text-xs ${result.ok ? 'text-green-600' : 'text-red-600'}`}>
                            {result.ok ? <Wifi className="h-3.5 w-3.5" /> : <WifiOff className="h-3.5 w-3.5" />}
                            {result.message}
                          </span>
                        ) : (
                          <span className="text-xs text-gray-400">未检测</span>
                        )}
                      </td>
                      <td>
                        <div className="flex items-center justify-end gap-2">
                          <button
                            type="button"
                            className="ios-btn-secondary inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs"
                            onClick={() => test(item)}
                            disabled={testingId === item.id}
                          >
                            {testingId === item.id ? (
                              <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                              <Wifi className="h-4 w-4" />
                            )}
                            测试
                          </button>
                          <button type="button" className="ios-btn-secondary inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs" onClick={() => openEdit(item)}>
                            <Pencil className="h-4 w-4" />
                            编辑
                          </button>
                          <button type="button" className="ios-btn-danger inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs" onClick={() => remove(item)}>
                            <Trash2 className="h-4 w-4" />
                            删除
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {modalOpen && (
        <div className="modal-overlay">
          <div className="modal-container" style={{ maxWidth: '32rem' }}>
            <div className="modal-header flex items-start justify-between gap-4">
              <div>
                <h3 className="text-lg font-bold">{editing ? '编辑代理' : '新增代理'}</h3>
                <p className="mt-1 text-xs text-gray-500">
                  支持 http://user:pass@host:port、socks5://host:port（免认证）。
                </p>
              </div>
              <button
                type="button"
                onClick={() => setModalOpen(false)}
                className="rounded-md p-2 hover:bg-gray-100"
                aria-label="关闭"
              >
                <X className="h-5 w-5 text-gray-600" />
              </button>
            </div>

            <div className="modal-body space-y-4">
              <div>
                <label className="field-label">名称</label>
                <input
                  className="ios-input w-full rounded-md px-3 py-2.5"
                  value={form.name}
                  placeholder="例如：上海住宅 01"
                  onChange={e => setForm({ ...form, name: e.target.value })}
                />
              </div>
              <div>
                <label className="field-label">代理地址</label>
                <input
                  className="ios-input w-full rounded-md px-3 py-2.5 font-mono text-sm"
                  value={form.url}
                  placeholder="http://user:pass@1.2.3.4:8080"
                  onChange={e => setForm({ ...form, url: e.target.value })}
                />
              </div>
              <div>
                <label className="field-label">备注</label>
                <input
                  className="ios-input w-full rounded-md px-3 py-2.5"
                  value={form.remark}
                  placeholder="可选，例如：到期时间 / 归属地"
                  onChange={e => setForm({ ...form, remark: e.target.value })}
                />
              </div>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={form.enabled}
                  onChange={e => setForm({ ...form, enabled: e.target.checked })}
                />
                启用（停用后使用它的账号将无法连接，而不是回退直连）
              </label>
            </div>

            <div className="modal-footer">
              <div className="flex w-full gap-2">
                <button
                  type="button"
                  className="ios-btn-secondary flex flex-1 items-center justify-center gap-2 rounded-md px-4 py-2.5 text-sm"
                  onClick={() => setModalOpen(false)}
                >
                  取消
                </button>
                <button
                  type="button"
                  className="ios-btn-primary flex flex-1 items-center justify-center gap-2 rounded-md px-4 py-2.5 text-sm"
                  onClick={save}
                  disabled={saving}
                >
                  {saving && <Loader2 className="h-4 w-4 animate-spin" />}
                  保存
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default ProxyManager;
