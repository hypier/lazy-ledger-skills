import React, { createContext, useContext, useEffect, useState } from 'react';
import { Renderer, createLibrary, createParser, defineComponent } from '@openuidev/react-lang';
import { z } from 'zod/v4';
import catalog from '../../../assets/openui/catalog.json';
import { DayChart, RingChart, Icon } from './charts.jsx';

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
  return <section className="card metric" data-component="Metric" data-metric={dataRef.split('.')[1]}>
    <div className="metric-heading"><h2>{data.label}</h2><Icon name={dataRef.split('.')[1]} /></div><div className="amount">{data.value}<small>{view.currency}</small></div>
    <details><summary>金额依据 · {data.evidence.length} 条</summary>
      <ul>{data.evidence.map(id => <li key={id}>{id}</li>)}</ul>
    </details>
  </section>;
}

function BreakdownCard({ dataRef }) {
  const [data, view] = useData(dataRef);
  const [mode, setMode] = useState('chart');
  const max = Math.max(1, ...data.rows.map(row => Number(row.amount)));
  return <section className="card wide" data-component="Breakdown" data-series={dataRef}>
    <div className="chart-heading"><h2><Icon name="chart" />{data.label}</h2>
      <div className="view-switch" role="group" aria-label={`${data.label}显示方式`}>
        <button type="button" aria-pressed={mode === 'chart'} onClick={() => setMode('chart')}>图表</button>
        <button type="button" aria-pressed={mode === 'list'} onClick={() => setMode('list')}>列表</button>
      </div>
    </div>
    <p className="muted">{view.period.label} · 金额（{view.currency}）</p>
    {data.rows.length ? mode === 'chart' ? (dataRef === 'by_category' ?
      <RingChart rows={data.rows} currency={view.currency} /> :
      <DayChart rows={data.rows} currency={view.currency} period={view.period} />) : <ul className="bars">{data.rows.map(row => <li key={row.label}>
      <span>{row.label}</span><div className="bar-track" aria-hidden="true"><div style={{ width: `${Number(row.amount) / max * 100}%` }} /></div>
      <strong>{row.amount}</strong>
    </li>)}</ul> : <div className="empty-chart"><Icon name="empty" /><p>本期间没有已记录支出。</p></div>}
  </section>;
}

function BudgetCard({ dataRef }) {
  const [data, view] = useData(dataRef);
  return <section className="card wide" data-component="Budget"><h2 className="icon-heading"><Icon name="budget" />预算执行</h2>
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
        {importing && <><td>{r.line}</td><td><span className="badge" data-state={r.state}>{states[r.state] || r.state}</span></td></>}
        <td>{r.occurred_at.slice(0, 10)}</td><td><span className="badge" data-kind={r.type}>{kinds[r.type] || r.type}</span>{r.excluded && '（不计总额）'}</td>
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
    <div className="counts">{Object.entries(labels).map(([key, label]) => <div key={key} data-state={key}><span>{label}</span><strong>{data[key]}</strong></div>)}</div>
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
// These are composable primitives, not page recipes. Recursive child refs keep
// layout decisions with the generating agent.
let allComponents;
const children = z.array(z.lazy(() => z.union(allComponents.map(c => c.ref)))).min(1).max(64);
const tone = z.enum(['neutral', 'blue', 'green', 'coral', 'violet', 'amber']);
const Text = defineComponent({ name: 'Text', description: 'Agent-authored explanation, not an authoritative financial value',
  props: z.object({ text: z.string().max(6000), tone: tone.optional() }),
  component: ({ props }) => <p className="generated-text" data-tone={props.tone}>{props.text}</p> });
const Section = defineComponent({ name: 'Section', description: 'Titled group; compose any children',
  props: z.object({ title: z.string().max(200), children, tone: tone.optional() }),
  component: ({ props, renderNode }) => <section className="generated-section" data-tone={props.tone}><h2>{props.title}</h2><div className="generated-stack">{renderNode(props.children)}</div></section> });
const Stack = defineComponent({ name: 'Stack', description: 'Vertical composition of any components',
  props: z.object({ children }), component: ({ props, renderNode }) => <div className="generated-stack">{renderNode(props.children)}</div> });
const Columns = defineComponent({ name: 'Columns', description: 'Responsive equal columns, collapsing on small screens',
  props: z.object({ children, columns: z.number().int().min(1).max(4).optional() }),
  component: ({ props, renderNode }) => <div className="generated-columns" style={{ '--columns': props.columns || 2 }}>{renderNode(props.children)}</div> });
const Disclosure = defineComponent({ name: 'Disclosure', description: 'Expandable evidence or explanation; works with keyboard',
  props: z.object({ title: z.string().max(200), children }),
  component: ({ props, renderNode }) => <details className="generated-section"><summary>{props.title}</summary><div className="generated-stack">{renderNode(props.children)}</div></details> });
function TabsView({ titles, children: items, renderNode }) {
  const [active, setActive] = useState(0);
  return <section className="generated-tabs"><div className="view-switch" role="group" aria-label="切换内容">
    {titles.map((title, i) => <button key={i} type="button" aria-pressed={active === i} onClick={() => setActive(i)}>{title}</button>)}
    </div><div className="generated-stack">{renderNode(items[active])}</div></section>;
}
const Tabs = defineComponent({ name: 'Tabs', description: 'Switch between children; one title per child',
  props: z.object({ titles: z.array(z.string().max(100)).min(1).max(12), children }),
  component: ({ props, renderNode }) => <TabsView {...props} renderNode={renderNode} /> });
const LedgerView = defineComponent({ name: 'LedgerView', description: 'Root of an agent-designed view; no prescribed content or layout',
  props: z.object({ children }), component: ({ props, renderNode }) => {
    const view = useContext(Snapshot);
    return <div className={view?.mode ? "generated-stack" : "grid"}>{renderNode(props.children)}</div>;
  } });
allComponents = [...components, Text, Section, Stack, Columns, Disclosure, Tabs];
export const library = createLibrary({ root: 'LedgerView', components: [LedgerView, ...allComponents] });

export class ViewError extends Error {
  constructor(code, message, details = []) { super(message); this.code = code; this.details = details; }
}
export function validateLang(lang, kind) {
  if (!['report', 'import'].includes(kind)) throw new ViewError('snapshot-kind', 'Unknown snapshot kind');
  if (typeof lang !== 'string' || !lang.trim() || lang.length > 100000) throw new ViewError('source-size', 'OpenUI source must contain 1–100000 characters');
  const result = createParser(library.toJSONSchema()).parse(lang);
  if (!result.root || result.root.typeName !== 'LedgerView' || result.root.statementId !== 'root' || result.meta.incomplete ||
      result.meta.errors.length || result.meta.unresolved.length || result.meta.orphaned.length) {
    throw new ViewError('parse', 'Correct the OpenUI description, unresolved references or unused statements', result.meta);
  }
  if (result.queryStatements.length || result.mutationStatements.length || Object.keys(result.stateDeclarations).length)
    throw new ViewError('capability', 'This offline viewer accepts components only; tools and global state are unavailable');
  const refs = new Set();
  let nodes = 0;
  function visit(node, depth = 0) {
    if (++nodes > 256 || depth > 16) throw new ViewError('complexity', 'Use at most 256 nodes and 16 nesting levels');
    if (!node || node.type !== 'element' || node.partial || node.hasDynamicProps) throw new ViewError('node', 'Only complete component nodes with literal props are supported');
    const component = library.components[node.typeName];
    if (!component) throw new ViewError('component', `Unknown component ${node.typeName}`);
    const { children: items, ...scalarProps } = node.props;
    const propsSchema = component.props.shape.children ? component.props.omit({ children: true }) : component.props;
    const scalarCheck = propsSchema.strict().safeParse(scalarProps);
    if (!scalarCheck.success) throw new ViewError('props', `${node.typeName}: invalid properties`, scalarCheck.error.issues);
    if (component.props.shape.children && (!Array.isArray(items) || items.length < 1 || items.length > 64))
      throw new ViewError('children', `${node.typeName} needs 1–64 component children`);
    const spec = catalog.components[node.typeName];
    if (spec) {
      if (!spec.kinds.includes(kind) || !spec.refs.includes(node.props.dataRef)) throw new ViewError('data-ref', `${node.typeName}: unavailable reference ${node.props.dataRef}`);
      refs.add(node.props.dataRef);
    }
    if (node.typeName === 'Tabs' && node.props.titles.length !== node.props.children.length)
      throw new ViewError('tabs', 'Tabs needs exactly one title per child');
    for (const child of node.props.children || []) visit(child, depth + 1);
  }
  visit(result.root);
  return { result, refs: [...refs] };
}

export function validateView(view) {
  if (view.version !== 1 || view.renderer !== catalog.renderer || !['report', 'import'].includes(view.kind)) throw new ViewError('version', 'Unsupported view version');
  if (view.mode === 'html') {
    if (typeof view.html_document !== 'string' || !view.html_document.startsWith('<!doctype html>')) throw new ViewError('html', 'Missing normalized HTML document');
    return;
  }
  const checked = validateLang(view.lang, view.kind);
  for (const ref of checked.refs) if (!Object.hasOwn(view.datasets, ref)) throw new ViewError('data-ref', `Missing dataset ${ref}`);
  return checked.result;
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
    {view.mode === 'html' ? <section className="html-artifact"><p className="muted">AI 自定义展示 · 数值请结合下方原始快照核对</p>
      <iframe title="AI 生成的账本展示" sandbox="allow-scripts" referrerPolicy="no-referrer" srcDoc={view.html_document} />
      </section> : <Renderer library={library} response={view.lang} isStreaming={false} publishObservability={false}
      onError={errors => { if (errors.length) setFailed(true); }} />}
    <footer><button onClick={() => setShowText(!showText)}>{showText ? '收起文本快照' : '查看文本快照'}</button><small>{view.snapshot_id}</small></footer>
  </Snapshot.Provider>;
}

export function App({ view }) { return <Boundary><Content view={view} /></Boundary>; }
