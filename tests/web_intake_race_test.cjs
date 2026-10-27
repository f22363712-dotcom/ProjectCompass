const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    innerHTML: '', textContent: '', value: 'Why this state?', hidden: true,
    classList: {toggle() {}, add() {}, remove() {}},
    addEventListener() {}, setAttribute() {}, append() {}, prepend() {},
    querySelector() {return element(id + '-child');},
  });
  return elements.get(id);
}
function state(count) {
  return {project:{name:'Synthetic race fixture', evidence_count:count, fact_count:0}, personal:{},
    team:{facts:[],evidence_ids:[],conflicts:[]}, evidence:[],candidate_relations:[],confirmed_relations:[],
    review_history:[],fact_reviews:[],intake:{enabled:true,captured_messages:count,new_messages:count-1}};
}
function response(body) {return {ok:true,json:async()=>body};}
let finishInvestigation;
let intakeTimer;
const delayed = new Promise(resolve => {finishInvestigation=resolve;});
const context = vm.createContext({
  console, AbortController, setTimeout, clearTimeout, setInterval(callback, milliseconds) {intakeTimer={callback,milliseconds};},
  document:{hidden:false,getElementById:element,querySelectorAll:()=>[],createElement:()=>element('created')},
  fetch:async(path)=>path==='/api/state' ? response(state(1)) : path==='/api/refresh' ? response(state(2)) : delayed,
});
async function main() {
  vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  const pending=vm.runInContext('investigateProject({preventDefault(){}})',context);
  assert.equal(intakeTimer.milliseconds,5000);
  intakeTimer.callback();
  await new Promise(resolve=>setImmediate(resolve));
  assert.ok(element('investigation-answer').innerHTML.includes('已接入新消息'));
  finishInvestigation(response({conclusion:'OLD ANSWER BASED ON 1 EVENT',goal:'Old state',active_tasks:[],
    evidence_ids:[],conflicts:[],next_action:'Old action',acceptance_condition:'Old acceptance',evidence_gaps:[],findings:[]}));
  await pending;
  assert.ok(!element('investigation-answer').innerHTML.includes('OLD ANSWER'), 'Delayed investigation replaced the new-evidence warning');
  console.log('Stale investigation response discarded after source refresh.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
