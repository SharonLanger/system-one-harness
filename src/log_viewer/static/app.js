'use strict';
const FIELDS=['session_id','correlation_id','flow_name','component','component_type','level','event','hook_event'];
const NULLABLE=FIELDS.filter(x=>!['level','event'].includes(x));
const LABELS={session_id:'Session ID',correlation_id:'Correlation ID',flow_name:'Flow name',component:'Component',component_type:'Component type',level:'Levels (All when blank)',event:'Event',hook_event:'Hook event'};
function defaultDraft(){return {timeMode:'relative',relativeSeconds:3600,start:'',end:'',fieldValues:{},nullFields:[],textValue:'',includeDetails:false,pageSize:200};}
function utcControlValue(value,key){
  const match=/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(\.\d{1,6})?)?(Z|[+-]\d{2}:\d{2})?$/.exec(value);
  if(!match)throw Error('Enter a valid UTC '+key+' date with at most six fractional digits.');
  const [,y,m,d,h,minute,second,fraction,zone]=match;
  const check=new Date(Date.UTC(Number(y),Number(m)-1,Number(d),Number(h),Number(minute),Number(second||0)));
  if(check.getUTCFullYear()!==Number(y)||check.getUTCMonth()!==Number(m)-1||check.getUTCDate()!==Number(d)||check.getUTCHours()!==Number(h)||check.getUTCMinutes()!==Number(minute)||check.getUTCSeconds()!==Number(second||0))throw Error('Enter a valid UTC '+key+' date.');
  const aware=y+'-'+m+'-'+d+'T'+h+':'+minute+':'+(second||'00')+(fraction||'')+(zone||'Z');
  if(!Number.isFinite(Date.parse(aware)))throw Error('Enter a valid UTC '+key+' date.');
  return aware;
}
function buildRequest(draft){
  const time={mode:draft.timeMode};
  if(time.mode==='relative')time.seconds=Number(draft.relativeSeconds);
  else if(time.mode==='absolute'){
    for(const key of ['start','end'])if(draft[key]){
      time[key]=utcControlValue(draft[key],key);
    }
    if(!time.start&&!time.end)throw Error('Enter at least one UTC bound.');
  }
  const request={query_version:1,time,page_size:Number(draft.pageSize),order:'file_append_desc'};
  const filters={};
  for(const field of FIELDS){
    const values=(draft.fieldValues[field]||[]).filter(v=>v!=='');
    if(values.some(v=>!v.trim()))throw Error('Categorical values must contain visible text.');
    const include_null=(draft.nullFields||[]).includes(field);
    if(values.length||include_null)filters[field]={values,include_null};
  }
  if(Object.keys(filters).length)request.filters=filters;
  if(draft.textValue!=='')request.text={value:draft.textValue,scope:draft.includeDetails?'message_and_details':'message'};
  return request;
}
class LogViewerController{
  constructor(token){
    this.token=token;this.draft=defaultDraft();this.submitted=null;this.queryId=null;this.generation=0;this.cursor=null;this.partialPage=null;this.pages=[];this.selectedRecord=null;this.status='idle';this.busy=false;this.submitting=false;this.previousDraft=null;this.evicted=false;this.selectionGeneration=0;this.diagnosticGeneration=0;this.limits={client_pages:5,handles:1000};
    this.el=id=>document.getElementById(id);
    for(const field of FIELDS){
      const wrapper=document.createElement('div');const label=document.createElement('label');label.textContent=LABELS[field];
      const input=document.createElement('textarea');input.id=field;input.rows=1;input.setAttribute('aria-label',LABELS[field]);label.append(input);wrapper.append(label);
      if(NULLABLE.includes(field)){const l=document.createElement('label');l.className='check';const c=document.createElement('input');c.type='checkbox';c.id='null-'+field;l.append(c,document.createTextNode('Include null'));wrapper.append(l);}
      this.el('fieldControls').append(wrapper);
    }
    this.writeDraft();
    this.el('rows').addEventListener('keydown',e=>{const buttons=Array.from(this.el('rows').querySelectorAll('button'));const index=buttons.indexOf(document.activeElement);const target=e.key==='ArrowDown'?index+1:e.key==='ArrowUp'?index-1:e.key==='Home'?0:e.key==='End'?buttons.length-1:null;if(target!==null&&buttons[target]){e.preventDefault();buttons[target].focus();}});
    this.el('filters').addEventListener('input',()=>this.changed());this.el('filters').addEventListener('change',()=>this.changed());
    this.el('filters').addEventListener('submit',e=>{e.preventDefault();if(!this.submitting&&(!this.busy||this.isDirty()))this.run();});
    this.el('clear').onclick=()=>{this.draft=defaultDraft();this.writeDraft();this.changed();};
    this.el('back').onclick=()=>{if(this.previousDraft){this.draft=this.previousDraft;this.previousDraft=null;this.writeDraft();this.changed();this.el('back').hidden=true;}};
    this.el('refresh').onclick=()=>{if(!this.isDirty())this.run();};
    this.el('cancel').onclick=()=>this.cancel();this.el('more').onclick=()=>{if(!this.busy&&this.cursor)this.advance();};
    this.el('sessionHistory').onclick=()=>this.shortcut('session_id');this.el('correlationHistory').onclick=()=>this.shortcut('correlation_id');
    this.el('copy').onclick=async()=>{try{await navigator.clipboard.writeText(JSON.stringify(this.selectedRecord.original,null,2));this.el('inspectionStatus').textContent='JSON copied.';}catch{this.el('inspectionStatus').textContent='Copy unavailable; select the JSON text below.';}};
    this.el('jsonDetails').addEventListener('toggle',()=>{if(this.el('jsonDetails').open&&this.selectedRecord)this.el('json').textContent=JSON.stringify(this.selectedRecord.original,null,2);});
    if(!token)this.disconnected('Launch or relaunch logs --ui and open its authorized URL.');
    else this.configReady=this.api('/api/config').then(c=>{this.limits=c.limits;}).catch(e=>this.disconnected(e.message));
  }
  readDraft(){
    const d=defaultDraft();for(const key of ['timeMode','start','end','textValue'])d[key]=this.el(key).value;
    d.relativeSeconds=Number(this.el('relativeSeconds').value);d.pageSize=Number(this.el('pageSize').value);d.includeDetails=this.el('includeDetails').checked;
    for(const field of FIELDS){const value=this.el(field).value;if(value!=='')d.fieldValues[field]=value.split('\n');if(NULLABLE.includes(field)&&this.el('null-'+field).checked)d.nullFields.push(field);}
    return d;
  }
  writeDraft(){
    const d=this.draft;for(const key of ['timeMode','relativeSeconds','start','end','textValue','pageSize'])this.el(key).value=['start','end'].includes(key)?d[key].replace(/Z$/,''):d[key];
    this.el('includeDetails').checked=d.includeDetails;
    for(const field of FIELDS){this.el(field).value=(d.fieldValues[field]||[]).join('\n');if(NULLABLE.includes(field))this.el('null-'+field).checked=d.nullFields.includes(field);}
  }
  isDirty(){try{return this.submitted!==null&&JSON.stringify(buildRequest(this.draft))!==JSON.stringify(this.submitted);}catch{return true;}}
  changed(){this.draft=this.readDraft();const dirty=this.isDirty();this.el('dirty').textContent=dirty?'Unsent changes · results use submitted parameters':this.submitted?'Submitted parameters':'Ready to search';this.el('refresh').disabled=!this.submitted||dirty||this.busy;}
  async api(path,method='GET',body){
    const options={method,headers:{Authorization:'Bearer '+this.token},cache:'no-store'};
    if(body!==undefined){options.headers['Content-Type']='application/json';options.body=JSON.stringify(body);}
    let response;try{response=await fetch(path,options);}catch{const error=Error('Disconnected. Relaunch logs --ui to reconnect.');error.transport=true;throw error;}
    let data;try{data=await response.json();}catch{const error=Error('The local response was interrupted.');error.transport=true;throw error;}
    if(!response.ok)throw Error(data.error?.code||'Local request failed');return data;
  }
  disconnected(message){this.status='disconnected';this.el('status').textContent=message;this.el('error').textContent=message;this.busy=false;this.el('cancel').disabled=true;this.el('more').disabled=true;this.el('refresh').disabled=true;}
  async release(id){if(!id)return;try{await this.api('/api/searches/'+id+'/cancel','POST',{});await this.api('/api/searches/'+id,'DELETE');}catch{/* Superseded/expired state is already unusable. */}}
  async run(){
    let request;try{this.draft=this.readDraft();request=buildRequest(this.draft);}catch(e){this.el('error').textContent=e.message;return;}
    const gen=++this.generation;const old=this.queryId;this.queryId=null;this.cursor=null;this.partialPage=null;this.busy=true;this.submitting=true;this.el('error').textContent='';this.el('status').textContent='Creating search…';
    this.pages=[];this.evicted=false;this.selectedRecord=null;this.selectionGeneration++;this.el('json').textContent='';this.el('inspectFields').replaceChildren();this.el('inspectionStatus').textContent='Select an event to inspect its full message and original JSON.';this.el('copy').disabled=true;this.el('sessionHistory').disabled=true;this.el('correlationHistory').disabled=true;this.renderRows();
    this.el('results').hidden=false;this.el('inspector').hidden=false;this.submitted=structuredClone(request);this.changed();this.release(old);
    try{
      if(this.configReady)await this.configReady;
      const created=await this.api('/api/searches','POST',request);
      if(gen!==this.generation){this.release(created.query_id);return;}
      this.queryId=created.query_id;this.cursor=created.cursor;this.submitting=false;this.el('scope').textContent='Submitted query: '+JSON.stringify(created.query);this.el('cancel').disabled=false;
      await this.advance(gen);
    }catch(e){if(gen===this.generation){this.submitting=false;this.disconnected(e.message);}}
  }
  async advance(gen=this.generation){
    if(!this.queryId||!this.cursor)return;
    const id=this.queryId;
    let pending=this.partialPage;
    if(!pending){
      const cap=Math.min(1000,this.limits.handles);const size=this.submitted.page_size;
      while(this.pages.length&&(this.pages.length>=this.limits.client_pages||this.pages.flat().length+size>cap)){this.pages.shift();this.evicted=true;}
      pending={queryId:id,generation:gen,cursor:this.cursor,matches:[]};this.partialPage=pending;
      this.renderRows();
    }
    if(pending.queryId!==id||pending.generation!==gen)return;
    this.busy=true;this.el('more').disabled=true;this.el('error').textContent='';this.changed();
    try{
      while(pending.cursor){
        // Do not move this cursor until its response is received and accepted.
        // If the server committed but delivery failed, the same cursor retries
        // its cached response without consuming another page allowance.
        const result=await this.api('/api/searches/'+id+'/advance','POST',{cursor:pending.cursor});
        if(gen!==this.generation||id!==this.queryId||this.partialPage!==pending)return;
        pending.matches.push(...result.matches);pending.cursor=result.cursor;
        this.cursor=result.cursor;this.status=result.state;
        const p=result.progress;
        const phase=!result.inventory_complete?'Discovering files':result.state==='complete'?'Complete':'Scanning';
        const warning=result.coverage==='partial'&&result.state==='complete';
        this.el('status').textContent=phase+(warning?' with warnings':'')+' · '+p.files_discovered+' files · '+p.bytes_scanned+' bytes · '+p.matches_seen+' matches'+(result.cursor?' found so far; more history remains':'');
        if(result.state==='complete'&&!p.matches_seen)this.el('status').textContent=result.empty_root?'Log root does not exist. No files were created.':p.skipped_count?'No readable matches; some data skipped.':!p.files_discovered?'Empty scope: no hourly log files.':'No matches in the submitted scope.';
        if(result.state==='failed')this.el('status').textContent='Search incomplete: '+result.error.code;
        if(result.state==='canceled')this.el('status').textContent='Canceled · incomplete results.';
        this.renderDiagnostics(result);
        if(result.state!=='budget_yield')break;
      }
      if(gen!==this.generation||id!==this.queryId)return;
      const page=pending.matches;this.partialPage=null;
      if(page.length){this.pages.push(page);while(this.pages.length>this.limits.client_pages||this.pages.flat().length>Math.min(1000,this.limits.handles)){this.pages.shift();this.evicted=true;}}
      this.renderRows();
      if(document.activeElement===document.body&&this.el('rows').querySelector('button'))this.el('rows').querySelector('button').focus({preventScroll:true});
    }catch(e){
      if(gen===this.generation&&id===this.queryId&&this.partialPage===pending){
        if(e.transport){
          this.status='interrupted';
          this.el('status').textContent='Search interrupted · '+pending.matches.length+' matches buffered. Retry current page to continue the same snapshot.';
        }else{
          this.cursor=null;this.partialPage=null;
          this.disconnected('Search incomplete: '+e.message+'. Run a new search to restart.');
        }
      }
    }finally{if(gen===this.generation&&id===this.queryId){this.busy=false;this.el('more').textContent=this.status==='interrupted'?'Retry current page':'Load more';this.el('more').disabled=!this.cursor;this.el('cancel').disabled=!this.cursor;this.changed();}}
  }
  async cancel(){const id=this.queryId;if(!id)return;this.generation++;this.cursor=null;this.partialPage=null;this.busy=false;this.el('status').textContent='Canceled · incomplete results.';this.el('cancel').disabled=true;this.el('more').disabled=true;this.changed();try{await this.api('/api/searches/'+id+'/cancel','POST',{});}catch(e){this.disconnected(e.message);}}
  renderRows(){
    const body=this.el('rows');const wrap=body.closest('.tablewrap');const scroll=wrap.scrollTop;const focused=document.activeElement.closest('tr')?.dataset.ref;body.replaceChildren();
    this.el('evicted').textContent=this.evicted?'Older pages were evicted. Run a new search to inspect that history.':'';
    for(const row of this.pages.flat()){
      const tr=document.createElement('tr');tr.dataset.ref=row.ref;tr.setAttribute('aria-selected',String(this.selectedRecord?.ref===row.ref));
      const a=document.createElement('td');a.textContent=row.timestamp;const badge=document.createElement('span');badge.className='badge';badge.textContent=row.level;a.append(badge);
      const b=document.createElement('td');b.textContent=(row.component??'null')+(row.fields_truncated.includes('component')?'… [preview]':'')+' / '+row.event+(row.fields_truncated.includes('event')?'… [preview]':'');
      const c=document.createElement('td');const select=document.createElement('button');select.className='secondary';select.textContent=(row.message||'(empty message)')+(row.message_truncated?'… [preview]':'');select.onclick=()=>this.inspect(row);c.append(select);
      const preview=key=>(row[key]??'null')+(row.fields_truncated.includes(key)?'… [preview]':'');
      const d=document.createElement('td');d.textContent='Session: '+preview('session_id')+'\nCorrelation: '+preview('correlation_id');tr.append(a,b,c,d);body.append(tr);
      if(focused===row.ref)select.focus({preventScroll:true});
    }
    wrap.scrollTop=scroll;
  }
  async inspect(row){
    const id=this.queryId,gen=this.generation,selection=++this.selectionGeneration;
    try{const detail=await this.api('/api/searches/'+id+'/records/'+row.ref);if(id!==this.queryId||gen!==this.generation||selection!==this.selectionGeneration)return;
      detail.fields={...detail.original,...detail.fields};this.selectedRecord={...detail,ref:row.ref};this.el('inspectionStatus').textContent=detail.source.path+' · byte '+detail.source.offset;
      const dl=document.createElement('dl');for(const [key,value]of Object.entries(detail.fields)){const dt=document.createElement('dt');dt.textContent=key;const dd=document.createElement('dd');if(key==='details'){const expand=document.createElement('details');const summary=document.createElement('summary');summary.textContent='Expand details';const pre=document.createElement('pre');expand.append(summary,pre);expand.addEventListener('toggle',()=>{if(expand.open)pre.textContent=JSON.stringify(value,null,2);});dd.append(expand);}else dd.textContent=typeof value==='object'?JSON.stringify(value):String(value);dl.append(dt,dd);}this.el('inspectFields').replaceChildren(dl);
      this.el('copy').disabled=false;this.el('sessionHistory').disabled=!detail.fields.session_id;this.el('correlationHistory').disabled=!detail.fields.correlation_id;
      this.el('json').textContent=this.el('jsonDetails').open?JSON.stringify(detail.original,null,2):'';
      for(const tr of this.el('rows').children)tr.setAttribute('aria-selected',String(tr.dataset.ref===row.ref));
    }catch(e){if(id===this.queryId&&gen===this.generation&&selection===this.selectionGeneration)this.el('inspectionStatus').textContent=e.message;}
  }
  shortcut(field){if(!this.selectedRecord?.fields[field])return;this.previousDraft=structuredClone(this.draft);this.draft=defaultDraft();this.draft.timeMode='all';this.draft.fieldValues[field]=[this.selectedRecord.fields[field]];this.writeDraft();this.changed();this.el('back').hidden=false;this.el(field).focus();}
  renderDiagnostics(result){
    const list=this.el('diagnosticList');list.replaceChildren();
    for(const entry of result.diagnostics){const p=document.createElement('p');p.textContent=entry.code+' · '+(entry.path||'scope')+' · byte '+(entry.offset??'—');if(entry.ref){const button=document.createElement('button');button.className='secondary';button.textContent='Inspect raw preview';const id=this.queryId,gen=this.generation;button.onclick=async()=>{const diagnostic=++this.diagnosticGeneration;try{const d=await this.api('/api/searches/'+id+'/diagnostics/'+entry.ref);if(gen===this.generation&&id===this.queryId&&diagnostic===this.diagnosticGeneration)this.el('rawDiagnostic').textContent='Unvalidated raw preview'+(d.truncated?' [truncated]':'')+'\n'+d.preview;}catch(e){if(gen===this.generation&&id===this.queryId&&diagnostic===this.diagnosticGeneration)this.el('rawDiagnostic').textContent=e.message;}};p.append(button);}list.append(p);}
    const counts=document.createElement('p');counts.textContent=JSON.stringify(result.diagnostic_counts);list.prepend(counts);
  }
}
if(typeof module!=='undefined')module.exports={buildRequest,defaultDraft,LogViewerController};
if(typeof document!=='undefined'){
  const fragment=new URLSearchParams(location.hash.slice(1));const token=fragment.get('token');history.replaceState(null,'',location.pathname);
  window.LogViewerController=LogViewerController;window.buildRequest=buildRequest;window.viewer=new LogViewerController(token);
}
