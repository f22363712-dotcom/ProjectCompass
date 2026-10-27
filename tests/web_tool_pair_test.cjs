const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map();function element(id){if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',value:'',hidden:true,
 classList:{toggle(){},add(){},remove(){}},addEventListener(){},setAttribute(){},append(){},prepend(){},querySelector(){return element(id+'-child');}});return elements.get(id);}
const state={project:{name:'Synthetic trace',evidence_count:2,fact_count:0},personal:{owner:{facts:[],evidence_ids:['call','result']}},team:{facts:[],evidence_ids:['call','result'],conflicts:[]},evidence:[],candidate_relations:[],confirmed_relations:[],review_history:[],fact_reviews:[],intake:{enabled:true,member_id:'owner'}};
const records={call:{id:'call',kind:'tool_call',author:'codex',content:'<script>synthetic public input</script>',metadata:{paired_evidence_id:'result',tool:'functions.exec'}},result:{id:'result',kind:'tool_result',author:'codex',content:'{"exit_code":0}',metadata:{paired_evidence_id:'call',tool:'functions.exec'}}};
const context=vm.createContext({console,AbortController,setTimeout,clearTimeout,setInterval(){},document:{hidden:false,getElementById:element,querySelectorAll:()=>[],createElement:()=>element('created')},fetch:async path=>({ok:true,json:async()=>path==='/api/state'?state:records[decodeURIComponent(path.split('id=')[1])]})});
async function main(){vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);await new Promise(r=>setImmediate(r));
 await vm.runInContext("openEvidence('call')",context);
 assert.ok(element('drawer-body').innerHTML.includes('data-evidence-id="result"'),'Call has no reciprocal result link');
 assert.ok(element('drawer-body').innerHTML.includes('&lt;script&gt;'));
 await vm.runInContext("openEvidence('result')",context);
 assert.ok(element('drawer-body').innerHTML.includes('data-evidence-id="call"'),'Result has no call link');
 assert.ok(!element('drawer-body').innerHTML.includes('data-fact-review-id'));
 records.command={id:'command',kind:'command_execution',content:'',author:'codex',metadata:{command:['python','<script>'],cwd:'file:///synthetic/project',exit_code:0,execution_status:'completed',duration:{secs:1,nanos:0}}};
 await vm.runInContext("openEvidence('command')",context);
 let commandHtml=element('drawer-body').innerHTML;
 assert.ok(commandHtml.includes('COMMAND EXECUTION'),'Typed command has no dedicated provenance block');
 assert.ok(commandHtml.includes('退出码 0'),'Zero exit code was lost');
 assert.ok(commandHtml.includes('file:///synthetic/project'));
 assert.ok(commandHtml.includes('&lt;script&gt;'));
 assert.ok(commandHtml.includes('此命令没有文本输出'));
 assert.ok(!commandHtml.includes('data-fact-review-id'));
 records.command.metadata.exit_code=1;records.command.metadata.execution_status='failed';
 await vm.runInContext("openEvidence('command')",context);
 assert.ok(element('drawer-body').innerHTML.includes('退出码 1'));
 console.log('Public call/result drill-down links are reciprocal and do not promote facts.');}
main().catch(error=>{console.error(error);process.exitCode=1;});
