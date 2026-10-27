const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map();
function element(id){if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',value:'',hidden:true,
 classList:{toggle(){},add(){},remove(){}},addEventListener(){},setAttribute(){},append(){},appendChild(){},prepend(){},
 querySelector(){return element(id+'-child');}});return elements.get(id);}
const report={id:'agent-failed',kind:'tool_result',author:'Alice',content_preview:'Original output only.',
 command_preview:'run-central.ps1 -Phase unit',command_origin:'agent_report',exit_code:1};
const retry={...report,id:'agent-passed',exit_code:0};
const native={...report,id:'bob-native',kind:'command_execution',author:'Bob',command_origin:'native_trace',exit_code:0};
const legacy={id:'legacy',content_preview:'Old payload without command fields.'};
const data={project:{name:'Synthetic command search',evidence_count:4,fact_count:0},
 personal:{Alice:{facts:[],evidence_ids:['agent-failed','agent-passed','legacy']},Bob:{facts:[],evidence_ids:['bob-native']}},
 team:{facts:[],evidence_ids:['agent-failed','agent-passed','legacy','bob-native'],conflicts:[]},
 evidence:[report,retry,native,legacy],candidate_relations:[],confirmed_relations:[],review_history:[],fact_reviews:[]};
const sources={'agent-failed':{id:'agent-failed',kind:'tool_result',author:'codex',content:'Failure output',
 metadata:{command:['pwsh','-Command','run-central.ps1 -Phase unit'],exit_code:1,origin:'agent_submission',actor_type:'agent',reported_source_ref:'native.jsonl:12 <script>'}},
 'bob-native':{id:'bob-native',kind:'command_execution',content:'Recorded output',metadata:{command:['python','checks.py'],exit_code:0}}};
const context=vm.createContext({console,AbortController,setTimeout,clearTimeout,setInterval(){},
 document:{hidden:false,getElementById:element,querySelectorAll:()=>[],createElement:()=>element('created')},
 fetch:async url=>({ok:true,json:async()=>url.startsWith('/api/evidence')?sources[new URL('http://local'+url).searchParams.get('id')]:data})});
async function main(){vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);await new Promise(r=>setImmediate(r));
 vm.runInContext("state.member='Alice';state.evidenceQuery='run-central.ps1';render()",context);
 let html=element('evidence-spine').innerHTML;
 assert.ok(html.includes('agent-failed')&&html.includes('agent-passed'),'Command-only query missed existing reports');
 assert.ok(!html.includes('bob-native')&&!html.includes('legacy'),'Search escaped selected member or matched unrelated record');
 assert.ok(html.includes('Agent命令报告')&&html.includes('退出码 0')&&html.includes('退出码 1'));
 assert.equal(element('spine-count').textContent,'2/3');
 await vm.runInContext("openEvidence('agent-failed')",context);
 html=element('drawer-body').innerHTML;
 assert.ok(html.includes('AGENT COMMAND REPORT')&&html.includes('run-central.ps1'));
 assert.ok(html.includes('native.jsonl:12 &lt;script&gt;')&&!html.includes('native.jsonl:12 <script>'));
 report.exit_code=null;
 // Raw JSON0.0/1.0 become JS0/1; the catalog retains their rejected type.
 for(const rawExit of [false,0,1]){
  sources['agent-failed'].metadata.exit_code=rawExit;
  await vm.runInContext("openEvidence('agent-failed')",context);
  assert.ok(element('drawer-body').innerHTML.includes('退出码 未知'),'Malformed legacy exit became a run result');
 }
 await vm.runInContext("openEvidence('bob-native')",context);
 assert.ok(element('drawer-body').innerHTML.includes('COMMAND EXECUTION'));
 vm.runInContext("state.lens='team';render()",context);
 assert.ok(element('evidence-spine').innerHTML.includes('原生命令记录'));
 const escaped=vm.runInContext("evidenceCard({id:'x',command_preview:'<img src=x onerror=alert(1)>',command_origin:'agent_report'})",context);
 assert.ok(!escaped.includes('<img')&&escaped.includes('&lt;img'));
 assert.ok(vm.runInContext("evidenceCard({id:'legacy',content_preview:'Old payload'})",context).includes('Old payload'));
 console.log('Command-only scoped search, origin/exit labels, original source drawer and escaping verified.');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
