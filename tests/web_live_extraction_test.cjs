const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map();
function element(id){if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',value:'',hidden:true,
 classList:{toggle(){},add(){},remove(){}},addEventListener(){},setAttribute(){},append(){},prepend(){},
 querySelector(){return element(id+'-child');}});return elements.get(id);}
function payload(done){return {state_version:done?1:0,project:{name:'Synthetic live extraction',evidence_count:1,fact_count:done?1:0},
 personal:{},team:{facts:done?[{id:'new',kind:'task',value:'New proposed task',status:'proposed',evidence_ids:[]}]:[],evidence_ids:[],conflicts:[]},
 evidence:[],candidate_relations:[],confirmed_relations:[],review_history:[],fact_reviews:[],intake:{enabled:false},
 extraction:{enabled:true,state:done?'complete':'running',running:!done,batches_used:1,max_batches:2,known_tokens:25,max_tokens:80000,unknown_usage_requests:0,pending_chars:0}};}
let timer,requests=0;
const context=vm.createContext({console,AbortController,setTimeout,clearTimeout,setInterval(callback){timer=callback;},document:{hidden:false,getElementById:element,
 querySelectorAll:()=>[],createElement:()=>element('created')},fetch:async path=>{
 assert.equal(path,'/api/state','Model or native route called while polling document-only extraction');
 return {ok:true,json:async()=>payload(requests++>0)};}});
async function main(){vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);await new Promise(r=>setImmediate(r));
 timer();await new Promise(r=>setImmediate(r));
 assert.equal(vm.runInContext('state.data.project.fact_count',context),1,'New candidates lost because raw count stayed unchanged');
 assert.ok(element('investigation-answer').innerHTML.includes('新候选'));
 console.log('Candidate-only completion updates a document-only board without a model call from polling.');}
main().catch(error=>{console.error(error);process.exitCode=1;});
