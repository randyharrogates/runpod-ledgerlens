"""Render UI components without a browser using the installed frontend toolchain."""

import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "app" / "frontend"


def test_memo_review_and_live_error_rendering():
    if not shutil.which("node") or not (FRONTEND / "node_modules/typescript").exists():
        pytest.skip("Frontend node_modules and Node are required")
    script = r"""
const fs = require('fs');
const assert = require('assert');
const ts = require('typescript');
require.extensions['.tsx'] = require.extensions['.ts'] = (module, filename) => {
  module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, esModuleInterop: true, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX }
  }).outputText, filename);
};
const React = require('react');
const {renderToStaticMarkup: render} = require('react-dom/server');
const {ResultTable} = require('./src/components/Results.tsx');
const evaluation = render(React.createElement(ResultTable, {value: {
  split_labels: {test_cord: 'Real receipts (CORD v2 test)', empty: 'Empty set'},
  split_notes: {test_cord: 'Live Serverless, one page at a time.'},
  splits: {test_cord: {
    '8b': {pages: 98, row_f1: 0.5, row_precision: 0.6, row_recall: 0.4, header_accuracy: 0.7, seconds_per_page: 2, cost_per_1000_pages: 0.4, gpu_name: 'Test GPU', price_per_hour: 0.72},
    '4b': {pages: 98, row_f1: 0.4, seconds_per_page: 1, cost_per_1000_pages: 0.2},
    '4b-ft': {pages: 98, row_f1: 0.9, seconds_per_page: 1, cost_per_1000_pages: 0.2}
  }, empty: {status: 'empty_set'}}
}}));
for (const label of ['Real receipts (CORD v2 test)', '98 pages', 'Big model', 'Small model', 'Trained small model', 'Lines read right (row F1)', 'Lines it wrote that are right (precision)', 'Real lines it found (recall)', 'Header fields right', 'Seconds per page', '$ per 1,000 pages', 'Test GPU', '$0.72/h', 'No pages in this set.']) assert(evaluation.includes(label), label);
// Rows with no value for any model (e.g. reconciliation on receipts) are hidden.
for (const label of ['after the balance check', 'Page adds up', 'Valid JSON', 'Red flags found (recall)', 'Red-flag precision']) assert(!evaluation.includes(label), label);
assert(evaluation.includes('Live Serverless, one page at a time.'));
assert(evaluation.includes('<strong>90.0%</strong>'));
assert(!evaluation.includes('<strong>50.0%</strong>'));
assert.equal((evaluation.match(/<strong>1.00<\/strong>/g) || []).length, 2);
assert.equal((evaluation.match(/<strong>\$0.20<\/strong>/g) || []).length, 2);
assert(evaluation.includes('—'));
assert(!evaluation.includes('<strong>—</strong>'));
const {modelState} = require('./src/models.ts');
assert.equal(modelState({}, 'ft'), 'unavailable');
for (const [payload, expected] of [[{offline:true}, 'offline'], [{unavailable:true}, 'unavailable'], [{workers:{idle:1}}, 'ready'], [{workers:{running:1}}, 'ready'], [{workers:{initializing:1}}, 'waking up'], [{workers:{idle:0},jobs:{inQueue:1}}, 'waking up'], [{workers:{idle:0,running:0,initializing:0},jobs:{inQueue:0}}, 'asleep'], [{workers:{}}, 'unavailable']]) assert.equal(modelState({ft:payload}, 'ft'), expected);
const {Term} = require('./src/components/Term.tsx');
for (const [name, definition] of Object.entries(require('./src/glossary.ts').glossary)) {
  const term = render(React.createElement(Term, {name}));
  assert(term.includes(definition)); assert(term.includes('role="tooltip"')); assert(term.includes('aria-describedby'));
}
const {RouterLink} = require('./src/router.tsx');
assert(render(React.createElement(RouterLink, {href:'/compare', 'aria-label':'Compare models'}, 'Compare')).includes('href="/compare"'));
const {MemoContent} = require('./src/components/Memo.tsx');
const jump = () => {};
for (const heading of ['Summary', '## Summary', '**Summary**']) {
  const html = render(React.createElement(MemoContent, {
    text: heading + '\nText [p3:r17] .\n• First\n- Second\n* Third', jump
  }));
  assert(html.includes('<h3>Summary</h3>'));
  assert(html.includes('[p3:r17]</button>.'));
  assert.equal((html.match(/<li>/g) || []).length, 3);
}
// An incomplete stream chunk stays readable, then becomes a cited heading/list.
assert(render(React.createElement(MemoContent, {text: '## Summ', jump})).includes('## Summ'));
assert(render(React.createElement(MemoContent, {text: '**Credit Memo**', jump})).includes('<h3>Credit Memo</h3>'));
const checked = render(React.createElement(MemoContent, {
  text: '**Totals 54,987.85**\nSGD 54,987.85 and 89,218.66; **54,987.85** [p1:r1–r18]',
  unverified: ['54,987.85'], jump
}));
assert.equal((checked.match(/<mark class="unverified" title="Not in the computed analysis — check before use">54,987.85<\/mark>/g) || []).length, 3);
assert(checked.includes('and 89,218.66;'));
assert(!checked.includes('>89,218.66</mark>'));
const citationOnly = render(React.createElement(MemoContent, {text: '[p1:r1–r18]', unverified: ['1'], jump}));
assert(!citationOnly.includes('<mark'));
const clicked = [];
const memo = MemoContent({text: `**Credit Memo**
---
**Summary**
- **Average Balance**: SGD 72,289.51 (observed running balances) [p1:r1]
- **Top Payer**: Senja Hijau contributed SGD 17,898.73 [p1:r2; p2:r1; p2:r8]
- **Jan 2026**: Inflow SGD 46,285.38 [p1:r1–r18]
---
1. Please explain the SGD 499.00 discrepancy on page 4 [p4:r17].`, jump: value => clicked.push(value)});
const memoHtml = render(memo);
for (const label of ['Average Balance', 'Top Payer', 'Jan 2026']) assert(memoHtml.includes(`<strong>${label}</strong>`));
assert(!memoHtml.includes('**'));
assert(!memoHtml.includes('---'));
assert(memoHtml.includes('<ul>'));
assert(memoHtml.includes('<ol><li>Please explain'));
for (const ref of ['p1:r2', 'p2:r1', 'p2:r8', 'p1:r1–r18']) assert(memoHtml.includes(`[${ref}]</button>`));
const buttons = [];
function visit(node) {
  React.Children.forEach(node, child => {
    if (!React.isValidElement(child)) return;
    if (child.type === 'button') buttons.push(child);
    visit(child.props.children);
  });
}
visit(memo);
buttons.find(button => button.props.children.join('') === '[p2:r8]').props.onClick();
assert.deepStrictEqual(clicked.pop(), {page_id: 'p2', row_index: 7});
buttons.find(button => button.props.children.join('') === '[p1:r1–r18]').props.onClick();
assert.deepStrictEqual(clicked.pop(), {page_id: 'p1', row_index: 0});
const variants = render(React.createElement(MemoContent, {text: '***\n___\nText **bold** [p1:r1-r18, p2:r8] <img src=x>', jump}));
assert(!variants.includes('***') && !variants.includes('___'));
assert(variants.includes('<strong>bold</strong>'));
assert(variants.includes('[p1:r1-r18]</button>'));
assert(variants.includes('[p2:r8]</button>'));
assert(variants.includes('&lt;img src=x&gt;'));
// Live model output also used round brackets, bare refs and page-qualified ranges.
const round = render(React.createElement(MemoContent, {text: 'Min SGD 44,587.78 (p1:r2). Net (p1:r1–p1:r18) and (p1:r3, p2:r2). See p4:r17 too.', jump}));
for (const ref of ['p1:r2', 'p1:r1–p1:r18', 'p1:r3', 'p2:r2', 'p4:r17']) assert(round.includes(`[${ref}]</button>`), ref);
assert(!round.includes('(p1:r2)'));
const {Cell} = require('./src/components/Review.tsx');
const cell = render(React.createElement(Cell, {
  value: '100', display: '100.00', edited: false, directionTitle: 'Moved to credit',
  numeric: true, label: 'Edit row 10 credit', save: async () => {}
}));
assert(cell.includes('cell-content'));
assert(cell.indexOf('↔') < cell.indexOf('100.00'));
const {Review} = require('./src/components/Review.tsx');
const reviewed = render(React.createElement(Review, {
  pages: [{id:'p1', thumbnail_url:'/photo', status:'done', document:{doc_type:'statement',currency:'SGD',opening_balance:'0',transactions:[{date:'2026-01-01',description:'Payment',credit:'100',debit:null,balance:'100'}]}, edited:[], direction_fixes:[{row:0,from:'debit',to:'credit',amount:'100'}],metrics:null}], selected:'p1', row:0, choose:()=>{}, save:async()=>{},
  analysis:{reconciliation:[],findings:[{citations:[{page_id:'p1',row_index:0}]}]}
}));
for (const label of ['What was read from p1','Balance adds up','Red flag','Moved from debit to credit: the balance went up by this amount','Statement evidence p1']) assert(reviewed.includes(label),label);
const {Documents} = require('./src/components/Documents.tsx');
const page = {id: 'p1', status: 'extracted', metrics: {source: 'cached-demo', live_error: 'Oops'}};
const props = {pages: [page], types: {}, session: ''};
const warning = render(React.createElement(Documents, props));
assert(warning.includes('Live call failed — showing cached result'));
assert(warning.includes('Oops'));
assert(!warning.includes('cached demo'));
delete page.metrics.live_error;
assert(render(React.createElement(Documents, props)).includes('cached demo'));
const {UnderTheHood} = require('./src/components/UnderTheHood.tsx');
assert(render(React.createElement(UnderTheHood, {
  history: [{source: 'cached-demo', task: 'extract', live_error: 'Oops'}], status: {}
})).includes('Oops'));
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=FRONTEND, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_cycle_two_rendering_and_calculations():
    if not shutil.which("node") or not (FRONTEND / "node_modules/typescript").exists():
        pytest.skip("Frontend node_modules and Node are required")
    script = r"""
const fs = require('fs'), assert = require('assert'), ts = require('typescript');
require.extensions['.tsx'] = require.extensions['.ts'] = (module, filename) => {
  module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, esModuleInterop: true, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX }
  }).outputText, filename);
};
const React = require('react'), {renderToStaticMarkup: render} = require('react-dom/server');
const {LineTable, verdict, Compare} = require('./src/components/Compare.tsx');
const example = JSON.parse(fs.readFileSync('../../eval/compare_examples.json','utf8')).examples[0];
const html = render(React.createElement(LineTable,{example}));
assert.equal((html.match(/money in read as money out/g)||[]).length,14);
assert.equal((html.match(/✓ right/g)||[]).length,40);
assert(html.includes('DATE + MONEY OUT + MONEY IN'));
assert.equal(verdict({date:'2026-01-01',credit:'100',debit:null},[{date:'2026-01-01',debit:'100',credit:null}],false),'wrong · money in read as money out');
assert.equal(verdict({date:'x',description:'a',credit:'100'},[{date:'x',description:'a',credit:'101'}],false),'wrong · wrong amount');
assert.equal(verdict({date:'x'},[],false),'wrong · missing line');
assert.equal(verdict({},[],null),'Not scored');
assert.equal(verdict({description:'a',quantity:'2',unit_price:'5',amount:'10'},[{description:'a',quantity:'2',unit_price:'6',amount:'12'}],false,true),'wrong · wrong amount');
const offline = render(React.createElement(Compare,{session:'',pages:[],status:{}}));
assert(offline.includes('Recorded test examples'));
assert(offline.includes('Always available offline'));
const {monthlyCost,sessionCosts,Cost} = require('./src/components/Cost.tsx');
assert.equal(monthlyCost(50000,0.26),13);
assert.equal(monthlyCost(0,0.26),0);
assert.equal(monthlyCost(-5,0.26),0);
assert.equal(monthlyCost(Infinity,0.26),0);
assert.equal(sessionCosts([{model:'ft',task:'extract',source:'cached-demo',cost:5}], 'ft'),null);
assert.deepEqual(sessionCosts([{model:'ft',task:'extract',source:'runpod',cost:0.01,gpu:'GPU A'},{model:'ft',task:'extract',source:'runpod',cost:0.03,gpu:'GPU A'},{model:'ft',task:'memo',source:'runpod',cost:20}], 'ft'),{perPage:0.02,gpu:'GPU A'});
const costHtml = render(React.createElement(Cost,{history:[{model:'ft',task:'extract',source:'runpod',cost:0.02,gpu:'GPU A'}]}));
for (const label of ['this session: $0.02000 per page measured on GPU A','not measured yet','Idle cost $0','Copy this estimate','Romania, EU-RO-1']) assert(costHtml.includes(label),label);
const {ResultsHeadline} = require('./src/components/Results.tsx');
const summary = JSON.parse(fs.readFileSync('../../eval/summary.json','utf8'));
const headline = render(React.createElement(ResultsHeadline,{summary}));
for (const label of ['1,499 pages tested','Lines read right (row F1)','96.5%','57.8%','61.4%']) assert(headline.includes(label),label);
const {Cashflow, monthlyPoints} = require('./src/components/Analysis.tsx');
assert.deepEqual(monthlyPoints({'2026-01':{inflow:'60000.50',outflow:'30000.25',net:'30000.25',partial:false,days_observed:31}}),[{month:'2026-01',partial:false,daysObserved:31,inflow:60000.5,outflow:30000.25,net:30000.25}]);
assert(render(React.createElement(Cashflow,{data:null,jump:()=>{}})).includes('Extract statements'));
assert(render(React.createElement(Cashflow,{data:{monthly:{}},jump:()=>{}})).includes('Extract statements'));
const {PageToolbar} = require('./src/components/PageToolbar.tsx');
assert.equal(render(React.createElement(PageToolbar,{pages:[]})), '');
const toolbar = render(React.createElement(PageToolbar,{pages:[{id:'p1',metrics:{source:'cached-demo'},document:{transactions:[{date:'x'}]}}],selected:'p1',choose:()=>{},read:()=>{},label:()=> 'read',busy:false}));
for (const label of ['Read this page','Download CSV','cached demo']) assert(toolbar.includes(label));
assert(!toolbar.includes('<select'));
const {Memo} = require('./src/components/Memo.tsx');
const memo = render(React.createElement(Memo,{session:'s',model:'ft',jump:()=>{}}));
assert(memo.includes('Generate a credit memo from the checked rows'));
assert(memo.indexOf('class="memo"') < memo.indexOf('Generate memo'));
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=FRONTEND, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
