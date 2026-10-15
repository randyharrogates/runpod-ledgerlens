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
for (const [payload, expected] of [[{offline:true}, 'offline'], [{unavailable:true}, 'unavailable'], [{workers:{idle:1}}, 'on standby'], [{workers:{running:1}}, 'ready'], [{workers:{initializing:1}}, 'waking up'], [{workers:{idle:0},jobs:{inQueue:1}}, 'waking up'], [{workers:{idle:0,running:0,initializing:0},jobs:{inQueue:0}}, 'asleep'], [{workers:{}}, 'unavailable']]) assert.equal(modelState({ft:payload}, 'ft'), expected);
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
  assert(html.includes('[p3:r17]'));
  assert(html.includes('title="p3 · line 17"'));
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
for(const field of ['date','description','debit','credit','balance']) assert(reviewed.includes('data-field="'+field+'"'),field);
const invoiceHtml = render(React.createElement(Review, {
  pages: [{id:'invoice',thumbnail_url:'/photo',status:'done',document:{doc_type:'invoice',currency:'SGD',opening_balance:'0',line_items:[{description:'Fictional supplies',quantity:'2',unit_price:'5',amount:'10'}]},edited:[],direction_fixes:[],metrics:null}],
  selected:'invoice',row:-1,choose:()=>{},save:async()=>{},analysis:null
}));
assert(invoiceHtml.includes('invoice-rows'));
for(const field of ['description','quantity','unit_price','amount']) {
  assert(invoiceHtml.includes('data-field="'+field+'"'),field);
  assert(invoiceHtml.includes('Edit row 1 '+field),field);
}
const {Documents} = require('./src/components/Documents.tsx');
const page = {id: 'p1', status: 'extracted', metrics: {source: 'cached-demo', live_error: 'Oops'}};
const props = {pages: [page], types: {}, session: ''};
const warning = render(React.createElement(Documents, props));
assert(warning.includes('Live call failed — showing cached result'));
assert(warning.includes('Oops'));
assert(!warning.includes('Recorded answer (demo)'));
delete page.metrics.live_error;
assert(render(React.createElement(Documents, props)).includes('Recorded answer (demo)'));
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
const {logTicks,runLabel} = require('./src/components/Training.tsx');
const {visibleHardPages,HARD_SET_LABELS} = require('./src/components/Results.tsx');
const {phoneFinding} = require('./src/components/Results.tsx');
const phoneSet = (ft,big,small) => ({'4b-ft':{row_f1:ft,reconciliation:0,pages:18},'8b':{row_f1:big,reconciliation:0},'4b':{row_f1:small,reconciliation:0}});
const phoneText = phoneFinding({splits:{test_phone:phoneSet(0.672,0.411,0.258),test_phone_clean:phoneSet(0.984,0.671,0.723)},split_labels:{}});
assert(phoneText.includes('67.2% against 41.1% and 25.8%'));
assert(phoneText.includes('drops from 98.4%'));
assert(phoneText.includes('no model gets a photographed page to add up'));
assert.equal(phoneFinding({splits:{},split_labels:{}}),null);
const hardPages = Array.from({length:20},(_,i)=>({id:`p${i}`,row_f1:{}}));
const hardBreakdown = {splits:{test_unseen_layout:{models:{},hardest_pages:hardPages}}};
assert.equal(visibleHardPages(hardBreakdown,'test_unseen_layout',false).length,8);
assert.equal(visibleHardPages(hardBreakdown,'test_unseen_layout',true).length,20);
assert.equal(visibleHardPages(hardBreakdown,'test_cord',false).length,0);
assert.equal(HARD_SET_LABELS.test_cord,'Real receipts');
assert.deepEqual(logTicks([0.003,0.06]),[0.001,0.01,0.1]);
assert.deepEqual(logTicks([0.01,1]),[0.01,0.1,1]);
assert.deepEqual(logTicks([0,-1,NaN,Infinity]),[0.01,0.1,1]);
assert.deepEqual(logTicks([0.1]),[0.1,1]);
const {statusSummary,models} = require('./src/models.ts');
const uniform = endpoint => Object.fromEntries(models.map(m=>[m.key,endpoint]));
const ready = {workers:{idle:1}};
const asleep = {workers:{idle:0,running:0,initializing:0},jobs:{inQueue:0}};
const waking = {workers:{initializing:1}};
assert.equal(statusSummary({}),'Checking live models…');
assert.equal(statusSummary(uniform({offline:true})),'Live models off · recorded results still work');
assert.equal(statusSummary({'ft':{offline:true},'8b':{unavailable:true}}),'Live models off · recorded results still work');
assert.equal(statusSummary(uniform(asleep)),'Live models asleep · first read wakes them (can take minutes)');
assert.equal(statusSummary(uniform(ready)),'Live models on standby · the first read can take a few minutes');
assert.equal(statusSummary(uniform({workers:{running:1}})),'All 3 models ready');
assert.equal(statusSummary(uniform(waking)),'Live models waking up');
assert.equal(statusSummary({...uniform(ready),ft:asleep}),null);
const {defaultCompareMode} = require('./src/components/Compare.tsx');
for(const endpoint of [ready,asleep,waking]) assert.equal(defaultCompareMode(null,[{id:'p'}],uniform(endpoint)),'live');
assert.equal(defaultCompareMode(null,[{id:'p'}],uniform({offline:true})),'recorded');
assert.equal(defaultCompareMode(null,[],uniform(ready)),'recorded');
assert.equal(defaultCompareMode('p',[{id:'p'}],uniform(ready)),'recorded');
assert.equal(defaultCompareMode('',[{id:'p'}],uniform(ready)),'recorded');
assert.equal(defaultCompareMode(null,[{id:'p'}],{}),'recorded');
const offlineWithPages = render(React.createElement(Compare,{session:'',pages:[{id:'p'}],status:uniform({offline:true})}));
assert(offlineWithPages.includes('aria-current="page">Recorded test examples'));
assert(!offlineWithPages.includes('Model state right now'));
const history = [{id:'later',created:'2026-10-11T02:00:00Z',simulated:true,status:'done'},{id:'reported',created:'2026-10-10'},{id:'earlier',created:'2026-10-11T01:12:00+08:00',simulated:true,status:'done'}];
assert.equal(runLabel(history[1],history),'Run 1 · reported');
assert.equal(runLabel(history[2],history),'Run 2 · simulated, no GPU used · 11 Oct, 01:12 · done');
assert(runLabel(history[0],history).startsWith('Run 3 ·'));
assert(!runLabel(history[0],history).includes('later'));
const {damageLabel,periodLabel} = require('./src/components/format.ts');
assert.deepEqual(['clean','light','medium','heavy'].map(damageLabel),['Clean','Light','Medium','Heavy']);
assert.equal(periodLabel('2026-01-01','2026-06-30'),'1 Jan to 30 Jun 2026');
assert.equal(periodLabel('2025-12-01','2026-06-30'),'1 Dec 2025 to 30 Jun 2026');
const {liveCostText,monthlyDollars} = require('./src/components/Cost.tsx');
const {liveRateText} = require('./src/components/Cost.tsx');
assert.equal(liveRateText({splits:{test_phone_clean:{'4b-ft':{cost_per_1000_pages:2.4751,gpu_name:'NVIDIA GeForce RTX 5090'}}}},'ft',50000),'$2.48 per 1,000 pages live on RTX 5090 (test pages, one at a time); $124 per month');
assert.equal(liveRateText(null,'ft',50000),null);
assert.equal(liveCostText(50000),'$6.00 to $6.60 per 1,000 pages warm on RTX 5090; $300 to $330 per month');
assert.equal(monthlyDollars(99),'$99.00');
assert.equal(monthlyDollars(100.5),'$100.50');
assert.equal(monthlyCost(50000,0.2554),13);
assert.equal(monthlyCost(50000,0.519),26);
assert.equal(monthlyCost(50000,0.3354),17);

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
for (const label of ['Read this page','Download CSV','Recorded answer (demo)']) assert(toolbar.includes(label));
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


def test_cycle_three_data_training_and_formatting():
    if not shutil.which("node") or not (FRONTEND / "node_modules/typescript").exists():
        pytest.skip("Frontend node_modules and Node are required")
    script = r"""
const fs = require('fs'), assert = require('assert'), ts = require('typescript');
require.extensions['.tsx'] = require.extensions['.ts'] = (module, filename) => {
  module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: {target:ts.ScriptTarget.ES2022, esModuleInterop:true, module:ts.ModuleKind.CommonJS, jsx:ts.JsxEmit.ReactJSX}
  }).outputText, filename);
};
const React = require('react'), {renderToStaticMarkup:render} = require('react-dom/server');
const {DatasetTiles,ValidationReport,AnswerKey,amountBins} = require('./src/components/Data.tsx');
const fixture = JSON.parse(fs.readFileSync('../../eval/dataset_summary.json', 'utf8'));
const data = render(React.createElement(DatasetTiles,{summary:fixture}));
for(const text of ['4,475','400','3,675','800','No business appears in more than one group','Statement E (held back)','Statement F (held back)','57%','One customer dominates','Bounced cheque','Indonesian rupiah','1 Jan to 30 Jun 2026']) assert(data.includes(text),text);
for(const text of ['Clean','Light','Medium','Heavy']) assert(data.includes(text),text);
const bins = amountBins({...fixture,transaction_amount_distribution:{money_in:{'2':'8413'},money_out:{'2':'6732'}}});
assert.deepEqual(bins,[{range:'100s',moneyIn:8413,moneyOut:6732}]);
const blocked = render(React.createElement(ValidationReport,{report:{valid:false,pages:3,businesses:2,entries:9,problems:[{file:'p1.meta.json',problem:'Missing business'}],leakage:{businesses:{count:1,examples:['biz']},counterparties:[]}}}));
for(const text of ['Blocked','p1.meta.json','Missing business','1 businesses appear in more than one group','CORD v2']) assert(blocked.includes(text),text);
const warning = render(React.createElement(ValidationReport,{report:{valid:true,pages:3,problems:[],leakage:{businesses:{count:0,examples:[]},counterparties:[{count:2,examples:['Fictional shop'],splits:['train','test']}]}}}));
for(const text of ['OK','No business appears in more than one group','Warning: 2','does not block training','Learn from','Final test']) assert(warning.includes(text),text);
const key = render(React.createElement(AnswerKey,{document:{currency:'IDR',transactions:[{date:'2026-04-06',description:'ATM WITHDRAWAL',debit:'114800',credit:null,balance:'500000'}]}}));
for(const text of ['Money out','Money in','6 Apr 2026','114,800']) assert(key.includes(text),text);
const {TrainingPlanView,PodAlert,ReportedTraining,RunView,runLabel} = require('./src/components/Training.tsx');
const plan = {simulated:true,confirm_token:'do-not-render',gpu:'Test GPU',price_per_hour:0.72,stock:'Stock unavailable',stock_verified:false,estimate:{total_minutes:180,total_cost:2.7},cap_usd:5,dataset:'default',steps:['Train','Delete Pod'],warning:'Volume must be free'};
const simulated = render(React.createElement(TrainingPlanView,{plan}));
for(const text of ['SIMULATED','simulated, no GPU used','Stock unavailable','Volume must be free','$2.7','$5','CORD v2']) assert(simulated.includes(text),text);
assert(!simulated.includes('do-not-render'));
const real = render(React.createElement(TrainingPlanView,{plan:{...plan,simulated:false}}));
assert(real.includes('REAL'));assert(!real.includes('SIMULATED'));
const alert = render(React.createElement(PodAlert,{runs:[{id:'abc',pod_id:'pod123',status:'POD MAY STILL BE RUNNING: failed deletion'}]}));
for(const text of ['role="alert"','pod-alert','pod123','delete it in the Runpod console']) assert(alert.includes(text),text);
assert.equal(render(React.createElement(PodAlert,{runs:[{status:'done'}]})), '');
const training = JSON.parse(fs.readFileSync('../../eval/training_summary.json','utf8'));
const reported = render(React.createElement(ReportedTraining,{summary:training}));
for(const text of ['Qwen3-VL-4B','396','rank 16','86 min','13.1 GB','$1.03 = 86 min × $0.72/h','Whole-run stages','105 min']) assert(reported.includes(text),text);
const quick = {id:'abc',created:'2026-10-11',preset:'quick',dataset:'default',simulated:true,status:'done',stage:'predict-ft',elapsed:60,spend:1,cap_usd:5,result_summary:{quick_check:true}};
const quickHtml = render(React.createElement(RunView,{run:quick}));
assert(quickHtml.includes('Quick check passed, no scores'));assert(quickHtml.includes('simulated, no GPU used'));assert(quickHtml.includes('Simulated Pod deleted'));
assert(runLabel(quick).includes('simulated, no GPU used'));
const f = require('./src/components/format.ts');
assert.equal(f.number('50000'),'50,000');assert.equal(f.dollars(300),'$300.00');assert.equal(f.money('114800','IDR'),'114,800');assert.equal(f.money('16.2'),'16.20');assert.equal(f.money('354.4','SGD',true),'SGD 354.40');assert.equal(f.printedDate('2026-04-06'),'6 Apr 2026');assert.equal(f.monthName('2026-01'),'Jan 2026');
assert.equal(f.metadata({layout_id:'stmt_e',strength:'medium'},'IDR'),'Statement E (held back) · medium photo damage · Indonesian rupiah');
assert.equal(f.groupName('test_unseen_layout'),'final test, unseen layout');
const {models} = require('./src/models.ts');
assert.deepEqual(models.map(m=>m.name),['Trained small model','Big model','Small model']);
const {ResultTable} = require('./src/components/Results.tsx');
const scores = render(React.createElement(ResultTable,{value:JSON.parse(fs.readFileSync('../../eval/summary.json','utf8'))}));
assert(scores.indexOf('<th scope="col">Trained small model') < scores.indexOf('<th scope="col">Big model'));
assert(scores.indexOf('<th scope="col">Big model') < scores.indexOf('<th scope="col">Small model'));
assert(scores.includes('Answer in the right format (valid JSON)'));
const {LineTable} = require('./src/components/Compare.tsx');
const lines = render(React.createElement(LineTable,{example:{id:'p',doc_type:'statement',gold_rows:[{date:'2026-04-06',description:'ATM WITHDRAWAL',debit:'114800',credit:'0'}],models:{}}}));
assert(lines.includes('6 Apr · ATM WITHDRAWAL · out 114,800'));assert(!lines.includes('money in 0'));
const {Cost} = require('./src/components/Cost.tsx');
const cost = render(React.createElement(Cost,{history:[]}));
assert(cost.includes('value="50,000"'));assert.equal((cost.match(/warm on RTX 5090/g)||[]).length,1);assert(cost.includes('$300 to $330'));
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=FRONTEND, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_final_cycle_presenter_exports_and_reported_run():
    if not shutil.which("node") or not (FRONTEND / "node_modules/typescript").exists():
        pytest.skip("Frontend node_modules and Node are required")
    script = r"""
const fs=require('fs'), assert=require('assert'), ts=require('typescript');
require.extensions['.tsx']=require.extensions['.ts']=(module,filename)=>module._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,esModuleInterop:true,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,filename);
const React=require('react'), {renderToStaticMarkup:render}=require('react-dom/server');
const {tour}=require('./src/tour.ts');
assert.equal(tour.length,8);assert(tour.every(s=>s.route.startsWith('/') && s.words && s.title && s.end>s.start));assert(tour[7].end<=210);assert.equal(tour.reduce((sum,s)=>sum+s.end-s.start,0),210);
const source=fs.readFileSync('../../docs/ui-mockups/tour-script.md','utf8');
const words=[...source.matchAll(/\n"(.*?)"/g)].map(m=>m[1]);assert.deepEqual(tour.slice(1).map(s=>s.words),words);
assert.equal(tour[4].query,'?example=biz_0007_p006');assert.deepEqual(tour.map(s=>s.route),['/assistant','/','/','/','/compare','/results','/cost','/training']);assert.deepEqual(tour.slice(1,4).map(s=>s.readStep),[2,2,3]);assert.deepEqual(tour[3].readSteps,[3,4]);
const {clockText}=require('./src/components/Presenter.tsx');
const {Presenter}=require('./src/components/Presenter.tsx');
const panel=render(React.createElement(Presenter,{index:0,go:()=>{},exit:()=>{},readMemo:()=>{}}));
for(const text of ['Step 1 of 8','Back','Next','Exit','Collapse','3:30','0:00–0:30','Presenter tour'])assert(panel.includes(text),text);
assert(panel.includes(tour[0].words.replaceAll("'",'&#x27;')));
assert.equal(clockText(180),'3:00');assert.equal(clockText(9),'0:09');
assert(render(React.createElement(Presenter,{index:3,go:()=>{},exit:()=>{},readMemo:()=>{}})).includes('Open credit memo (Read step 4)'));
const f=require('./src/components/format.ts');assert.equal(f.dollars(2.7),'$2.70');assert.equal(f.number(50000),'50,000');assert.equal(f.printedDate('2026-04-06'),'6 Apr 2026');
const {RunView,StageTimeline,stageWidth,TrainingPlanView}=require('./src/components/Training.tsx');
const summary=JSON.parse(fs.readFileSync('../../eval/summary.json','utf8'));
const reported=render(React.createElement(RunView,{run:{id:'reported',created:'2026-10-10',dataset:'default',status:'done',stage:'report',result_summary:{evaluation:summary}}}));
for(const text of ['10 Oct 2026','Same practice pages','RTX PRO 4500','$0.72/h','5 h 46 min','$1.03','96.5%','57.8%','href="/results"'])assert(reported.includes(text),text);
for(const text of ['Stage:','Spend so far','Elapsed:','No log lines','Pod deletion','progress','Answer in the right format'])assert(!reported.includes(text),text);
assert.equal(stageWidth(86,346),86/346*100);assert.equal(stageWidth(0,0),0);assert.equal(stageWidth(-1,10),0);assert.equal(stageWidth(20,10),100);
const timeline=render(React.createElement(StageTimeline,{}));assert(timeline.includes('background:var(--sky)'));assert(!timeline.includes('var(--accent)'));assert(timeline.includes('width:'+String(stageWidth(86,346))+'%'));
const plan=render(React.createElement(TrainingPlanView,{plan:{simulated:true,gpu:null,price_per_hour:0.72,estimate:{total_minutes:180,total_cost:2.7},cap_usd:5,dataset:'default',steps:[],stock:'Neither supported GPU is available in EU-RO-1'}}));assert(plan.includes('No GPU of the right type is free in Romania right now. A simulation can still run; a real run would wait or fail.'));assert(plan.includes('$2.70'));assert(!plan.includes('Neither supported'));
const {PageToolbar}=require('./src/components/PageToolbar.tsx');assert(render(React.createElement(PageToolbar,{pages:[{id:'p1',document:{transactions:[]}}],label:()=>'',choose:()=>{},read:()=>{}})).includes('Download all pages (CSV)'));
const {csvText}=require('./src/components/export.ts');assert.equal(csvText([['a,b','"quoted"','a\nb','=SUM(A1)']]),'"a,b","\"\"quoted\"\"","a\nb","\'=SUM(A1)"');
const {scoresCSV}=require('./src/components/Results.tsx');const csv=scoresCSV(summary);assert(csv.some(row=>row.includes('Lines read right (row F1)')));assert.equal(csv[0][2],'Trained small model');assert(csvText(csv).includes('0.965'));
const {DatasetPreview}=require('./src/components/Data.tsx');const preview=render(React.createElement(DatasetPreview,{id:'page',query:'',kind:'html'}));assert(preview.includes('sandbox=""'));assert(preview.includes('width:1240px;height:1754px'));assert(render(React.createElement(DatasetPreview,{id:'page',query:'',kind:'image'})).includes('/image?'));assert(render(React.createElement(DatasetPreview,{id:'page',query:'',kind:null})).includes('No preview available'));
const css=fs.readFileSync('./src/style.css','utf8');assert(!css.includes('text-transform:capitalize'));assert(css.includes('@media print'));assert(css.includes('.memo .raw-citation {display:inline}'));
// Clipboard export sends the chosen estimate through the real click handler.
let states=[50000,'ft',summary,'',''];let idx=0;const original=React.useState;React.useState=()=>[states[idx++],()=>{}];
let copied='';Object.defineProperty(global,'navigator',{value:{clipboard:{writeText:async value=>{copied=value;}}},configurable:true});
delete require.cache[require.resolve('./src/components/Cost.tsx')];const {Cost}=require('./src/components/Cost.tsx');
function findButton(node){if(!node||typeof node!=='object')return null;if(node.type==='button'&&node.props.children==='Copy this estimate')return node;for(const child of [node.props?.children].flat(Infinity)){const result=findButton(child);if(result)return result;}return null;}
// Call the component with effects disabled to inspect its real click handler.
const effect=React.useEffect;React.useEffect=()=>{};const tree=Cost({history:[]});React.useEffect=effect;React.useState=original;
const estimateHtml=render(tree);
for(const text of ['$13.00','$26.00','$17.00','$0.26 per 1,000 pages','$0.52 per 1,000 pages','$0.34 per 1,000 pages']) assert(estimateHtml.includes(text),text);
findButton(tree).props.onClick();setImmediate(()=>{assert(copied.includes('50,000 pages/month'));assert(copied.includes('Trained small model'));assert(copied.includes('RTX PRO 4500'));assert(copied.includes('$0.26 per 1,000 pages; $13.00 per month'));assert(copied.includes('Big model: $5.26 per 1,000 pages live on RTX 5090 (test pages, one at a time); $263 per month'));assert(copied.includes('Trained small model: $2.48 per 1,000 pages live'));});
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=FRONTEND, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_assistant_turns_and_split_post_stream():
    if not shutil.which("node") or not (FRONTEND / "node_modules/typescript").exists():
        pytest.skip("Frontend node_modules and Node are required")
    script = r"""
const fs=require('fs'), assert=require('assert'), ts=require('typescript');
require.extensions['.tsx']=require.extensions['.ts']=(module,filename)=>module._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,esModuleInterop:true,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,filename);
const React=require('react'), {renderToStaticMarkup:render}=require('react-dom/server');
const {reduceTurn,TurnView,sections}=require('./src/components/Assistant.tsx');
const previous=Object.fromEntries(sections.map(([id])=>[id,'Previous '+id]));
const events=[
 {type:'route',intent:'edit_memo',reason:'Shorten the requested section.'},
 {type:'step',name:'reader',page_id:'p1',status:'start'},
 {type:'step',name:'reader',page_id:'p1',status:'done',ms:1200},
 {type:'tool',name:'find_rows',arguments:'{"month":"2026-03"}',result:{rows:[{},{}],count:2}},
 {type:'deltas',parts:[{part:'red_flags',text:'Unchecked draft'}]},
 {type:'section_reset',section:'red_flags'},
];
const reset=reduceTurn(events,previous);
assert.equal(reset.cards.red_flags.text,'');
assert.equal(reset.cards.red_flags.resetting,true);
assert(render(React.createElement(TurnView,{turn:reset,jump:()=>{}})).includes('rewriting after a check'));
events.push({type:'deltas',parts:[{part:'red_flags',text:'Checked [p1:r4]'}]},
 {type:'section',section:'red_flags',text:'Final [p1:r4]',problems:['Check this figure'],rewritten:true},
 {type:'final',intent:'edit_memo',memo:{...previous,red_flags:'Final [p1:r4]'}},
 {type:'metrics',total_s:4,delayTime:2000,executionTime:1500,usage:{prompt_tokens:123,completion_tokens:45},gpu:'Test GPU',cost:0.001,source:'recorded'});
const turn=reduceTurn(events,previous);
assert.equal(turn.steps.filter(s=>s.type==='step').length,1);
assert.equal(turn.cards.red_flags.text,'Final [p1:r4]');
assert.equal(turn.cards.red_flags.resetting,false);
const html=render(React.createElement(TurnView,{turn,jump:()=>{}}));
let index=-1;for(const [,heading] of sections){const next=html.indexOf('<h3>'+heading+'</h3>');assert(next>index,heading);index=next;}
for(const text of ['rewritten once','unchanged','Check this figure','Memo edit','reader · p1','1.2 s','find_rows(month: 2026-03) · 2 results','4.0 s total','2.0 s queue','1.5 s execution','123 tokens in / 45 out','Test GPU','$0.00100','Recorded run','[p1:r4]</button>'])assert(html.includes(text),text);
assert(!html.includes('Unchecked draft'));
const answer=reduceTurn([{type:'deltas',parts:[{part:'answer',text:'Draft'}]},{type:'section_reset',section:'answer'},{type:'final',intent:'knowledge',answer:'Checked [p2:r1]'}]);
assert.equal(answer.answer,'Checked [p2:r1]');
assert.equal(reduceTurn([{type:'error',message:'Traceback (most recent call last):\n File worker.py'}]).error,'The request failed');
assert(render(React.createElement(TurnView,{turn:answer,jump:()=>{}})).includes('[p2:r1]</button>'));
assert(render(React.createElement(TurnView,{turn:reduceTurn([{type:'error',message:'Connection lost'}]),jump:()=>{}})).includes('The agent stopped: Connection lost. Your pages are saved; try again.'));
const checked=reduceTurn([{type:'route',intent:'memo'},{type:'section',section:'concentration',text:'## Concentration\nOne payer.',rewritten:true,problems:[{kind:'unverified_figure',detail:'31,363.11'},'plain note']},{type:'final',intent:'memo',memo:{concentration:'## Concentration\nOne payer.'},problems:[{kind:'too_long',detail:'x'}]}]);
assert.deepEqual(checked.cards.concentration.problems,['Figure not found in the analysis: 31,363.11','plain note']);
assert(render(React.createElement(TurnView,{turn:checked,jump:()=>{}})).includes('Figure not found in the analysis: 31,363.11'));
(async()=>{
 const {postEvents}=require('./src/api.ts');
 const frames='event: deltas\r\ndata: {"parts":[{"part":"answer","text":"é"}]}\r\n\r\nevent: final\ndata: {"answer":"done"}\n\n';
 const bytes=new TextEncoder().encode(frames);let position=0;
 global.fetch=async()=>new Response(new ReadableStream({pull(controller){if(position===bytes.length)controller.close();else controller.enqueue(bytes.slice(position,++position));}}));
 const received=[];await postEvents('/test',{},e=>received.push(e));
 assert.equal(received.length,2);assert.equal(received[0].type,'deltas');assert.equal(received[0].parts[0].text,'é');assert.equal(received[1].answer,'done');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    subprocess.run(["node", "-e", script], cwd=FRONTEND, check=True)


def test_unified_logs_and_turn_links():
    if not shutil.which("node") or not (FRONTEND / "node_modules/typescript").exists():
        pytest.skip("Frontend node_modules and Node are required")
    script = r"""
const fs=require('fs'), assert=require('assert'), ts=require('typescript');
require.extensions['.tsx']=require.extensions['.ts']=(module,filename)=>module._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,esModuleInterop:true,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,filename);
const React=require('react'), {renderToStaticMarkup:render}=require('react-dom/server');
const {filterLogs,BrowserLogQueue}=require('./src/logs.ts');
const entries=[
 {id:1,ts:1,source:'app',level:'info',message:'Started',session:'s1',turn:'t1'},
 {id:2,ts:2,source:'browser',level:'warning',message:'<script>alert(1)</script>',session:'s1'},
 {id:3,ts:3,source:'worker',level:'error',message:'Failed',job_id:'job-1',session:'s1',turn:'t1',page_id:'p1',task:'reader'},
 {id:4,ts:4,source:'vllm',level:'debug',message:'Ready',session:'s2'},
 {id:5,ts:5,source:'app',level:'warning',message:'Retry',session:'s2',turn:'t2'},
 {id:6,ts:6,source:'worker',level:'info',message:'Done',session:'s1',turn:'t1'},
];
const all={source:'',errorsOnly:false,text:'',session:'',turn:''};
const ids=filter=>filterLogs(entries,{...all,...filter}).map(e=>e.id);
assert.deepEqual(ids({source:'worker'}),[3,6]);assert.deepEqual(ids({errorsOnly:true}),[2,3,5]);
assert.deepEqual(ids({text:'STARTED'}),[1]);assert.deepEqual(ids({text:'JOB-1'}),[3]);
assert.deepEqual(ids({text:'READER'}),[3]);assert.deepEqual(ids({text:'P1'}),[3]);
assert.deepEqual(ids({session:'s1'}),[1,2,3,6]);assert.deepEqual(ids({turn:'t1'}),[1,3,6]);
assert.deepEqual(ids({source:'worker',errorsOnly:true,session:'s1',turn:'t1',text:'JOB'}),[3]);
const {LogRows,LogsDrawer}=require('./src/components/Logs.tsx');
const rows=render(React.createElement(LogRows,{entries,filter:all}));
for(const text of ['log-source log-app','log-source log-browser','log-level log-error','data-level="warning"','job job-1','&lt;script&gt;'])assert(rows.includes(text),text);
assert(!rows.includes('<script>'));assert(render(React.createElement(LogRows,{entries,filter:{...all,text:'missing'}})).includes('No log lines match these filters.'));
const drawer=render(React.createElement(LogsDrawer,{open:true,onClose:()=>{},session:'s1',initial:{turn:'t1',jobId:'job-1'}}));
for(const text of ['Errors only','This session only','placeholder="Search logs"','This turn only','job-1','aria-modal="false"'])assert(drawer.includes(text),text);
assert.equal(render(React.createElement(LogsDrawer,{open:false,onClose:()=>{},session:'s1'})),'');
const queue=new BrowserLogQueue();for(let i=0;i<105;i++)queue.push({level:'info',message:'x'.repeat(2100),url:'u'.repeat(400)});
assert.equal(queue.dropped,5);let batch=queue.take();assert(batch.length<=20);assert.equal(batch.filter(e=>e.message==='5 browser log lines dropped').length,1);assert.equal(queue.dropped,0);
assert(batch.some(e=>e.message.length===2000 && e.url.length===300));
let rest=[];while((batch=queue.take())!==null){assert(batch.length<=20);rest.push(...batch);}assert(!rest.some(e=>e.message.includes('dropped')));
const {reduceTurn,TurnView}=require('./src/components/Assistant.tsx');
const turn=reduceTurn([{type:'turn',turn_id:'t1'},{type:'job',job_id:'job-1'}]);assert.equal(turn.turnId,'t1');assert.equal(turn.jobId,'job-1');
assert(render(React.createElement(TurnView,{turn,jump:()=>{}})).includes('Logs for this turn'));
const measured=reduceTurn([{type:'metrics',turn_id:'t1',job_id:'job-1',total_s:1}]);assert.equal(measured.turnId,'t1');assert.equal(measured.jobId,'job-1');
const html=render(React.createElement(TurnView,{turn:measured,jump:()=>{}}));assert(html.includes('Logs for this turn'));assert(html.includes('job job-1'));

(async()=>{
 const logs=require('./src/logs.ts');const calls=[];let pending;
 const oldTimeout=global.setTimeout,oldClear=global.clearTimeout;
 global.setTimeout=fn=>{pending=fn;return 1;};global.clearTimeout=()=>{pending=undefined;};
 const target=new EventTarget();target.location={href:'https://example.test/',origin:'https://example.test'};
 const failure=new Error('Connection lost'),abort=new Error('Stopped');abort.name='AbortError';
 target.fetch=async(input,options)=>{
  const url=input instanceof Request ? input.url : String(input);calls.push({url,options});
  if(url.includes('/fail'))throw failure;if(url.includes('/abort'))throw abort;
  return new Response('{}',{status:url.includes('/bad')?503:200});
 };
 global.window=target;logs.installBrowserLogging(()=> 's1');const wrapped=target.fetch;
 logs.installBrowserLogging(()=> 's2');assert.equal(target.fetch,wrapped);
 const response=await target.fetch('/api/bad?secret=hidden',{method:'post'});assert.equal(response.status,503);
 await assert.rejects(target.fetch('/api/fail'),e=>e===failure);
 await assert.rejects(target.fetch('/api/abort'),e=>e===abort);
 await target.fetch('https://elsewhere.test/api/bad');await target.fetch('/api/logs?since=0');
 const event=new Event('error');Object.assign(event,{message:'Oops',filename:'main.js',lineno:4,error:new Error('Oops')});target.dispatchEvent(event);
 const rejection=new Event('unhandledrejection');rejection.reason=new Error('Rejected');target.dispatchEvent(rejection);
 assert(pending);pending();await Promise.resolve();
 const post=calls.find(c=>c.url==='/api/logs/browser');assert(post);assert(post.options.keepalive);
 const body=JSON.parse(post.options.body);assert.equal(body.session,'s1');assert.equal(body.entries.length,4);
 assert.equal(body.entries[0].message,'POST /api/bad → 503');assert.equal(body.entries[1].message,'GET /api/fail failed: Connection lost');
 assert(body.entries[2].message.includes('main.js:4'));assert.equal(body.entries[3].message,'Rejected');
 assert(!post.options.body.includes('secret'));assert(!pending);
 let memoStream;global.EventSource=class extends EventTarget {constructor(){super();memoStream=this;}};
 require('./src/api.ts').api.memo('s1','ft');memoStream.dispatchEvent(new Event('error'));memoStream.dispatchEvent(new Event('error'));
 pending();await Promise.resolve();const last=JSON.parse(calls[calls.length-1].options.body);
 assert.equal(last.entries.length,1);assert.equal(last.entries[0].message,'memo stream error');
 global.setTimeout=oldTimeout;global.clearTimeout=oldClear;delete global.window;
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=FRONTEND, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_assistant_chat_typing_and_finished_trail():
    if not shutil.which("node") or not (FRONTEND / "node_modules/typescript").exists():
        pytest.skip("Frontend node_modules and Node are required")
    script = r"""
const fs=require('fs'), assert=require('assert'), ts=require('typescript');
require.extensions['.tsx']=require.extensions['.ts']=(module,filename)=>module._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,esModuleInterop:true,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,filename);
const React=require('react'), {renderToStaticMarkup:render}=require('react-dom/server');
const {reduceTurn,TurnView}=require('./src/components/Assistant.tsx');
const view=events=>render(React.createElement(TurnView,{turn:reduceTurn(events),jump:()=>{}}));
const running=view([{type:'step',name:'reader',page_id:'p2',status:'start'}]);
for(const text of ['assistant-typing','Reading p2…','assistant-dot-running','open=""','aria-live="polite"','LL'])assert(running.includes(text),text);
assert(view([{type:'step',name:'writer',section:'cash_flow',status:'start'}]).includes('Writing Cash flow…'));
assert(view([{type:'step',name:'checker',status:'start'}]).includes('Checking figures…'));
const finished=view([{type:'route',intent:'memo'},{type:'step',name:'reader',page_id:'p2',status:'done',ms:1200},{type:'final',intent:'memo',memo:{summary:'Checked summary [p2:r1]'}},{type:'metrics',total_s:2.5}]);
for(const text of ['Credit memo','assistant-intent-pill','assistant-intent-violet','steps · 2.5 s','Copy Summary','assistant-citation'])assert(finished.includes(text),text);
assert(!finished.includes('open=""'));assert(!finished.includes('assistant-typing'));
const answer=view([{type:'final',intent:'knowledge',answer:'A finished answer'}]);
assert(answer.includes('Copy answer'));assert(answer.includes('assistant-intent-good'));
const failed=view([{type:'step',name:'reader',status:'start'},{type:'error',message:'Connection lost'}]);
assert(failed.includes('assistant-dot-bad'));assert(!failed.includes('open=""'));assert(!failed.includes('assistant-typing'));
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=FRONTEND, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_memo_report_and_trace():
    if not shutil.which("node") or not (FRONTEND / "node_modules/typescript").exists():
        pytest.skip("Frontend node_modules and Node are required")
    script = r"""
const fs=require('fs'),assert=require('assert'),ts=require('typescript');
require.extensions['.tsx']=require.extensions['.ts']=(m,f)=>m._compile(ts.transpileModule(fs.readFileSync(f,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,esModuleInterop:true,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,f);
const React=require('react'),{renderToStaticMarkup:render}=require('react-dom/server');
const {traceModel,Trace}=require('./src/components/Trace.tsx');
const events=[{type:'step',name:'router',status:'start',ts:100},{type:'step',name:'analyst',status:'start',ts:100},{type:'step',name:'router',status:'done',ts:101},{type:'step',name:'analyst',status:'done',ts:102},{type:'step',name:'writer',section:'summary',status:'start',ts:102},{type:'step',name:'writer',section:'cash_flow',status:'start',ts:102},{type:'step',name:'writer',section:'summary',status:'done',ts:103},{type:'step',name:'writer',section:'cash_flow',status:'done',ts:104},{type:'step',name:'checker',section:'summary',status:'start',ts:103},{type:'step',name:'checker',section:'summary',status:'done',ts:104},{type:'section_reset',section:'summary',ts:104},{type:'step',name:'writer',section:'summary',status:'start',ts:104},{type:'step',name:'writer',section:'summary',status:'done',ts:106},{type:'final',ts:106},{type:'metrics',delayTime:2000,ts:106}];
const model=traceModel(events);assert.equal(model.start,98);assert.equal(model.end,106);
assert.equal(model.lanes[0].label,'Queue / cold start');const writers=model.lanes.filter(l=>l.kind==='writer');assert.equal(writers.length,2);assert.deepEqual(writers[0].spans.map(s=>s.rewrite),[false,true]);assert.deepEqual(writers[0].spans.map(s=>s.end-s.start),[1,2]);assert(model.edges.length);
const replay=traceModel([{type:'step',name:'router',status:'start',t:0,rx:1000},{type:'step',name:'router',status:'done',t:2,rx:1000.01}]);assert.equal(replay.lanes[0].spans[0].end,1002);
const html=render(React.createElement(Trace,{events,showLogs:()=>{}}));assert(html.includes('<svg'));assert(html.includes('Timeline'));assert(html.includes('role="button"'));
const {LogsDrawer}=require('./src/components/Logs.tsx');const drawer=render(React.createElement(LogsDrawer,{open:true,session:'s',onClose:()=>{},initial:{tab:'trace',events}}));assert(drawer.includes('<svg'));assert(drawer.includes('Timeline'));
const {reduceTurn,TurnView}=require('./src/components/Assistant.tsx');
const report={type:'report',title:'Credit memo',subtitle:'Cash-flow review',borrower:'Fictional Goods',bank:'Harbour Bank',account:'***123',currency:'SGD',period:{start:'2026-01-01',end:'2026-02-28'},prepared:'2026-03-01',audience:'Credit officer',evidence:{statements:2,invoices:1,page_ids:['p1','p2','p3']},totals:{inflow:'10000',outflow:'9000',net:'1000'},balances:{average:{value:'2000',cites:[]},minimum:{value:'500',cites:[]}},monthly:[{month:'2026-01',inflow:'5000',outflow:'4000',net:'1000',partial:false},{month:'2026-02',inflow:'5000',outflow:'5000',net:'0',partial:true}],top_payer:{payer:'Juniper Goods',amount:'3000',total_inflow:'10000',share_percent:'30',cites:['[p1:r1]']},findings:[{type:'returned_cheque',label:'Returned cheque',amount:'100',cites:['[p1:r2]'],description:'Cheque returned'}],reconciliation:[]};
const memo={summary:'## Summary\nSound cash position.',cash_flow:'## Cash flow\nStable flows.',questions:'## Questions for the applicant\n1. Explain the cheque.'};
const view=es=>render(React.createElement(TurnView,{turn:reduceTurn(es),jump:()=>{}}));
const withReport=view([report,{type:'final',intent:'memo',memo,problems:[]}]);for(const t of ['Credit memo','Key figures','10,000.00','2026-01','2026-02','Returned cheque','All figures checked','Copy memo'])assert(withReport.includes(t),t);

const {MemoReport,ReportDialog,activityText}=require('./src/components/MemoReport.tsx');
assert(!withReport.includes('<th>#</th>'));
assert(withReport.includes('class="report-nowrap">Returned cheque</td>'));
assert(withReport.includes('Open full report'));
const activityReport={...report,totals:{...report.totals,months:3,full_months:2},
 monthly:[...report.monthly,{month:'2026-03',partial:true}]};
assert.equal(activityText(activityReport),'Jan – Mar 2026 (3 months, 2 full)');
assert(view([activityReport,{type:'final',intent:'memo',memo}]).includes('Jan – Mar 2026 (3 months, 2 full)'));
// The existing helpers render on the server, so exercise the dialog in its open state.
const dialog=render(React.createElement(ReportDialog,{onClose:()=>{}},React.createElement(MemoReport,{report,finished:true,problems:0,texts:[]})));
assert(dialog.includes('role="dialog"'));assert(dialog.includes('aria-modal="true"'));
assert(dialog.includes('Close</button>'));assert(dialog.includes('Fictional Goods'));
assert(drawer.includes('role="tab" aria-selected="true"'));
assert(drawer.includes('role="tab" aria-selected="false"'));
const sections=['summary','cash_flow','concentration','red_flags','questions'];
const recorded=[
 {type:'step',name:'router',status:'done',ms:100,t:0.1},
 {type:'step',name:'analyst',status:'done',ms:100,t:0.2},
 ...sections.flatMap((section,i)=>[
  {type:'step',name:'writer',section,status:'done',ms:5000,t:6+i/10},
  {type:'step',name:'checker',section,status:'done',ms:1,t:6.1+i/10}]),
 {type:'final',intent:'memo',t:7,source:'recorded'},
 {type:'metrics',total_s:8.5,t:7.1,source:'recorded'}
].map(({t,...event})=>({...event,ts:1000+t,rx:1000+Math.min(t,4.3)}));
const recordedModel=traceModel(recorded);
assert(recordedModel.end-recordedModel.start>=8.5);
assert.deepEqual(recordedModel.lanes.slice(2).map(l=>l.id),
 sections.flatMap(s=>['writer:'+s,'checker:'+s]));
const graph=render(React.createElement(Trace,{events:recorded,showLogs:()=>{}}));
for(const id of ['router-0','analyst-0','answer',...sections.flatMap(s=>['writer:'+s+'-0','checker:'+s+'-0'])])
 assert(graph.includes('data-node="'+id+'"'),id);
assert(graph.includes('viewBox="0 0 ')&&graph.includes('width:100%'));assert(graph.includes('Recorded run · times as recorded'));
assert(graph.includes('max(6px'));
const originalUseState=React.useState;
try {
 React.useState=()=>['router-0',()=>{}];
 const selected=render(React.createElement(Trace,{events:recorded,showLogs:()=>{},
  entries:[{id:1,ts:1000.1,source:'worker',level:'info',message:'Router log in selected window'}]}));
 assert(selected.includes('class="trace-selected"'));
 assert(selected.includes('Router log in selected window'));
 const empty=render(React.createElement(Trace,{events:recorded,showLogs:()=>{}}));
 assert(empty.includes('No log lines in this window'));
} finally { React.useState=originalUseState; }


const old=view([{type:'final',intent:'memo',memo}]);assert(old.includes('Sound cash position.'));assert(!old.includes('Key figures'));assert(old.includes('report-paper'));
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=FRONTEND, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
