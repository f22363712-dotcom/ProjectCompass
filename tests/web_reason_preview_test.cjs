const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map();function el(id){if(!nodes.has(id))nodes.set(id,{innerHTML:'',textContent:'',value:'',hidden:false,
classList:{toggle(){},add(){},remove(){}},addEventListener(){},setAttribute(){},append(){},appendChild(){},prepend(){},querySelector(){return el(id+'-child');}});return nodes.get(id);}
const context=vm.createContext({console,AbortController,setTimeout,clearTimeout,setInterval(){},document:{hidden:false,getElementById:el,querySelectorAll:()=>[],createElement:()=>el('created')},fetch:async()=>({ok:true,json:async()=>({project:{},personal:{},team:{facts:[]},evidence:[]})})});
(async()=>{vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);await new Promise(r=>setImmediate(r));
assert.equal(vm.runInContext('typeof reasonPreviewHTML',context),'function','Reason preview renderer missing');
context.preview={enabled:true,preview_only:true,unavailable_targets:0,candidates:[{id:'r',target_fact_id:'f',target_quote:'选择 <script>local</script>',member_id:'Alice',status:'accepted',relation_type_suggested:'hypothesized_reason',summary:'可能 <img src=x onerror=bad()>',assumptions:['前提 <b>待验</b>'],source_quotes:[{source_id:'s',quote:'原话 <script>data</script>',member_id:'Bob',reported_by:'codex',claim_type:'assistant_report',observed_at:'2026-10-20T00:00:00Z',source_kind:'assistant_message'}],counter_evidence:[],scope_source_ids:['s']}],gaps:[{target_fact_id:'f',target_quote:'本地',member_id:'Alice',text:'原因尚待确认',scope_source_ids:['s']}]};
const html=vm.runInContext('reasonPreviewHTML(preview)',context);
assert.ok(html.includes('推断假说')&&html.includes('待核对')&&html.includes('前提'));
assert.ok(html.includes('data-evidence-id="s"')&&html.includes('data-fact-id="f"'));
assert.ok(html.includes('&lt;script&gt;')&&!html.includes('<script>')&&!html.includes('<img src='));
assert.ok(!html.includes('data-action=')&&!html.includes('已确认原因'));
vm.runInContext('state.data={reason_preview:preview};state.lens="personal";state.member="Bob";renderReasonPreview()',context);
assert.ok(!el('reason-preview-list').innerHTML.includes('可能'));
vm.runInContext('state.member="Alice";renderReasonPreview()',context);
assert.ok(el('reason-preview-list').innerHTML.includes('可能'));
console.log('Reason candidates escape source/model text, preserve assumptions/gaps and member scope without owner actions.');
})().catch(e=>{console.error(e);process.exitCode=1;});
