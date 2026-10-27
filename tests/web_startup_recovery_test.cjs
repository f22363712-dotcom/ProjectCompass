const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[2],'utf8');
const payload={project:{name:'Synthetic startup recovery',evidence_count:1,fact_count:0},personal:{'agent-a':{facts:[],evidence_ids:['synthetic']}},team:{facts:[],evidence_ids:['synthetic'],conflicts:[]},evidence:[{id:'synthetic',kind:'assistant_message',author:'codex',content_preview:'Synthetic persisted statement'}],candidate_relations:[],confirmed_relations:[],review_history:[],fact_reviews:[],intake:{enabled:false}};
const flush=async()=>{for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));};
async function run(mode){
 const elements=new Map();function element(id){if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',value:'',hidden:true,classList:{toggle(){},add(){},remove(){}},addEventListener(){},setAttribute(){},append(){},prepend(){}});return elements.get(id);}
 const timers=new Map(),delays=[],calls=[];let next=0;
 const context=vm.createContext({console,AbortController,setInterval(){},
  setTimeout(fn,ms){const id=++next;timers.set(id,{fn,ms});if(ms<8000){delays.push(ms);queueMicrotask(()=>{if(timers.delete(id))fn();});}return id;},clearTimeout(id){timers.delete(id);},
  document:{hidden:false,getElementById:element,querySelectorAll:()=>[],createElement:()=>element('created')},
  fetch:async(path,options)=>{
   calls.push({path,method:options?.method||'GET'});const attempt=calls.length;
   if(mode==='network' || (mode==='recover'&&attempt===1))throw new TypeError('Failed to fetch');
   if(mode==='timeout')return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(Object.assign(new Error('request aborted'),{name:'AbortError'}))));
   if(mode==='http')return {ok:false,status:403,json:async()=>{throw new Error('must not parse denied response');}};
   return {ok:true,status:200,json:async()=>{
    if(mode==='json')throw new SyntaxError('malformed response');
    if(mode==='schema')return {};
    if(mode==='member-schema')return {...structuredClone(payload),personal:{'agent-a':{}}};
    if(mode==='team-schema')return {...structuredClone(payload),team:{facts:[]}};
    if(mode==='body'&&attempt===1)throw new TypeError('network body interrupted');
    if(mode==='bodytimeout')return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(Object.assign(new Error('body aborted'),{name:'AbortError'}))));
    return structuredClone(payload);
   }};
  }});
 vm.runInContext(source,context);await flush();
 if(['timeout','bodytimeout'].includes(mode))for(let i=0;i<3;i++){
  const timer=[...timers.entries()].find(([,t])=>t.ms===8000);
  assert.ok(timer,'No bounded timeout for pending initial read');timers.delete(timer[0]);timer[1].fn();await flush();
 }
 const recovered=mode==='recover'||mode==='body';
 assert.equal(calls.length,recovered?2:['network','timeout','bodytimeout'].includes(mode)?3:1,mode+' request count');
 assert.ok(calls.every(c=>c.path==='/api/state'&&c.method==='GET'),'Startup retry triggered a mutation');
 assert.deepEqual(delays,recovered?[250]:['network','timeout','bodytimeout'].includes(mode)?[250,750]:[]);
 assert.equal(timers.size,0,'Startup timer leaked');
 if(recovered){assert.ok(element('evidence-spine').innerHTML.includes('Synthetic persisted statement'));assert.ok(!element('connection-label').textContent.includes('unavailable'));}
 else{assert.ok(element('connection-label').textContent.includes('unavailable'));assert.equal(element('evidence-spine').innerHTML,'','Malformed/failed state rendered as empty truth');}
}
(async()=>{for(const mode of ['recover','network','timeout','bodytimeout','http','json','schema','member-schema','team-schema','body'])await run(mode);console.log('Ten synthetic startup recovery, failure and timeout cases passed; GET-only, bounded, timer-clean.');})().catch(error=>{console.error(error);process.exitCode=1;});
