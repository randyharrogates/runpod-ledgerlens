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
    compilerOptions: { esModuleInterop: true, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX }
  }).outputText, filename);
};
const React = require('react');
const {renderToStaticMarkup: render} = require('react-dom/server');
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
const {Cell} = require('./src/components/Review.tsx');
const cell = render(React.createElement(Cell, {
  value: '100', display: '100.00', edited: false, directionTitle: 'Moved to credit',
  numeric: true, label: 'Edit row 10 credit', save: async () => {}
}));
assert(cell.includes('cell-content'));
assert(cell.indexOf('↔') < cell.indexOf('100.00'));
const {Documents} = require('./src/components/Documents.tsx');
const page = {id: 'p1', status: 'extracted', metrics: {source: 'cached-demo', live_error: 'Oops'}};
const props = {pages: [page], types: {}, session: ''};
const warning = render(React.createElement(Documents, props));
assert(warning.includes('Live call failed — showing cached result'));
assert(warning.includes('Oops'));
assert(!warning.includes('Cached demo · ground truth'));
delete page.metrics.live_error;
assert(render(React.createElement(Documents, props)).includes('Cached demo · ground truth'));
const {UnderTheHood} = require('./src/components/UnderTheHood.tsx');
assert(render(React.createElement(UnderTheHood, {
  history: [{source: 'cached-demo', task: 'extract', live_error: 'Oops'}], status: {}
})).includes('Oops'));
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=FRONTEND, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
