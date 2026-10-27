const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map();function element(id){if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',value:'',hidden:true,
 classList:{toggle(){},add(){},remove(){}},addEventListener(){},setAttribute(){},append(){},prepend(){},querySelector(){return element(id+'-child');}});return elements.get(id);}
let payload={project:{name:'Synthetic Agent API',evidence_count:0,fact_count:0},personal:{},team:{facts:[],evidence_ids:[],conflicts:[]},evidence:[],candidate_relations:[],confirmed_relations:[],review_history:[],fact_reviews:[],intake:{enabled:false},agent_api:{enabled:true,owner_id:'synthetic-owner',clients:[{member_id:'agent-a'}]}};
const calls=[];const context=vm.createContext({console,AbortController,setTimeout,clearTimeout,setInterval(){},document:{hidden:false,getElementById:element,querySelectorAll:()=>[],createElement:()=>element('created')},fetch:async path=>{calls.push(path);return {ok:true,json:async()=>structuredClone(payload)}}});
async function main(){vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);await new Promise(r=>setImmediate(r));
 payload={...payload,project:{...payload.project,evidence_count:1},personal:{'agent-a':{facts:[],evidence_ids:['new']}},evidence:[{id:'new',kind:'assistant_message',content_preview:'A real-context report arrives',author:'codex'}]};
 await vm.runInContext('refreshNative()',context);
 assert.equal(calls.filter(p=>p==='/api/state').length,2,'Agent-only board did not poll shared state');
 assert.ok(element('evidence-spine').innerHTML.includes('A real-context report arrives'));
 assert.equal(element('reviewer-input').value,'synthetic-owner');
 assert.equal(element('reviewer-input').readOnly,true);
 assert.equal(element('intake-bar').hidden,false);
 console.log('Agent-only live polling and configured browser-owner identity verified.');}
main().catch(e=>{console.error(e);process.exitCode=1;});
