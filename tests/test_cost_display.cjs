const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
const context = vm.createContext({});
vm.runInContext(source.slice(0, source.indexOf('const historyList =')), context);
const call = (fields = {}) => ({provider:'codex', model:'deepseek-v4-1-flash-261017', ...fields});
test('CNY input/output prices, no double-counted cache or reasoning', () => {
  const result = context.apiCost([call({cached_input_tokens:1000000, cache_write_input_tokens:1000000, input_tokens:5000000, output_tokens:90000, reasoning_output_tokens:80000})]);
  assert.ok(Math.abs(result.amount - 6.432) < 1e-10);
  assert.equal(result.missing, 0);
  assert.match(context.apiCostText([call({input_tokens:1000000,output_tokens:1000000})]), /¥6\.0000/);
});
test('Unknown counters and unpriced models are not treated as free', () => {
  assert.equal(context.apiCost([call({})]).amount, null);
  assert.equal(context.apiCost([call({cached_input_tokens:NaN})]).amount, null);
  assert.equal(context.apiCost([call({model:'gpt-6-sol', cached_input_tokens:1000000})]).amount, null);
  assert.equal(context.apiCost([call({provider:'other', cached_input_tokens:1000000})]).amount, null);
  const knownZero = context.apiCost([call({input_tokens:0})]);
  assert.equal(knownZero.amount, 0);
  assert.equal(knownZero.missing, 1);
});
test('Task total includes front stages and failed calls once; rounds partition total', () => {
  const calls = [call({round_number:0,input_tokens:100,status:'completed'}),call({round_number:1,input_tokens:200,status:'error'}),call({round_number:1,input_tokens:300,status:'completed'}),call({round_number:2,input_tokens:400,status:'completed'})];
  const roundSum = [0,1,2].reduce((sum, n) => sum + context.apiCost(calls.filter(c => c.round_number === n)).amount, 0);
  assert.ok(Math.abs(context.apiCost(calls).amount - roundSum) < 1e-10);
  assert.ok(Math.abs(roundSum - .0012) < 1e-10);
});

test('GPT uses its own input/output rates', () => {
  const result = context.apiCost([{provider:'openai',model:'gpt-5.6-sol',input_tokens:244280,output_tokens:14880,cached_input_tokens:38016}]);
  assert.ok(Math.abs(result.amount - 1.6678) < 1e-10);
});
