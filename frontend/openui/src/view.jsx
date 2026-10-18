import React, { createContext, useContext, useEffect, useState } from 'react';
import { Renderer, createLibrary, createParser, defineComponent } from '@openuidev/react-lang';
import { z } from 'zod/v4';
import catalog from '../../../assets/openui/catalog.json';

const Snapshot = createContext(null);
const kinds = { expense: '支出', income: '收入', refund: '退款', transfer: '转账' };
const states = { new: '新增', existing: '已存在', linked: '关联现有记录', excluded: '排除', review: '待复核' };
const useData = ref => {
  const view = useContext(Snapshot);
  if (!Object.hasOwn(view.datasets, ref)) throw new Error('Missing data reference');
  return [view.datasets[ref], view];
};

function MetricCard({ dataRef }) {
  const [data, view] = useData(dataRef);
  return <section className="card metric" data-component="Metric">
    <h2>{data.label}</h2><div className="amount">{data.value}<small>{view.currency}</small></div>
    <details><summary>金额依据 · {data.evidence.length} 条</summary>
      <ul>{data.evidence.map(id => <li key={id}>{id}</li>)}</ul>
    </details>
  </section>;
}

function BreakdownCard({ dataRef }) {
  const [data, view] = useData(dataRef);
  const max = Math.max(1, ...data.rows.map(row => Number(row.amount)));
  return <section className="card wide" data-component="Breakdown"><h2>{data.label}</h2>
    <p className="muted">{view.period.label} · 金额（{view.currency}）</p>
    {data.rows.length ? <ul className="bars">{data.rows.map(row => <li key={row.label}>
      <span>{row.label}</span><div className="bar-track" aria-hidden="true"><div style={{ width: `${Number(row.amount) / max * 100}%` }} /></div>
      <strong>{row.amount}</strong>
    </li>)}</ul> : <p>本期间没有已记录支出。</p>}
  </section>;
}

function BudgetCard({ dataRef }) {
  const [data, view] = useData(dataRef);
  return <section className="card wide" data-component="Budget"><h2>预算执行</h2>
    <p className="muted">实际支出范围：{view.period.label} · {view.currency}。不足整月时，仅比较已选期间。</p>
    {data.length ? data.map(row => <article className="budget" key={row.id}>
      <div className="spread"><h3>{row.category}</h3><span>{row.month || '通用预算'}</span></div>
      <p>已用 <strong>{row.spent}</strong> / 预算 {row.limit} · 剩余 {row.remaining}</p>
      <div className="bar-track" role="img" aria-label={row.pct == null ? '预算比例不可计算' : `已用 ${row.pct}%`}>
        <div className={row.over ? 'over' : ''} style={{ width: `${Math.min(100, Math.max(0, row.pct ?? 0))}%` }} />
      </div><p className="muted">{row.pct == null ? '预算为零，比例不可计算' : `已用 ${row.pct}%`}</p>
    </article>) : <p>本期间未设置预算。</p>}
  </section>;
}

function RowsCard({ dataRef }) {
  const [data] = useData(dataRef);
  const [query, setQuery] = useState('');
  const [state, setState] = useState('all');
  const [page, setPage] = useState(0);
  const importing = dataRef === 'import_rows';
  const filtered = data.filter(r => (state === 'all' || r.state === state) &&
    [r.id, r.merchant, r.category, r.occurred_at].some(v => String(v).toLocaleLowerCase().includes(query.toLocaleLowerCase())));
  const visible = filtered.slice(page * 20, (page + 1) * 20);
  const title = importing ? '来源记录' : '交易依据';
  return <section className="card full" data-component={importing ? 'ImportRows' : 'Transactions'}>
    <h2>{title}</h2><div className="filters">
      <label>查找记录<input type="search" value={query} onChange={e => { setQuery(e.target.value); setPage(0); }} placeholder="商户、分类、日期或记录 ID" /></label>
      {importing && <label>记录状态<select value={state} onChange={e => { setState(e.target.value); setPage(0); }}>
        <option value="all">全部</option>{Object.entries(states).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
      </select></label>}
    </div>
    <div className="table-wrap"><table><caption>{title} · 显示 {filtered.length} 条，筛选不改变报告指标</caption>
      <thead><tr>{(importing ? ['来源行', '状态', '日期', '类型', '商户 / 分类', '金额 / 币种', '记录 ID'] : ['日期', '类型', '商户 / 分类', '金额 / 币种', '记录 ID']).map(label => <th key={label} scope="col">{label}</th>)}</tr></thead>
      <tbody>{visible.map((r, index) => <tr key={`${r.line || r.id}-${index}`}>
        {importing && <><td>{r.line}</td><td>{states[r.state] || r.state}</td></>}
        <td>{r.occurred_at.slice(0, 10)}</td><td>{kinds[r.type] || r.type}{r.excluded && '（不计总额）'}</td>
        <td>{r.merchant || '未填商户'}<small>{r.category}</small></td><td className="number">{r.amount} {r.currency}</td><td className="record-id">{r.id || '尚未分配'}</td>
      </tr>)}</tbody></table></div>
    {!visible.length && <p>没有符合条件的记录。</p>}
    <nav className="pager" aria-label={`${title}分页`}>
      <button disabled={!page} onClick={() => setPage(page - 1)}>上一页</button><span>第 {page + 1} 页</span>
      <button disabled={(page + 1) * 20 >= filtered.length} onClick={() => setPage(page + 1)}>下一页</button>
    </nav>
  </section>;
}

function ImportSummaryCard({ dataRef }) {
  const [data] = useData(dataRef);
  const labels = { count: '来源记录', new: '新增', existing: '已存在 / 关联', excluded: '已排除', review: '待复核', errors: '解析失败' };
  return <section className="card full" data-component="ImportSummary"><h2>导入概览</h2>
    <div className="counts">{Object.entries(labels).map(([key, label]) => <div key={key}><span>{label}</span><strong>{data[key]}</strong></div>)}</div>
  </section>;
}

function IssuesCard({ dataRef }) {
  const [data] = useData(dataRef);
  return <section className="card full" data-component="Issues"><h2>待核对事项</h2>
    {data.length ? <ul>{data.map((row, index) => <li key={index}>{row.line != null && `来源第 ${row.line} 行：`}{row.reason}
      {!!row.related_ids.length && <small>相关记录：{row.related_ids.join('、')}</small>}</li>)}</ul> : <p>准备时未发现待核对项；这不代表已经导入。</p>}
  </section>;
}

const renderers = { Metric: MetricCard, Breakdown: BreakdownCard, Budget: BudgetCard,
  Transactions: RowsCard, ImportSummary: ImportSummaryCard, ImportRows: RowsCard, Issues: IssuesCard };
const components = Object.entries(catalog.components).map(([name, spec]) => {
  const Component = renderers[name];
  if (!Component) throw new Error(`No renderer for ${name}`);
  return defineComponent({ name, description: `Read-only ${name} from a verified snapshot reference`,
    props: z.object({ dataRef: z.enum(spec.refs) }),
    component: ({ props }) => <Component dataRef={props.dataRef} /> });
});
const LedgerView = defineComponent({ name: 'LedgerView', description: 'Read-only ledger component composition',
  props: z.object({ children: z.array(z.union(components.map(c => c.ref))) }),
  component: ({ props, renderNode }) => <div className="grid">{renderNode(props.children)}</div> });
export const library = createLibrary({ root: 'LedgerView', components: [LedgerView, ...components] });

export function validateView(view) {
  if (view.version !== 1 || view.renderer !== catalog.renderer || view.layout.version !== 1 ||
      !['report', 'import'].includes(view.kind)) throw new Error('Unsupported view version');
  const blocks = view.layout.blocks;
  if (!Array.isArray(blocks) || !blocks.length || blocks.length > 24) throw new Error('Invalid blocks');
  const seen = new Set();
  for (const block of blocks) {
    const spec = catalog.components[block.component];
    const key = `${block.component}:${block.ref}`;
    if (!spec || !spec.kinds.includes(view.kind) || !spec.refs.includes(block.ref) ||
        !Object.hasOwn(view.datasets, block.ref) || seen.has(key) || Object.keys(block).sort().join() !== 'component,ref') throw new Error('Invalid data reference');
    seen.add(key);
  }
  const expected = `root = LedgerView([${blocks.map((_, i) => `b${i}`).join(', ')}])\n` +
    blocks.map((b, i) => `b${i} = ${b.component}(${JSON.stringify(b.ref)})`).join('\n');
  if (view.lang !== expected) throw new Error('Unexpected executable description');
  const result = createParser(library.toJSONSchema()).parse(view.lang);
  if (!result.root || result.meta.incomplete || result.meta.errors.length || result.meta.unresolved.length ||
      result.meta.orphaned.length || result.queryStatements.length || result.mutationStatements.length ||
      Object.keys(result.stateDeclarations).length) throw new Error('Incomplete OpenUI description');
  return result;
}

export function showFallback() {
  document.getElementById('fallback').hidden = false;
  document.getElementById('status').textContent = '交互视图不可用，已显示同一快照的文本。';
}

class Boundary extends React.Component {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch() { showFallback(); }
  render() { return this.state.failed ? null : this.props.children; }
}

function Content({ view }) {
  const [failed, setFailed] = useState(false);
  const [showText, setShowText] = useState(false);
  useEffect(() => {
    if (failed) { showFallback(); return; }
    document.getElementById('fallback').hidden = showText ? false : true;
    document.getElementById('status').textContent = '只读快照 · 本地展示';
    return () => { document.getElementById('fallback').hidden = false; };
  }, [failed, showText]);
  if (failed) return null;
  const coverage = view.coverage;
  return <Snapshot.Provider value={view}>
    <header><div className="eyebrow">LAZY LEDGER / 账务管家</div><h1>{view.period.label}</h1>
      <p>币种 {view.currency} · 修订 {view.revision} · 生成于 {view.generated_at}</p>
      <p className="notice">{coverage.notice}</p>
      {view.kind === 'report' ? <p>已记录 {coverage.recorded_rows} 条 · 未指定账户 {coverage.unassigned_rows} 条 · 期初未确认账户 {coverage.unknown_openings} 个</p> :
        <p className="notice">{coverage.ready_at_prepare ? '准备时可提交，尚未执行' : '存在未解决事项，不能提交此计划'}</p>}
    </header>
    <Renderer library={library} response={view.lang} isStreaming={false} publishObservability={false}
      onError={errors => { if (errors.length) setFailed(true); }} />
    <footer><button onClick={() => setShowText(!showText)}>{showText ? '收起文本快照' : '查看文本快照'}</button><small>{view.snapshot_id}</small></footer>
  </Snapshot.Provider>;
}

export function App({ view }) { return <Boundary><Content view={view} /></Boundary>; }
