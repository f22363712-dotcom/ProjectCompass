const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map();
function element(id){if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',value:'',hidden:true,
 scrollLeft:0,scrollWidth:1500,clientWidth:500,listeners:{},
 classList:{toggle(){},add(){},remove(){}},addEventListener(name,fn){this.listeners[name]=fn;},
 setAttribute(){},append(){},appendChild(){},prepend(){},querySelector(selector){this.lastSelector=selector;
 const index=/data-trace-index="(\d+)"/.exec(selector);return index ? {offsetLeft:Number(index[1])*300} : null;}});return elements.get(id);}
const a={id:'a',subject_key:'task:build',kind:'task',value:'Build <script>',author:'Alice',status:'proposed',
 evidence_ids:['ea'],observed_at:'2026-10-20T00:00:00Z'};
const b={...a,id:'b',value:'Build complete',author:'Bob',evidence_ids:['eb'],observed_at:'2026-10-21T00:00:00Z'};
const c={...a,id:'c',subject_key:'decision:scope',kind:'decision',value:'Keep original scope',evidence_ids:['ec'],observed_at:'2026-10-19T00:00:00Z'};
const gap={id:'gap',kind:'assumption',value:'No source supplied',evidence_ids:['missing']};
const evidence=[{id:'ea',author:'Alice',content_preview:'Alice source',observed_at:a.observed_at},
 {id:'eb',author:'Bob',content_preview:'PRIVATE BOB SOURCE',observed_at:b.observed_at},
 {id:'ec',author:'Alice',kind:'file_snapshot',content_preview:'Scope file',observed_at:c.observed_at}];
const reviews=[{fact_id:'a',action:'accept_fact',reviewer:'Owner <img>',observed_at:'2026-10-22T00:00:00Z'},
 {fact_id:'b',action:'accept_fact',reviewer:'Owner',observed_at:'2026-10-23T00:00:00Z'}];
const relations=[{fact_id:'a',evidence_id:'ea',status:'candidate'},
 {fact_id:'c',evidence_id:'ec',status:'confirmed'},{fact_id:'a',evidence_id:'eb',status:'candidate'}];
const data={project:{name:'Synthetic visual trace',evidence_count:3,fact_count:4},
 personal:{Alice:{facts:[a,c,gap],evidence_ids:['ea','ec']},Bob:{facts:[b],evidence_ids:['eb']}},
 team:{facts:[{...a,status:'conflicted'},{...b,status:'accepted'},c,gap],evidence_ids:['ea','eb','ec'],conflicts:[]},
 evidence,candidate_relations:relations.filter(x=>x.status==='candidate'),confirmed_relations:relations.filter(x=>x.status==='confirmed'),
 fact_reviews:reviews,review_history:reviews};
const context=vm.createContext({console,AbortController,setTimeout,clearTimeout,setInterval(){},
 document:{hidden:false,getElementById:element,querySelectorAll:()=>[],createElement:()=>element('created')},
 fetch:async()=>({ok:true,json:async()=>data})});
const run=(s)=>vm.runInContext(s,context);
async function main(){run(fs.readFileSync(process.argv[2],'utf8'));await new Promise(r=>setImmediate(r));
 assert.equal(run('typeof buildTraceGroups'),'function','Approved horizontal trace is missing');
 const groups=run('buildTraceGroups(activeView().facts,[...state.data.candidate_relations,...state.data.confirmed_relations],state.data.evidence.filter(x=>x.id!=="eb"),state.data.fact_reviews,state.data.team.facts)');
 assert.equal(groups.length,3);assert.equal(groups[0].subject,'decision:scope');assert.equal(groups[2].subject,'gap');
 let html=element('trace-track').innerHTML;
 assert.ok(html.includes('data-fact-id="a"')&&html.includes('data-evidence-id="ea"'));
 assert.ok(html.includes('data-fact-id="c"')&&!html.includes('data-fact-id="b"'));
 assert.ok(!html.includes('PRIVATE BOB SOURCE')&&!html.includes('data-evidence-id="eb"'));
 assert.ok(html.includes('trace-wire candidate')&&html.includes('trace-wire confirmed'));
 assert.ok(html.includes('历史确认')&&!html.includes('当前团队确认'));
 assert.ok(html.includes('来源记录时间未提供')&&html.includes('缺失来源')&&html.includes('材料采集时间'));
 assert.ok(html.includes('Build &lt;script&gt;')&&!html.includes('Build <script>'));
 assert.ok(html.includes('Owner &lt;img&gt;')&&!html.includes('Owner <img>'));
 assert.equal(run('state.data.personal.Alice.facts[0].status'),'proposed','Rendering changed personal truth');
 element('trace-scroller').scrollLeft=213;run('render()');assert.equal(element('trace-scroller').scrollLeft,213);
 run('state.lens="team";render()');html=element('trace-track').innerHTML;
 assert.ok(html.includes('data-fact-id="a"')&&html.includes('data-fact-id="b"')&&html.includes('当前团队确认'));
 assert.equal(run('buildTraceGroups(state.data.team.facts,[],[],[],[]).find(x=>x.subject==="task:build").facts.length'),2);
 element('trace-scroller').scrollLeft=717;run('state.lens="personal";render()');assert.equal(element('trace-scroller').scrollLeft,213);
 element('trace-scroller').scrollLeft=0;run('render()');assert.equal(element('trace-scroller').scrollLeft,0,'Manual start was lost');
 run('state.member="Bob";render()');assert.ok(!element('trace-track').innerHTML.includes('data-fact-id="a"'));
 run('state.lens="team";render()');assert.equal(element('trace-scroller').scrollLeft,717);
 run('jumpTrace(false)');assert.equal(element('trace-scroller').scrollLeft,0);
 run('jumpTrace(true)');assert.equal(element('trace-scroller').scrollLeft,300,'Latest landed on the undated last group');
 // A newer branch can belong to an old subject in the first column.
 run('state.data.team.facts.push({...state.data.team.facts[2],id:"recent-branch",value:"Latest scope decision",observed_at:"2026-10-26T00:00:00Z"});render();jumpTrace(true)');
 assert.equal(element('trace-scroller').scrollLeft,0,'Latest missed a recent branch of the earliest subject');
 assert.equal(element('trace-track').lastSelector,'[data-trace-index="0"]');
 run('state.lens="personal";state.member="Alice";state.data.team.facts[1].source_state={status:"superseded"};state.data.fact_reviews.push({action:"accept_fact",fact_id:"c",observed_at:"2026-10-24"});render()');
 assert.ok(!element('trace-track').innerHTML.includes('当前团队确认'),'Stale or unaccepted fact showed current team review');
 run('state.data.personal.Alice.facts=[];render()');assert.ok(element('trace-track').innerHTML.includes('尚无结构化记录'));
 assert.ok(!element('trace-track').innerHTML.includes('data-fact-id='));
 run('state.data.personal.Alice.facts=[{id:"undated",kind:"task",value:"No date"}];render();jumpTrace(true)');
 assert.equal(element('trace-scroller').scrollLeft,0,'Unknown date invented a latest point');
 console.log('Scoped horizontal trace, exact subject branches, independent relation/review status, dates, escaping and per-view scroll verified.');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
