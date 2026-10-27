const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {innerHTML:'',textContent:'',value:'',hidden:true,
    classList:{toggle(){},add(){},remove(){}},addEventListener(){},setAttribute(){},append(){},prepend(){},
    querySelector(){return element(id+'-child');}});
  return elements.get(id);
}
function state(version, count) {
  return {state_version:version,project:{name:'Synthetic rescan race',evidence_count:count,fact_count:0},
    personal:{},team:{facts:[],evidence_ids:[],conflicts:[]},evidence:[],candidate_relations:[],confirmed_relations:[],
    review_history:[],fact_reviews:[],intake:{enabled:true,captured_messages:count},
    project_scan:{enabled:true,updated:version===2,file_count:1,changed_files:1,unavailable_files:0,last_checked_at:'2026-10-25T10:00:00Z'}};
}
const response=body=>({ok:true,json:async()=>body});
let releaseOld;
const old=new Promise(resolve=>{releaseOld=resolve;});
const context=vm.createContext({console,AbortController,setTimeout,clearTimeout,setInterval(){},document:{hidden:false,getElementById:element,
  querySelectorAll:()=>[],createElement:()=>element('new')},
  fetch:async path=>path==='/api/state'?response(state(0,1)):path==='/api/refresh'?old:response(state(2,3))});
async function main() {
  vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  const polling=vm.runInContext('refreshNative()',context);
  await vm.runInContext('rescanProject()',context);
  releaseOld(response(state(1,2)));
  await polling;
  assert.equal(vm.runInContext('state.data.project.evidence_count',context),3,'Old native response overwrote completed project rescan');
  assert.equal(vm.runInContext('state.data.state_version',context),2);
  console.log('Older capture response cannot roll back a completed project rescan.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
