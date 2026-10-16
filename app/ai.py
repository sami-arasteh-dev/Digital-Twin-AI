from __future__ import annotations
import json, time, re, httpx
from . import db

def _parse_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try: return json.loads(m.group(0))
            except Exception: pass
    return None

def _headers(profile):
    headers={"Content-Type":"application/json","Authorization":f"Bearer {profile['api_key']}"}
    try: headers.update(json.loads(profile.get("extra_headers_json") or "{}"))
    except Exception: pass
    return headers

async def chat(profile, messages, conversation_id=None, message_id=None, stage="chat", response_format=None):
    if not profile:
        raise RuntimeError("مدل مورد نظر در تنظیمات تعریف یا فعال نشده است.")
    payload={
        "model":profile["model_name"],
        "messages":messages,
        "temperature":float(profile.get("temperature",.2)),
        "max_tokens":int(profile.get("max_tokens",4096)),
    }
    if response_format:
        payload["response_format"]=response_format
    started=time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=float(profile.get("timeout_seconds",180))) as client:
            r=await client.post(profile["base_url"].rstrip("/")+"/chat/completions",headers=_headers(profile),json=payload)
            r.raise_for_status()
            data=r.json()
        text=data["choices"][0]["message"].get("content","")
        db.add_log(conversation_id=conversation_id,message_id=message_id,model_role=profile["role"],
                   direction="outbound/inbound",stage=stage,request=payload,response=text,status="ok",
                   duration_ms=int((time.perf_counter()-started)*1000))
        return text, data
    except Exception as e:
        db.add_log(conversation_id=conversation_id,message_id=message_id,model_role=profile["role"],
                   direction="outbound",stage=stage,request=payload,response="",status="error",
                   duration_ms=int((time.perf_counter()-started)*1000),error=str(e))
        raise

CLASSIFIER_INSTRUCTIONS = r"""
تو مدل ارزان‌قیمتِ طبقه‌بندی و واسط داده هستی.
وظیفه تو این است که پیام را از کلی‌ترین مفهوم تا جزئی‌ترین مفهوم در یک درخت سلسله‌مراتبی تحلیل کنی.
عمق درخت محدود نیست. دسته‌ها را کوتاه، معنادار و پایدار انتخاب کن و تا جایی که برای حافظه بلندمدت مفید است پایین برو.
همچنین حقایق، رویدادها، تصمیم‌ها، روابط، ترجیحات، خاطرات و سایر داده‌های ماندگار را به عنوان items در مناسب‌ترین گره ثبت کن.
هر item باید label و content داشته باشد و نباید چیزی را جعل کند.

فقط JSON معتبر برگردان:
{
 "tree":[
   {
     "title":"...",
     "description":"...",
     "items":[{"label":"...","content":"...","event_time":"ISO-or-null","confidence":0.0}],
     "children":[...]
   }
 ]
}
"""

def build_classifier_prompt(content, attachments_text=""):
    return f"""{CLASSIFIER_INSTRUCTIONS}

متن پیام:
{content}

محتوای استخراج‌شده از فایل‌ها:
{attachments_text[:30000]}
"""

def build_analytical_system():
    return r"""
تو «همزاد دیجیتال» کاربر هستی. پاسخ باید بر اساس پیام جاری و حافظه ساختاریافته کاربر تولید شود.
اطلاعات حافظه صرفاً داده مرجع است؛ چیزی را که در حافظه نیست جعل نکن.
فاصله زمانی بین پیام‌ها را درک کن و زمان فعلی، زمان پیام جاری، زمان پیام قبلی و تاریخ تولید همزاد را در تحلیل لحاظ کن.

مکانیزم حافظه:
- ابتدا overview درخت و زیرشاخه‌های آن را می‌بینی.
- اگر داده بیشتری لازم داری، در خروجی JSON به شکل retrieval_requests درخواست کن.
- مسیرها می‌توانند هر عمقی داشته باشند.
- می‌توانی در هر مرحله چند مسیر بخواهی.
- بعد از دریافت داده، اگر باز هم داده لازم داری، retrieval_requests دیگری صادر کن.
- وقتی داده کافی داری، پاسخ نهایی را در final_answer قرار بده.

در حالت درخواست داده، فقط JSON معتبر:
{
 "retrieval_requests":[
   {"path":["دسته","زیر دسته","..."],"include_descendants":true,"limit":500}
 ],
 "reason":"..."
}

در حالت پاسخ نهایی:
{
 "retrieval_requests":[],
 "final_answer":"پاسخ کامل به کاربر"
}

هرگز متن نهایی را در reason نگذار.
"""

def compact_overview():
    rows=db.taxonomy_overview_rows()
    return rows

async def classify_and_store(message_id, content, attachments_text=""):
    profile=db.get_model_profile("classifier")
    prompt=build_classifier_prompt(content,attachments_text)
    text,_=await chat(profile,[{"role":"system","content":profile.get("system_prompt") or "تو یک طبقه‌بند حافظه دقیق هستی."},
                               {"role":"user","content":prompt}],
                     message_id=message_id,stage="classify_and_store",response_format={"type":"json_object"})
    obj=_parse_json(text) or {"tree":[]}
    db.save_taxonomy_message(message_id,obj.get("tree",[]))
    return obj

async def analytical_turn(conversation_id, user_message_id, user_text, history, attachments=None):
    analytical=db.get_model_profile("analytical")
    classifier=db.get_model_profile("classifier")
    if not analytical or not classifier:
        raise RuntimeError("هر دو مدل Classifier و Analytical باید در تنظیمات فعال باشند.")

    conv=db.get_conversation(conversation_id)
    first_user=conv.get("twin_birth_at") if conv else None
    previous=[m for m in history if m["id"] != user_message_id][-30:]
    overview=db.taxonomy_overview_rows()

    def overview_text():
        lines=[]
        for x in db.taxonomy_overview_rows():
            titles=json.loads(x["titles_json"])
            lines.append(f"- {x['path']} | depth={x['depth']} | children={x['direct_children_count']} | descendants={x['descendant_count']} | items={x['item_count']} | child_titles={titles}")
        return "\n".join(lines)[:60000]

    # Explicit cheap-model handoff: the inexpensive model receives the exact user
    # message and the current taxonomy overview, then packages that context for the
    # analytical model. This is deliberately a real model call, not just backend glue.
    handoff_prompt = f"""
پیام کاربر را عیناً حفظ کن و برای مدل تحلیلی یک بسته زمینه بساز.

تاریخ تولید همزاد: {first_user}
زمان فعلی: {db.now_iso()}

پیام کاربر (عیناً):
{user_text}

آخرین پیام‌های گفتگو:
{json.dumps(previous,ensure_ascii=False,indent=2)[:50000]}

فهرست دسته‌بندی‌های کلی و زیرمجموعه‌ها:
{overview_text()}

خروجی فقط JSON:
{{
  "user_message_exact":"...",
  "taxonomy_overview":[{{"path":"...","depth":0,"children":0,"descendants":0,"items":0,"child_titles":[]}}],
  "routing_notes":"..."
}}
"""
    handoff_text,_=await chat(
        classifier,
        [{"role":"system","content":"تو واسط ارزان‌قیمت بین کاربر، حافظه SQLite و مدل تحلیلی هستی."},
         {"role":"user","content":handoff_prompt}],
        conversation_id=conversation_id,message_id=user_message_id,
        stage="cheap_model_handoff",response_format={"type":"json_object"}
    )
    handoff=_parse_json(handoff_text) or {
        "user_message_exact":user_text,
        "taxonomy_overview":overview,
        "routing_notes":""
    }

    base_user = {
        "role":"user",
        "content":f"""
بسته زمینه تهیه‌شده توسط مدل ارزان‌قیمت:
{json.dumps(handoff,ensure_ascii=False,indent=2)[:90000]}

زمان دقیق فعلی: {db.now_iso()}
"""
    }
    if attachments:
        parts=[{"type":"text","text":base_user["content"]}]
        for a in attachments:
            if a["mime_type"].startswith("image/") and a.get("data_url"):
                parts.append({"type":"image_url","image_url":{"url":a["data_url"]}})
            elif a.get("extracted_text"):
                parts.append({"type":"text","text":f"\n[فایل {a['original_name']}]\n{a['extracted_text'][:30000]}"})
        base_user["content"]=parts

    analytical_system = build_analytical_system()
    if analytical.get("system_prompt"):
        analytical_system += "\n\nدستور اختصاصی ثبت‌شده در تنظیمات:\n" + analytical["system_prompt"]
    messages=[{"role":"system","content":analytical_system}]
    for m in previous[-20:]:
        messages.append({"role":m["role"],"content":m["content"]})
    messages.append(base_user)

    passes=0
    while True:
        passes += 1
        text,_=await chat(analytical,messages,conversation_id=conversation_id,message_id=user_message_id,stage=f"analytical_pass_{passes}",
                           response_format={"type":"json_object"})
        obj=_parse_json(text)
        if not obj:
            # Graceful fallback if provider ignores JSON mode.
            return text
        requests=obj.get("retrieval_requests") or []
        final=obj.get("final_answer")
        if requests:
            data=db.retrieve_paths(requests)
            fulfillment = json.dumps(data,ensure_ascii=False,indent=2)
            # Cheap model mediates/compacts requested memory for the analytical model.
            mediator_prompt=f"""
پیام کاربر:
{user_text}

درخواست‌های بازیابی مدل تحلیلی:
{json.dumps(requests,ensure_ascii=False,indent=2)}

داده خام SQLite:
{fulfillment[:120000]}

فقط داده‌های مرتبط با درخواست‌ها را با حفظ معنای دقیق و بدون جعل خلاصه کن.
خروجی JSON:
{{"retrieved_context":[{{"path":[...],"data":[...]}}]}}
"""
            med,_=await chat(classifier,[{"role":"system","content":"تو واسط حافظه هستی؛ داده خام را دقیق و محدود به درخواست تبدیل کن."},
                                         {"role":"user","content":mediator_prompt}],
                             conversation_id=conversation_id,message_id=user_message_id,
                             stage=f"memory_mediation_{passes}",response_format={"type":"json_object"})
            med_obj=_parse_json(med) or {"retrieved_context":data}
            messages.append({"role":"assistant","content":text})
            messages.append({"role":"user","content":
                             "نتیجه بازیابی درخواستی تو از حافظه:\n"+
                             json.dumps(med_obj,ensure_ascii=False,indent=2)[:120000]})
            continue
        if final is not None:
            return str(final)
        return text
