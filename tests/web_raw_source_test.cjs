const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map();
function element(id){if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',value:'',hidden:true,
 classList:{toggle(){},add(){},remove(){}},addEventListener(){},setAttribute(){},append(){},appendChild(){},prepend(){},
 querySelector(){return element(id+'-child');}});return elements.get(id);}
const data={project:{name:'Synthetic raw investigation',evidence_count:0,fact_count:0},personal:{},
 team:{facts:[],evidence_ids:[],conflicts:[]},evidence:[],candidate_relations:[],confirmed_relations:[]};
const context=vm.createContext({console,AbortController,setTimeout,clearTimeout,setInterval(){},
 document:{hidden:false,getElementById:element,querySelectorAll:()=>[],createElement:()=>element('created')},
 fetch:async()=>({ok:true,json:async()=>data})});
async function main(){vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);await new Promise(r=>setImmediate(r));
 const answer={conclusion:'Recorded originals',goal:'Synthetic',active_tasks:[],next_action:'Check sources',
 acceptance_condition:'No automatic acceptance',evidence_ids:['git','native','reported'],conflicts:[],findings:[
 {source_kind:'git_commit',quote:'Commit <script>',status:'raw',claim_type:'document_statement',evidence_ids:['git'],source_ref:'git:abc'},
 {source_kind:'command_execution',quote:'Failure output',status:'raw',claim_type:'execution_record',evidence_ids:['native'],
  command_preview:'check <img>',command_origin:'native_trace',exit_code:1},
 {source_kind:'tool_result',quote:'Agent output',status:'raw',claim_type:'assistant_report',evidence_ids:['reported'],
  command_preview:'read-report',command_origin:'agent_report',exit_code:0}]};
 context.answer=answer;vm.runInContext('renderInvestigation(answer)',context);
 let html=element('investigation-answer-child').innerHTML;
 assert.ok(html.includes('Git 提交')&&html.includes('git:abc'),'Raw source identity was not rendered');
 assert.ok(html.includes('原生命令记录')&&html.includes('Agent命令报告'));
 assert.ok(html.includes('退出码 1')&&html.includes('退出码 0'));
 assert.ok(html.includes('check &lt;img&gt;')&&!html.includes('check <img>'));
 assert.ok(html.includes('Commit &lt;script&gt;')&&!html.includes('Commit <script>'));
 assert.ok(html.includes('data-evidence-id="git"')&&html.includes('data-evidence-id="native"'));
 answer.findings[1].exit_code=null;vm.runInContext('renderInvestigation(answer)',context);
 assert.ok(element('investigation-answer-child').innerHTML.includes('退出码 未知'));
 console.log('Raw Git/source refs, native/Agent origin, normalized exit, links and escaping verified.');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
