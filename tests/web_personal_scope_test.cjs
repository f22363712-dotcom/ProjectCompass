const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map();
function element(id){if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',value:'',hidden:true,
 classList:{toggle(){},add(){},remove(){}},addEventListener(){},setAttribute(){},append(){},prepend(){},
 querySelector(){return element(id+'-child');}});return elements.get(id);}
const alice={id:'alice-fact',kind:'task',value:'Alice task',status:'proposed',evidence_ids:['a'],
 source_state:{status:'superseded',replacement_ids:['r'],observation_ids:['o']}};
const bob={id:'bob-fact',kind:'task',value:'Bob task',status:'proposed',evidence_ids:['b']};
const data={project:{name:'Synthetic personal scope',evidence_count:6,fact_count:2},intake:{enabled:true,member_id:'alice'},
 personal:{'project-documents':{facts:[],evidence_ids:['d','r','o']},alice:{facts:[alice],evidence_ids:['a']},bob:{facts:[bob],evidence_ids:['b','s']}},
 team:{facts:[alice,bob],evidence_ids:['a','b','d','s','r','o'],conflicts:[]},
 evidence:[['a','ALICE_SOURCE'],['b','BOB_UNRELATED'],['d','PROJECT_DOC'],['s','CONFIRMED_SUPPORT'],['r','REPLACEMENT'],['o','OBSERVATION']].map(([id,content_preview])=>({id,content_preview})),
 candidate_relations:[{id:'ca',fact_id:'alice-fact',evidence_id:'a',status:'candidate'},{id:'cb',fact_id:'bob-fact',evidence_id:'b',status:'candidate'}],
 confirmed_relations:[{id:'cs',fact_id:'alice-fact',evidence_id:'s',status:'confirmed'}],review_history:[],fact_reviews:[]};
const context=vm.createContext({console,AbortController,setTimeout,clearTimeout,setInterval(){},document:{hidden:false,getElementById:element,querySelectorAll:()=>[],createElement:()=>element('created')},
 fetch:async()=>({ok:true,json:async()=>data})});
async function main(){vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);await new Promise(r=>setImmediate(r));
 assert.equal(vm.runInContext('state.member',context),'alice','Connected member was not selected');
 const personal=element('evidence-spine').innerHTML;
 assert.ok(personal.includes('ALICE_SOURCE')&&personal.includes('CONFIRMED_SUPPORT')&&personal.includes('REPLACEMENT')&&personal.includes('OBSERVATION'));
 assert.ok(!personal.includes('BOB_UNRELATED')&&!personal.includes('PROJECT_DOC'),'Personal spine leaked unrelated catalog');
 assert.equal(element('evidence-count').textContent,6,'Project header became a personal count');
 assert.equal(element('spine-count').textContent,4);
 vm.runInContext("state.lens='team';render()",context);
 assert.ok(element('evidence-spine').innerHTML.includes('BOB_UNRELATED')&&element('evidence-spine').innerHTML.includes('PROJECT_DOC'));
 assert.equal(element('spine-count').textContent,6);
 vm.runInContext("state.lens='personal';state.member='bob';render()",context);
 assert.equal(vm.runInContext('state.member',context),'bob','Manual member choice was overwritten by connected member');
 assert.ok(!element('evidence-spine').innerHTML.includes('ALICE_SOURCE'));
 vm.runInContext("state.member=null;state.data.intake={enabled:false};state.data.extraction={enabled:true,member_id:'bob',state:'idle',running:false,batches_used:0,max_batches:1,known_tokens:0,max_tokens:40000,unknown_usage_requests:0,pending_chars:1};render()",context);
 assert.equal(vm.runInContext('state.member',context),'bob','Manual-job member fallback was ignored');
 console.log('Connected default, scoped personal sources, confirmed/freshness links and full team catalog verified.');}
main().catch(error=>{console.error(error);process.exitCode=1;});
