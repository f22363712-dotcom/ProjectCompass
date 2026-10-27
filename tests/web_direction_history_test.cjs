const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map();
function el(id){if(!nodes.has(id))nodes.set(id,{innerHTML:'',textContent:'',value:'',hidden:false,
 classList:{toggle(){},add(){},remove(){}},addEventListener(){},setAttribute(){},append(){},appendChild(){},prepend(){},
 querySelector(){return el(id+'-child');}});return nodes.get(id);}
const context=vm.createContext({console,AbortController,setTimeout,clearTimeout,setInterval(){},
 document:{hidden:false,getElementById:el,querySelectorAll:()=>[],createElement:()=>el('created')},
 fetch:async()=>({ok:true,json:async()=>({project:{},personal:{},team:{facts:[],directions:{candidates:[],current:[],history:[]}},evidence:[]})})});
async function main(){vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);await new Promise(r=>setImmediate(r));
 context.directions={candidates:[],current:[{fact_id:'f',quote:'放弃 <script>old</script>',direction_state:'abandoned',evidence_ids:['source'],reviewed_by:'owner',reviewed_at:'2026-10-26',current:true}],
 history:[{fact_id:'f',quote:'放弃旧方案',direction_state:'active',evidence_ids:['source'],reviewed_by:'owner',reviewed_at:'2026-10-26',current:true}]};
 vm.runInContext('renderDirections(directions, true)',context);
 const html=el('direction-list').innerHTML+el('direction-history').innerHTML;
 assert.ok(html.includes('放弃')&&html.includes('已恢复'));
 assert.ok(html.includes('&lt;script&gt;')&&!html.includes('<script>'));
 assert.ok(html.includes('data-fact-id="f"')&&html.includes('data-evidence-id="source"'));
 vm.runInContext('renderDirections({candidates:[{fact_id:"p",quote:"建议放弃旧插件",direction_state:"abandoned",status:"proposed",evidence_ids:[]}],current:[],history:[]},false)',context);
 assert.ok(!el('direction-list').innerHTML.includes('已放弃'),'Personal proposal was worded as an established state');
 context.fact={id:'f',value:'暂缓云同步',kind:'decision',status:'proposed',evidence_ids:['source'],subject_key:'cloud',direction_suggestion:'deferred'};
 vm.runInContext('state.data={team:{facts:[fact]},fact_reviews:[],candidate_relations:[],confirmed_relations:[]};state.lens="team";openFact("f")',context);
 assert.ok(el('drawer-body').innerHTML.includes('confirm_deferred'));
 vm.runInContext('state.data.team.directions={current:[{fact_id:"f",direction_state:"active"}],history:[]}',context);
 assert.ok(vm.runInContext('factCard(fact,[])',context).includes('已恢复推进'),'Fact card lost current restoration beside old source quote');
 vm.runInContext('state.data={intake:{enabled:false,direction_scan:{enabled:true,completed_sources:200,eligible_sources:205,pending_sources:5,scanned_chars:1000,total_chars:1300,max_statement_chars:1600}}};renderIntake()',context);
 assert.equal(el('intake-bar').hidden,false,'Non-native project rule scan progress must be visible');
 assert.match(el('intake-status').textContent,/方向规则已扫 200\/205/);
 assert.match(el('intake-status').textContent,/仍有 5 条/);
 assert.match(el('intake-status').textContent,/语义/);
 vm.runInContext('state.data.intake.direction_scan.pending_sources=0;renderIntake()',context);
 assert.match(el('intake-status').textContent,/语义/,'Rule completion cannot claim semantic coverage');
 vm.runInContext('state.data={intake:{enabled:false,user_task_scan:{enabled:true,completed_sources:15,eligible_sources:20,pending_sources:5,skipped_long_sources:2}}};renderIntake()',context);
 assert.equal(el('intake-bar').hidden,false);
 assert.match(el('intake-status').textContent,/用户任务规则已扫 15\/20/);
 assert.match(el('intake-status').textContent,/2 条长消息/);
 assert.match(el('intake-status').textContent,/语义/);
 console.log('Direction labels, history, source links, escaping and explicit owner action verified.');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
