let currentCid=null;
let conversations=[];
const $=id=>document.getElementById(id);

function esc(s){return String(s??"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m]));}
function fmt(t){try{return new Date(t).toLocaleString("fa-IR")}catch{return t||""}}

async function api(url,opt={}){let r=await fetch(url,opt);if(!r.ok){let x=await r.text();throw new Error(x)}return r.json()}

async function loadConversations(){
  conversations=await api("/api/conversations");
  $("conversationList").innerHTML=conversations.map(c=>`
    <div class="conv ${c.id===currentCid?"active":""}" onclick="openConversation(${c.id})">
      ${esc(c.title)}<small>${c.message_count} پیام · ${fmt(c.updated_at)}</small>
    </div>`).join("");
  $("history").innerHTML=conversations.map(c=>`
    <div class="history-card"><b>${esc(c.title)}</b><div class="muted">${c.message_count} پیام · ${fmt(c.created_at)}</div>
    <div class="row" style="margin-top:8px"><button onclick="openConversation(${c.id})">ادامه گفتگو</button>
    <button onclick="deleteConversation(${c.id})">حذف</button></div></div>`).join("");
}

async function newChat(){
  const r=await api("/api/conversations",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({title:"گفتگوی جدید"})});
  await loadConversations(); await openConversation(r.id); showTab("chat");
}
async function deleteConversation(id){
  if(!confirm("این گفتگو و داده‌های مرتبط حذف شود؟"))return;
  await api("/api/conversations/"+id,{method:"DELETE"});
  if(currentCid===id)currentCid=null;
  await loadConversations(); if(conversations[0]) openConversation(conversations[0].id);
}
async function openConversation(id){
  currentCid=id; const r=await api("/api/conversations/"+id);
  $("chatTitle").textContent=r.conversation.title;
  $("liveToggle").checked=!!r.conversation.live_mode;
  $("thoughtToggle").checked=!!r.conversation.thought_network;
  $("messages").innerHTML=r.messages.map(renderMessage).join("");
  renderThoughts(r.thoughts||[]);
  await loadConversations(); $("messages").scrollTop=$("messages").scrollHeight;
}
function renderMessage(m){return `<div class="msg ${m.role}"><div>${esc(m.content)}</div><div class="msg-meta">${fmt(m.sent_at||m.received_at||m.created_at)}${m.metadata_json?.includes("autonomous")?" · خودکار":""}</div></div>`}
function renderThoughts(arr){
  $("thoughts").innerHTML=arr.filter(x=>x.status==="pending").map(t=>`
    <div class="thought"><b>💡 ${esc(t.title)}</b><div>${esc(t.content)}</div>
    <button onclick="continueThought(${t.id})">ادامه بحث</button>
    <button onclick="ignoreThought(${t.id})">نادیده گرفتن</button></div>`).join("");
}
async function continueThought(id){
  const r=await api("/api/thoughts/"+id,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"continue"})});
  $("messageInput").value=r.thought.content; $("messageInput").focus();
}
async function ignoreThought(id){await api("/api/thoughts/"+id,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"ignore"})});await openConversation(currentCid)}

$("newChat").onclick=newChat;
$("chatForm").onsubmit=async e=>{
  e.preventDefault(); if(!currentCid)return;
  const text=$("messageInput").value.trim(); if(!text && !$("fileInput").files.length)return;
  const fd=new FormData();fd.append("message",text);
  for(const f of $("fileInput").files)fd.append("files",f);
  $("status").textContent="در حال تحلیل و تولید پاسخ...";
  try{
    const r=await fetch(`/api/chat/${currentCid}`,{method:"POST",body:fd});
    if(!r.ok)throw new Error(await r.text());
    const data=await r.json();
    $("messageInput").value=""; $("fileInput").value=""; $("fileChips").innerHTML="";
    await openConversation(currentCid);
    $("status").textContent="آماده";
  }catch(err){$("status").textContent="خطا: "+err.message}
};

$("fileInput").onchange=e=>{$("fileChips").innerHTML=[...e.target.files].map(f=>`<span class="chip">${esc(f.name)}</span>`).join("")};

$("liveToggle").onchange=async e=>{if(currentCid)await api(`/api/conversations/${currentCid}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({live_mode:e.target.checked})})};
$("thoughtToggle").onchange=async e=>{if(currentCid)await api(`/api/conversations/${currentCid}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({thought_network:e.target.checked})})};

document.querySelectorAll(".tabs button").forEach(b=>b.onclick=()=>showTab(b.dataset.tab));
function showTab(name){
 document.querySelectorAll(".tabs button").forEach(b=>b.classList.toggle("active",b.dataset.tab===name));
 document.querySelectorAll(".tab").forEach(x=>x.classList.toggle("active",x.id==="tab-"+name));
 if(name==="models")loadSettings(); if(name==="taxonomy")loadTaxonomy(); if(name==="monitor")loadLogs(); if(name==="history")loadConversations();
}

async function loadSettings(){
 const r=await api("/api/settings");
 $("models").innerHTML=r.models.map(m=>`<div class="model-card">
 <b>${esc(m.name)} · ${esc(m.role)}</b><small>${esc(m.model_name)} · ${esc(m.base_url)}</small>
 <div class="row" style="margin-top:7px"><button onclick='editModel(${JSON.stringify(m)})'>ویرایش</button><button onclick="testModel(${m.id})">تست اتصال</button><button onclick="deleteModel(${m.id})">حذف</button></div></div>`).join("");
 document.querySelectorAll("[data-setting]").forEach(i=>i.value=r.settings[i.dataset.setting]||"");
}
window.editModel=m=>{
 $("modelId").value=m.id;$("modelNameDisplay").value=m.name;$("modelRole").value=m.role;$("baseUrl").value=m.base_url;
 $("apiKey").value=m.api_key;$("modelName").value=m.model_name;$("temperature").value=m.temperature;$("maxTokens").value=m.max_tokens;$("timeout").value=m.timeout_seconds;
 $("extraHeaders").value=m.extra_headers_json||"{}";$("systemPrompt").value=m.system_prompt||"";$("enabled").checked=!!m.enabled;
};
$("clearModel").onclick=()=>{$("modelForm").reset();$("modelId").value=""};
$("modelForm").onsubmit=async e=>{
 e.preventDefault();
 let headers={};try{headers=JSON.parse($("extraHeaders").value||"{}")}catch{alert("Headers JSON نامعتبر است");return}
 const data={id:$("modelId").value?Number($("modelId").value):null,name:$("modelNameDisplay").value,role:$("modelRole").value,base_url:$("baseUrl").value,api_key:$("apiKey").value,model_name:$("modelName").value,temperature:Number($("temperature").value),max_tokens:Number($("maxTokens").value),timeout_seconds:Number($("timeout").value),extra_headers:headers,system_prompt:$("systemPrompt").value,enabled:$("enabled").checked};
 await api("/api/models",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});alert("ذخیره شد");loadSettings();
};
window.testModel=async id=>{try{let r=await api("/api/models/test/"+id,{method:"POST"});alert("پاسخ تست: "+r.response)}catch(e){alert("خطا: "+e.message)}};
window.deleteModel=async id=>{if(confirm("حذف مدل؟")){await api("/api/models/"+id,{method:"DELETE"});loadSettings()}};
document.querySelectorAll("[data-setting]").forEach(i=>i.onchange=async()=>{await api("/api/settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({key:i.dataset.setting,value:i.value})})});

async function loadTaxonomy(){
 const r=await api("/api/taxonomy");
 $("taxonomyStats").textContent=`${r.overview.length} گره`;
 const build=nodes=>nodes.map(n=>`<div class="node"><div class="node-head"><b>${esc(n.title)}</b><span class="muted">${n.items.length} item · ${n.children.length} زیرمجموعه</span></div>
 ${n.items.map(i=>`<div class="item">🏷️ ${esc(i.label)}: ${esc(i.content)} <span class="muted">${fmt(i.created_at)}</span></div>`).join("")}
 ${n.children.length?`<div class="children">${build(n.children)}</div>`:""}</div>`).join("");
 $("taxonomyTree").innerHTML=build(r.tree);
}
$("exportJson").onclick=()=>window.open("/api/taxonomy/export","_blank");

async function loadLogs(){
 const rows=await api("/api/logs"+(currentCid?`?cid=${currentCid}`:""));
 $("logs").textContent=rows.reverse().map(x=>`[${x.created_at}] ${x.model_role}/${x.stage}/${x.status} ${x.duration_ms||0}ms\n${x.response_text||x.error_text}\n---`).join("\n");
}
$("refreshLogs").onclick=loadLogs;

const es=new EventSource("/api/events");
es.addEventListener("log",e=>{const x=JSON.parse(e.data);$("status").textContent=x.stage||"رویداد جدید"; if(document.getElementById("tab-monitor").classList.contains("active"))loadLogs()});
es.addEventListener("assistant",async e=>{const x=JSON.parse(e.data);if(x.conversation_id===currentCid){await openConversation(currentCid);$("status").textContent=x.autonomous?"پاسخ خودکار جدید":"آماده"}});
es.addEventListener("thought",async e=>{const x=JSON.parse(e.data);if(x.conversation_id===currentCid){await openConversation(currentCid);showTab("chat")}});
es.addEventListener("error",e=>{try{const x=JSON.parse(e.data);$("status").textContent="خطا: "+x.error}catch{}});

let recognition=null;
$("micBtn").onclick=()=>{
 const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
 if(!SR){alert("Web Speech API در این مرورگر در دسترس نیست.");return}
 if(recognition){recognition.stop();recognition=null;$("micBtn").textContent="🎙️";return}
 recognition=new SR();recognition.lang="fa-IR";recognition.continuous=true;recognition.interimResults=true;
 recognition.onresult=e=>{
   let finalText="";for(let i=e.resultIndex;i<e.results.length;i++){if(e.results[i].isFinal)finalText+=e.results[i][0].transcript}
   if(finalText){$("messageInput").value+=($("messageInput").value?" ":"")+finalText}
 };
 recognition.onend=()=>{recognition=null;$("micBtn").textContent="🎙️"};
 recognition.start();$("micBtn").textContent="⏹️";
};

(async function init(){
 await loadConversations();
 if(conversations.length) await openConversation(conversations[0].id); else await newChat();
})();
