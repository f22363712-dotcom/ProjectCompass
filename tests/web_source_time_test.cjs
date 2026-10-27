const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map();
function element(id){if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',value:'',hidden:true,
 classList:{toggle(){},add(){},remove(){}},addEventListener(){},setAttribute(){},append(){},appendChild(){},prepend(){},
 querySelector(){return element(id+'-child');}});return elements.get(id);}
const sourceTime='2026-10-19T06:17:38+00:00',reviewTime='2026-10-25T07:12:24+00:00';
const old={id:'old',kind:'task',subject_key:'install',value:'Candidate A installed; next retest.',
 status:'proposed',author:'Alice',evidence_ids:['source-old'],observed_at:sourceTime,
 source_kind:'assistant_message',session_id:'session-Alice'};
const goal={...old,id:'goal',kind:'goal',value:'Build the product.'};
const data={project:{name:'Temporal fixture',evidence_count:1,fact_count:2},
 personal:{Alice:{facts:[goal,old],evidence_ids:['source-old']}},
 team:{facts:[goal,old],evidence_ids:['source-old'],conflicts:[{subject_key:'install',sides:[old]}]},
 evidence:[{id:'source-old',content_preview:'Old report'}],candidate_relations:[],confirmed_relations:[],review_history:[],fact_reviews:[]};
const context=vm.createContext({console,AbortController,setTimeout,clearTimeout,setInterval(){},
 document:{hidden:false,getElementById:element,querySelectorAll:()=>[],createElement:()=>element('created')},
 fetch:async()=>({ok:true,json:async()=>data})});
async function main(){vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);await new Promise(r=>setImmediate(r));
 const cards=element('fact-list').innerHTML;
 assert.ok(cards.includes(`datetime="${sourceTime}"`),'Card lacks original source timestamp');
 assert.ok(cards.includes('来源记录时间')&&cards.includes('当前有效性待核对'));
 assert.ok(element('goal-note').textContent.includes('来源记录时间'),'Goal hides source time');
 vm.runInContext("openFact('old')",context);
 assert.ok(element('drawer-body').innerHTML.includes(sourceTime)&&element('drawer-body').innerHTML.includes('session-Alice'));
 vm.runInContext("state.data.personal.Alice.facts[1].session_id='<img src=x>';openFact('old')",context);
 assert.ok(!element('drawer-body').innerHTML.includes('<img')&&element('drawer-body').innerHTML.includes('&lt;img'));
 vm.runInContext("state.lens='team';render()",context);
 assert.ok(element('conflict-list').innerHTML.includes(sourceTime),'Conflict side hides source time');
 const docCard=vm.runInContext("factCard({...state.data.team.facts[1],source_kind:'file_snapshot'},[])",context);
 assert.ok(docCard.includes('材料采集时间')&&!docCard.includes('来源记录时间'),'Snapshot time mislabeled');
 const legacy=vm.runInContext("factCard({...state.data.team.facts[1],observed_at:null,session_id:null},[])",context);
 assert.ok(legacy.includes('时间未提供'),'Legacy date was invented');
 const escaped=vm.runInContext("factCard({...state.data.team.facts[1],observed_at:'<img src=x onerror=alert(1)>'},[])",context);
 assert.ok(!escaped.includes('<img')&&escaped.includes('时间未提供'));
 const answer={goal:'Build',conclusion:'Recorded claims',active_tasks:[],evidence_ids:[],conflicts:[],findings:[],
  next_action:old.value,next_action_status:'proposed',acceptance_condition:'Check current artifact',evidence_gaps:[],
  next_action_basis:{...old,quote:old.value,member_id:'Alice'}};
 context.answerFixture=answer;vm.runInContext('renderInvestigation(answerFixture)',context);
 let html=element('investigation-answer').innerHTML;
 assert.ok(html.includes('NEXT ACTION CANDIDATE')&&html.includes(sourceTime)&&html.includes('source-old'));
 assert.ok(html.includes('当前版本需核对'));
 answer.next_action_status='accepted';answer.next_action_basis={...answer.next_action_basis,status:'accepted',
  source_kind:'file_snapshot',reviewed_at:reviewTime,reviewed_by:'owner <script>'};
 vm.runInContext('renderInvestigation(answerFixture)',context);html=element('investigation-answer').innerHTML;
 assert.ok(html.includes('CONFIRMED NEXT ACTION')&&html.includes('材料采集时间')&&html.includes(reviewTime));
 assert.ok(html.includes('owner &lt;script&gt;')&&!html.includes('owner <script>'));
 answer.next_action_basis=null;answer.next_action_status=null;
 answer.conflicts=[{subject_key:'install',sides:[old]}];
 vm.runInContext('renderInvestigation(answerFixture)',context);
 assert.ok(element('investigation-answer').innerHTML.includes(sourceTime),'Answer conflict side hides source time');
 answer.conflicts=[];
 vm.runInContext('renderInvestigation(answerFixture)',context);
 assert.ok(!element('investigation-answer').innerHTML.includes('datetime='),'Generic action acquired a fabricated date');
 console.log('Source dates/kinds, conflict history, action basis, legacy missing dates and escaping verified.');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
