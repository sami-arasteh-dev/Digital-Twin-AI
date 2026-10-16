from __future__ import annotations
import asyncio, json, os, uuid
from pathlib import Path
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .ai import classify_and_store, analytical_turn
from .extract import extract_file, image_data_url

db.init_db()
app=FastAPI(title="Digital Twin AI")
BASE=Path(__file__).resolve().parent.parent
app.mount("/static",StaticFiles(directory=str(BASE/"static")),name="static")

subscribers=set()
jobs={}
job_lock=asyncio.Lock()

async def emit(event, payload):
    dead=[]
    data=f"event: {event}\ndata: {json.dumps(payload,ensure_ascii=False)}\n\n"
    for q in list(subscribers):
        try: q.put_nowait(data)
        except Exception: dead.append(q)
    for q in dead: subscribers.discard(q)

@app.get("/",response_class=HTMLResponse)
async def index():
    return FileResponse(BASE/"static/index.html")

class ModelIn(BaseModel):
    id:int|None=None
    name:str
    role:str
    base_url:str
    api_key:str
    model_name:str
    temperature:float=0.2
    max_tokens:int=4096
    timeout_seconds:int=180
    extra_headers:dict={}
    system_prompt:str=""
    enabled:bool=True

class SettingIn(BaseModel):
    key:str
    value:str

class ConversationIn(BaseModel):
    title:str="گفتگوی جدید"

class ToggleIn(BaseModel):
    live_mode:bool|None=None
    thought_network:bool|None=None

class ThoughtAction(BaseModel):
    action:str

@app.get("/api/settings")
async def settings():
    return {
        "settings":{
            "live_interval_seconds":db.get_setting("live_interval_seconds","60"),
            "thought_interval_seconds":db.get_setting("thought_interval_seconds","300"),
            "short_term_ttl_seconds":db.get_setting("short_term_ttl_seconds","86400"),
            "autonomous_timeout_seconds":db.get_setting("autonomous_timeout_seconds","120"),
        },
        "models":db.get_model_profiles()
    }

@app.post("/api/settings")
async def save_setting(x:SettingIn):
    allowed={"live_interval_seconds","thought_interval_seconds","short_term_ttl_seconds","autonomous_timeout_seconds"}
    if x.key not in allowed: raise HTTPException(400,"setting not allowed")
    db.set_setting(x.key,x.value)
    return {"ok":True}

@app.post("/api/models")
async def save_model(x:ModelIn):
    mid=db.upsert_model(x.model_dump())
    return {"ok":True,"id":mid}

@app.delete("/api/models/{mid}")
async def remove_model(mid:int):
    db.delete_model(mid)
    return {"ok":True}

@app.post("/api/models/test/{mid}")
async def test_model(mid:int):
    models=db.get_model_profiles()
    profile=next((x for x in models if x["id"]==mid),None)
    if not profile: raise HTTPException(404,"model not found")
    from .ai import chat
    text,_=await chat(profile,[{"role":"user","content":"Reply with exactly: OK"}],stage="connection_test")
    return {"ok":True,"response":text}

@app.get("/api/conversations")
async def conversations():
    return db.list_conversations()

@app.post("/api/conversations")
async def new_conversation(x:ConversationIn):
    cid=db.create_conversation(x.title)
    return {"id":cid}

@app.get("/api/conversations/{cid}")
async def conversation(cid:int):
    c=db.get_conversation(cid)
    if not c: raise HTTPException(404)
    return {"conversation":c,"messages":db.get_messages(cid),"thoughts":db.get_thoughts(cid),
            "notifications":db.get_unread_notifications(cid)}

@app.patch("/api/conversations/{cid}")
async def toggle(cid:int,x:ToggleIn):
    if x.live_mode is not None: db.update_conversation(cid,live_mode=int(x.live_mode))
    if x.thought_network is not None: db.update_conversation(cid,thought_network=int(x.thought_network))
    return db.get_conversation(cid)

@app.delete("/api/conversations/{cid}")
async def remove_conversation(cid:int):
    db.delete_conversation(cid)
    return {"ok":True}

@app.get("/api/logs")
async def logs(cid:int|None=None):
    return db.get_logs(cid)

@app.get("/api/taxonomy")
async def taxonomy():
    return {"tree":db.taxonomy_tree(),"overview":db.taxonomy_overview_rows()}

@app.get("/api/taxonomy/export")
async def taxonomy_export():
    return JSONResponse(db.taxonomy_tree(),headers={"Content-Disposition":"attachment; filename=taxonomy.json"})

@app.get("/api/thoughts")
async def thoughts(cid:int|None=None):
    db.expire_thoughts()
    return db.get_thoughts(cid)

@app.post("/api/thoughts/{tid}")
async def thought_action(tid:int,x:ThoughtAction):
    thoughts=db.get_thoughts()
    t=next((z for z in thoughts if z["id"]==tid),None)
    if not t: raise HTTPException(404)
    if x.action=="ignore":
        db.update_thought(tid,"ignored")
    elif x.action=="continue":
        db.update_thought(tid,"continued")
        # Returning the thought lets the frontend inject it as the next user prompt.
    else: raise HTTPException(400,"unknown action")
    return {"ok":True,"thought":t}

@app.post("/api/notifications/{nid}/read")
async def notification_read(nid:int):
    db.mark_notification_read(nid)
    return {"ok":True}

@app.post("/api/chat/{cid}")
async def chat_endpoint(cid:int, message:str=Form(...), files:list[UploadFile]=File(default=[])):
    conv=db.get_conversation(cid)
    if not conv: raise HTTPException(404,"conversation not found")
    sent=db.now_iso()
    msg_id=db.add_message(cid,"user",message,sent_at=sent,metadata={"attachment_count":len(files)})
    if not conv.get("twin_birth_at"):
        db.update_conversation(cid,twin_birth_at=sent)
    saved_attachments=[]
    attachment_text=[]
    upload_dir=db.UPLOADS/str(cid)
    upload_dir.mkdir(parents=True,exist_ok=True)
    for f in files:
        if not f.filename: continue
        safe=Path(f.filename).name
        dest=upload_dir/f"{uuid.uuid4().hex}_{safe}"
        data=await f.read()
        dest.write_bytes(data)
        mime=f.content_type or "application/octet-stream"
        extracted=extract_file(str(dest),mime)
        db.add_attachment(msg_id,safe,mime,str(dest),extracted)
        a={"original_name":safe,"mime_type":mime,"extracted_text":extracted}
        if mime.startswith("image/"):
            a["data_url"]=image_data_url(str(dest),mime)
        saved_attachments.append(a)
        attachment_text.append(f"[{safe}]\n{extracted}")
    await emit("log",{"type":"user_message","conversation_id":cid,"message_id":msg_id,"time":sent,"text":message})

    async def process():
        try:
            await emit("log",{"type":"stage","stage":"classification_started","message_id":msg_id})
            await classify_and_store(msg_id,message,"\n".join(attachment_text))
            await emit("log",{"type":"stage","stage":"classification_finished","message_id":msg_id})
            history=db.get_messages(cid)
            answer=await analytical_turn(cid,msg_id,message,history,saved_attachments)
            received=db.now_iso()
            aid=db.add_message(cid,"assistant",answer,received_at=received,
                               provider=(db.get_model_profile("analytical") or {}).get("base_url"),
                               model_name=(db.get_model_profile("analytical") or {}).get("model_name"),
                               reply_to_message_id=msg_id,
                               metadata={"label":"داده های تولید شده توسط مدل"})
            db.add_generated(cid,msg_id,aid,answer)
            await emit("assistant",{"conversation_id":cid,"message_id":aid,"reply_to":msg_id,"content":answer,"time":received})
            # Classify analytical output too.
            await classify_and_store(aid,answer,"")
            await emit("log",{"type":"stage","stage":"assistant_classification_finished","message_id":aid})
            return aid
        except Exception as e:
            await emit("error",{"conversation_id":cid,"message_id":msg_id,"error":str(e)})
            raise

    # Process inside request so the caller receives the completed answer; live UI uses fetch streaming behavior.
    aid=await process()
    return {"ok":True,"user_message_id":msg_id,"assistant_message_id":aid,
            "messages":db.get_messages(cid)[-2:]}

async def autonomous_for_conversation(cid, mode):
    conv=db.get_conversation(cid)
    if not conv: return
    messages=db.get_messages(cid)
    if not messages: return
    last=messages[-1]
    if mode=="live":
        # Ask analytical model whether it has an unsolicited follow-up.
        profile=db.get_model_profile("analytical")
        if not profile: return
        prompt=f"""
آخرین پیام کاربر:
{last['content']}
آخرین پاسخ مدل:
{messages[-2]['content'] if len(messages)>1 else ''}
زمان فعلی: {db.now_iso()}

اگر ادامه منطقی و مفید بدون پیام جدید کاربر وجود دارد، یک پاسخ کوتاه تولید کن.
اگر نیازی نیست، فقط NO_FOLLOWUP بنویس.
"""
        from .ai import chat
        text,_=await chat(profile,[{"role":"system","content":"حالت گفتگوی زنده فعال است."},{"role":"user","content":prompt}],
                          conversation_id=cid,stage="autonomous_live")
        if text.strip() and "NO_FOLLOWUP" not in text:
            aid=db.add_message(cid,"assistant",text,received_at=db.now_iso(),
                               provider=profile["base_url"],model_name=profile["model_name"],
                               metadata={"autonomous":True,"label":"داده های تولید شده توسط مدل"})
            db.add_generated(cid,None,aid,text)
            await classify_and_store(aid,text)
            await emit("assistant",{"conversation_id":cid,"message_id":aid,"content":text,"autonomous":True,"time":db.now_iso()})
    else:
        # Thought network: analytical model proposes future possibilities; cheap model has already mediated the relevant memory.
        profile=db.get_model_profile("analytical")
        classifier=db.get_model_profile("classifier")
        if not profile or not classifier: return
        recent=messages[-12:]
        overview=db.taxonomy_overview_rows()
        selector_prompt=f"""
شبکه افکار دائمی فعال است.
تاریخ تولید همزاد: {conv.get('twin_birth_at')}
زمان فعلی: {db.now_iso()}
آخرین پیام‌ها:
{json.dumps(recent,ensure_ascii=False)[:30000]}

خلاصه حافظه:
{json.dumps(overview,ensure_ascii=False)[:40000]}

مدل ارزان‌قیمت باید از حافظه دو چیز را انتخاب کند:
1) نزدیک‌ترین مشابهات موضوعی به گفتگوهای اخیر.
2) داغ‌ترین/مهم‌ترین موضوعات بر اساس تازگی و تکرار.

فقط JSON:
{{"similar_paths":[["..."]],"hot_paths":[["..."]],"notes":"..."}}
"""
        from .ai import chat, _parse_json
        selector,_=await chat(classifier,[{"role":"system","content":"تو واسط حافظه هستی؛ فقط مسیرهای واقعی SQLite را انتخاب کن."},{"role":"user","content":selector_prompt}],
                          conversation_id=cid,stage="thought_memory_selection",
                          response_format={"type":"json_object"})
        selected=_parse_json(selector) or {"similar_paths":[],"hot_paths":[]}
        requested=[]
        for p in (selected.get("similar_paths") or [])+(selected.get("hot_paths") or []):
            requested.append({"path":p,"include_descendants":True,"limit":500})
        selected_data=db.retrieve_paths(requested)

        prompt=f"""
شبکه افکار دائمی فعال است.
تاریخ تولید همزاد: {conv.get('twin_birth_at')}
زمان فعلی: {db.now_iso()}
آخرین پیام‌ها:
{json.dumps(recent,ensure_ascii=False)[:30000]}

نزدیک‌ترین مشابهات و داغ‌ترین مسائل که مدل ارزان‌قیمت از حافظه انتخاب کرده:
{json.dumps(selected_data,ensure_ascii=False)[:80000]}

بر اساس داده‌های واقعی بالا، حداکثر سه احتمال/موضوع آینده که ممکن است برای کاربر مفید شوند پیشنهاد بده.
قطعیت ادعا نکن و داده‌ای را جعل نکن.
خروجی JSON:
{{"thoughts":[{{"title":"...","content":"...","path":["..."],"reason":"..."}}]}}
"""
        text,_=await chat(profile,[{"role":"system","content":"پیش‌بینی احتمالات آینده؛ بدون ادعای قطعیت."},{"role":"user","content":prompt}],
                          conversation_id=cid,stage="thought_network")
        obj=_parse_json(text) or {}
        ttl=int(db.get_setting("short_term_ttl_seconds","86400"))
        expires=(datetime.now(timezone.utc)+timedelta(seconds=ttl)).isoformat()
        for th in obj.get("thoughts",[])[:10]:
            tid=db.create_thought(cid,str(th.get("title","موضوع پیشنهادی")),
                                  str(th.get("content","")),th.get("path",[]),
                                  {"reason":th.get("reason",""),"generated_at":db.now_iso()},expires)
            await emit("thought",{"conversation_id":cid,"thought_id":tid,"title":th.get("title"),"content":th.get("content")})

async def scheduler():
    last_live=0; last_thought=0
    while True:
        await asyncio.sleep(2)
        now=asyncio.get_running_loop().time()
        try:
            live_interval=max(5,int(db.get_setting("live_interval_seconds","60")))
            thought_interval=max(10,int(db.get_setting("thought_interval_seconds","300")))
            for c in db.list_conversations():
                if c["live_mode"] and now-last_live>=live_interval:
                    await autonomous_for_conversation(c["id"],"live")
                if c["thought_network"] and now-last_thought>=thought_interval:
                    await autonomous_for_conversation(c["id"],"thought")
            if now-last_live>=live_interval: last_live=now
            if now-last_thought>=thought_interval: last_thought=now
            db.expire_thoughts()
        except Exception as e:
            await emit("error",{"error":str(e),"source":"scheduler"})

@app.on_event("startup")
async def startup():
    asyncio.create_task(scheduler())

@app.get("/api/events")
async def events():
    q=asyncio.Queue()
    subscribers.add(q)
    async def gen():
        try:
            yield f"event: ready\ndata: {json.dumps({'time':db.now_iso()})}\n\n"
            while True:
                yield await q.get()
        finally:
            subscribers.discard(q)
    return StreamingResponse(gen(),media_type="text/event-stream")
