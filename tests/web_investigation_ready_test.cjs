const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const nodes=new Map();
function el(id){if(!nodes.has(id))nodes.set(id,{innerHTML:'',textContent:'',value:'',hidden:true,disabled:false,
 classList:{toggle(){},add(){},remove(){}},addEventListener(){},setAttribute(){},append(){},appendChild(){},prepend(){},
 querySelector(){return el(id+'-child');}});return nodes.get(id);}
let resolveState,resolveAnswer,posts=0;
const pending=new Promise(resolve=>{resolveState=resolve});
const pendingAnswer=new Promise(resolve=>{resolveAnswer=resolve});
const data={project:{name:'Synthetic startup race',evidence_count:0,fact_count:0},personal:{},
 team:{facts:[],evidence_ids:[],conflicts:[]},evidence:[],candidate_relations:[],confirmed_relations:[],review_history:[],fact_reviews:[]};
const answer={conclusion:'Loaded answer',goal:'Known state',active_tasks:[],evidence_ids:[],conflicts:[],next_action:'Check',acceptance_condition:'Done'};
const context=vm.createContext({console,AbortController,setTimeout(){return 0},clearTimeout(){},setInterval(){},
 document:{hidden:false,getElementById:el,querySelectorAll:()=>[],createElement:()=>el('created')},
 fetch:async(url,options)=>{if(options?.method==='POST'){posts++;return pendingAnswer}return pending;}});
async function main(){vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
 el('investigation-question').value='当前状态是什么？';
 const early=vm.runInContext('investigateProject({preventDefault(){}})',context);
 const beforeReadyPosts=posts;
 resolveState({ok:true,json:async()=>data});await new Promise(r=>setImmediate(r));
 resolveAnswer({ok:true,json:async()=>answer});await early;
 assert.ok(!el('investigation-answer').innerHTML.includes('正在按证据链调查'),'Late answer was silently dropped after initial-state revision changed');
 assert.equal(beforeReadyPosts,0,'Investigation POST admitted before initial state was ready');
 assert.ok(el('investigation-answer').innerHTML.includes('加载'),'Missing explicit initial-state wait message');
 const html=fs.readFileSync(path.join(path.dirname(process.argv[2]),'index.html'),'utf8');
 assert.match(html,/<button[^>]*id="investigate-submit"[^>]*disabled/,'Initial submit control is enabled');
 assert.equal(el('investigate-submit').disabled,false);
 await vm.runInContext('investigateProject({preventDefault(){}})',context);
 assert.equal(posts,1);assert.ok(el('investigation-answer').innerHTML.includes('Loaded answer'));
 console.log('Initial-state wait, disabled control and successful post-load investigation verified.');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
