from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
UPLOADS = DATA / "uploads"
DATA.mkdir(exist_ok=True)
UPLOADS.mkdir(exist_ok=True)
DB_PATH = DATA / "digital_twin.sqlite3"

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA journal_mode=WAL")
    try:
        yield c
        c.commit()
    finally:
        c.close()

def init_db():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS model_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            role TEXT NOT NULL CHECK(role IN ('classifier','analytical')),
            base_url TEXT NOT NULL,
            api_key TEXT NOT NULL,
            model_name TEXT NOT NULL,
            temperature REAL NOT NULL DEFAULT 0.2,
            max_tokens INTEGER NOT NULL DEFAULT 4096,
            timeout_seconds INTEGER NOT NULL DEFAULT 180,
            extra_headers_json TEXT NOT NULL DEFAULT '{}',
            system_prompt TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL DEFAULT 'گفتگوی جدید',
            twin_birth_at TEXT,
            live_mode INTEGER NOT NULL DEFAULT 0,
            thought_network INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
            content TEXT NOT NULL,
            sent_at TEXT,
            received_at TEXT,
            provider TEXT,
            model_name TEXT,
            reply_to_message_id INTEGER REFERENCES messages(id),
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            original_name TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            path TEXT NOT NULL,
            extracted_text TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS taxonomy_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_id INTEGER REFERENCES taxonomy_nodes(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            node_type TEXT NOT NULL DEFAULT 'category',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(parent_id, title)
        );

        CREATE TABLE IF NOT EXISTS memory_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            taxonomy_node_id INTEGER NOT NULL REFERENCES taxonomy_nodes(id) ON DELETE CASCADE,
            source_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
            label TEXT NOT NULL,
            content TEXT NOT NULL,
            source_type TEXT NOT NULL DEFAULT 'user_message',
            event_time TEXT,
            confidence REAL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS message_taxonomy (
            message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            taxonomy_node_id INTEGER NOT NULL REFERENCES taxonomy_nodes(id) ON DELETE CASCADE,
            depth INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(message_id, taxonomy_node_id)
        );

        CREATE TABLE IF NOT EXISTS taxonomy_overview (
            taxonomy_node_id INTEGER PRIMARY KEY REFERENCES taxonomy_nodes(id) ON DELETE CASCADE,
            path TEXT NOT NULL,
            depth INTEGER NOT NULL,
            direct_children_count INTEGER NOT NULL DEFAULT 0,
            descendant_count INTEGER NOT NULL DEFAULT 0,
            item_count INTEGER NOT NULL DEFAULT 0,
            titles_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ai_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER REFERENCES conversations(id) ON DELETE SET NULL,
            message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
            model_role TEXT NOT NULL,
            direction TEXT NOT NULL,
            stage TEXT NOT NULL,
            request_json TEXT NOT NULL DEFAULT '{}',
            response_text TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            duration_ms INTEGER,
            error_text TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS generated_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER REFERENCES conversations(id) ON DELETE CASCADE,
            source_user_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
            source_assistant_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
            label TEXT NOT NULL DEFAULT 'داده های تولید شده توسط مدل',
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            source_user_message_sent_at TEXT
        );

        CREATE TABLE IF NOT EXISTS temporary_thoughts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER REFERENCES conversations(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            path_json TEXT NOT NULL DEFAULT '[]',
            source_context_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending',
            expires_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER REFERENCES conversations(id) ON DELETE CASCADE,
            thought_id INTEGER REFERENCES temporary_thoughts(id) ON DELETE CASCADE,
            text TEXT NOT NULL,
            read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_taxonomy_parent ON taxonomy_nodes(parent_id);
        CREATE INDEX IF NOT EXISTS idx_memory_node ON memory_items(taxonomy_node_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_logs_conversation ON ai_logs(conversation_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_thoughts_status ON temporary_thoughts(status, expires_at);
        """)

        # Lightweight migration for databases created by an earlier package version.
        cols = [r["name"] for r in c.execute("PRAGMA table_info(generated_data)").fetchall()]
        if "source_user_message_sent_at" not in cols:
            c.execute("ALTER TABLE generated_data ADD COLUMN source_user_message_sent_at TEXT")

        defaults = {
            "live_interval_seconds": "60",
            "thought_interval_seconds": "300",
            "short_term_ttl_seconds": "86400",
            "autonomous_timeout_seconds": "120",
            "last_maintenance_at": now_iso(),
        }
        for k, v in defaults.items():
            c.execute(
                "INSERT OR IGNORE INTO app_settings(key,value,created_at,updated_at) VALUES(?,?,?,?)",
                (k, v, now_iso(), now_iso()),
            )

def get_setting(key: str, default=None):
    with conn() as c:
        r = c.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

def set_setting(key: str, value):
    t = now_iso()
    with conn() as c:
        c.execute("""
        INSERT INTO app_settings(key,value,created_at,updated_at)
        VALUES(?,?,?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """, (key, str(value), t, t))

def json_dumps(v):
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))

def get_model_profiles():
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM model_profiles ORDER BY role,name")]

def get_model_profile(role):
    with conn() as c:
        r = c.execute("SELECT * FROM model_profiles WHERE role=? AND enabled=1 ORDER BY id LIMIT 1", (role,)).fetchone()
        return dict(r) if r else None

def upsert_model(data):
    t = now_iso()
    with conn() as c:
        if data.get("id"):
            c.execute("""
            UPDATE model_profiles SET name=?,role=?,base_url=?,api_key=?,model_name=?,
            temperature=?,max_tokens=?,timeout_seconds=?,extra_headers_json=?,system_prompt=?,
            enabled=?,updated_at=? WHERE id=?
            """, (data["name"], data["role"], data["base_url"].rstrip("/"), data["api_key"],
                  data["model_name"], float(data.get("temperature", .2)), int(data.get("max_tokens",4096)),
                  int(data.get("timeout_seconds",180)), json_dumps(data.get("extra_headers",{})),
                  data.get("system_prompt",""), int(bool(data.get("enabled",True))), t, int(data["id"])))
            return int(data["id"])
        cur = c.execute("""
        INSERT INTO model_profiles(name,role,base_url,api_key,model_name,temperature,max_tokens,
        timeout_seconds,extra_headers_json,system_prompt,enabled,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (data["name"], data["role"], data["base_url"].rstrip("/"), data["api_key"],
              data["model_name"], float(data.get("temperature", .2)), int(data.get("max_tokens",4096)),
              int(data.get("timeout_seconds",180)), json_dumps(data.get("extra_headers",{})),
              data.get("system_prompt",""), int(bool(data.get("enabled",True))), t, t))
        return cur.lastrowid

def delete_model(model_id):
    with conn() as c:
        c.execute("DELETE FROM model_profiles WHERE id=?", (model_id,))

def create_conversation(title="گفتگوی جدید"):
    t = now_iso()
    with conn() as c:
        cur = c.execute("INSERT INTO conversations(title,created_at,updated_at) VALUES(?,?,?)", (title,t,t))
        return cur.lastrowid

def list_conversations():
    with conn() as c:
        return [dict(r) for r in c.execute("""
        SELECT c.*, COUNT(m.id) message_count
        FROM conversations c LEFT JOIN messages m ON m.conversation_id=c.id
        GROUP BY c.id ORDER BY c.updated_at DESC
        """)]

def get_conversation(cid):
    with conn() as c:
        r = c.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        return dict(r) if r else None

def update_conversation(cid, **fields):
    allowed = {"title","live_mode","thought_network","twin_birth_at"}
    fields = {k:v for k,v in fields.items() if k in allowed}
    if not fields: return
    fields["updated_at"] = now_iso()
    sets = ",".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE conversations SET {sets} WHERE id=?", (*fields.values(), cid))

def delete_conversation(cid):
    with conn() as c:
        c.execute("DELETE FROM conversations WHERE id=?", (cid,))

def add_message(conversation_id, role, content, sent_at=None, received_at=None, provider=None, model_name=None, reply_to_message_id=None, metadata=None):
    t = now_iso()
    with conn() as c:
        cur = c.execute("""
        INSERT INTO messages(conversation_id,role,content,sent_at,received_at,provider,model_name,
        reply_to_message_id,metadata_json,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """, (conversation_id,role,content,sent_at,received_at,provider,model_name,reply_to_message_id,
              json_dumps(metadata or {}),t,t))
        c.execute("UPDATE conversations SET updated_at=? WHERE id=?", (t, conversation_id))
        return cur.lastrowid

def update_message(msg_id, **fields):
    fields["updated_at"] = now_iso()
    sets = ",".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE messages SET {sets} WHERE id=?", (*fields.values(), msg_id))

def get_messages(cid, limit=200):
    with conn() as c:
        return [dict(r) for r in c.execute("""
        SELECT * FROM messages WHERE conversation_id=? ORDER BY id ASC LIMIT ?
        """, (cid,limit))]

def add_attachment(message_id, name, mime, path, extracted):
    with conn() as c:
        cur = c.execute("""
        INSERT INTO attachments(message_id,original_name,mime_type,path,extracted_text,created_at)
        VALUES(?,?,?,?,?,?)
        """, (message_id,name,mime,path,extracted,now_iso()))
        return cur.lastrowid

def get_attachments_for_message(message_id):
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM attachments WHERE message_id=?", (message_id,))]

def _find_node(c, parent_id, title):
    r = c.execute("SELECT id FROM taxonomy_nodes WHERE parent_id IS ? AND lower(title)=lower(?)",
                  (parent_id,title.strip())).fetchone()
    return r["id"] if r else None

def get_or_create_node(parent_id, title, description="", node_type="category"):
    title = title.strip()
    if not title: return None
    with conn() as c:
        node_id = _find_node(c,parent_id,title)
        if node_id:
            if description:
                c.execute("UPDATE taxonomy_nodes SET description=?,updated_at=? WHERE id=?",
                          (description,now_iso(),node_id))
            return node_id
        t = now_iso()
        cur = c.execute("""
        INSERT INTO taxonomy_nodes(parent_id,title,description,node_type,created_at,updated_at)
        VALUES(?,?,?,?,?,?)
        """,(parent_id,title,description,node_type,t,t))
        return cur.lastrowid

def save_taxonomy_message(message_id, tree):
    """tree = [{title,description,children:[...],items:[{label,content,...}]}]"""
    def walk(c, parent_id, node, depth):
        title = str(node.get("title","")).strip()
        if not title: return
        existing = _find_node(c,parent_id,title)
        t = now_iso()
        if existing:
            node_id = existing
            if node.get("description"):
                c.execute("UPDATE taxonomy_nodes SET description=?,updated_at=? WHERE id=?",
                          (str(node.get("description","")),t,node_id))
        else:
            cur = c.execute("""
            INSERT INTO taxonomy_nodes(parent_id,title,description,node_type,created_at,updated_at)
            VALUES(?,?,?,?,?,?)
            """,(parent_id,title,str(node.get("description","")),str(node.get("node_type","category")),t,t))
            node_id = cur.lastrowid
        c.execute("""
        INSERT OR REPLACE INTO message_taxonomy(message_id,taxonomy_node_id,depth,created_at)
        VALUES(?,?,?,?)
        """,(message_id,node_id,depth,t))
        for item in node.get("items",[]) or []:
            label = str(item.get("label","اطلاعات"))
            content = str(item.get("content",""))
            if content:
                c.execute("""
                INSERT INTO memory_items(taxonomy_node_id,source_message_id,label,content,source_type,
                event_time,confidence,metadata_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                """,(node_id,message_id,label,content,str(item.get("source_type","message_analysis")),
                     item.get("event_time"),item.get("confidence"),json_dumps(item.get("metadata",{})),t,t))
        for child in node.get("children",[]) or []:
            walk(c,node_id,child,depth+1)
    with conn() as c:
        for n in tree or []:
            walk(c,None,n,0)
    refresh_overview()

def refresh_overview():
    t = now_iso()
    with conn() as c:
        nodes = c.execute("SELECT * FROM taxonomy_nodes ORDER BY id").fetchall()
        for n in nodes:
            nid = n["id"]
            depth = 0
            path_parts = [n["title"]]
            p = n["parent_id"]
            while p is not None:
                pr = c.execute("SELECT parent_id,title FROM taxonomy_nodes WHERE id=?", (p,)).fetchone()
                if not pr: break
                path_parts.append(pr["title"]); p = pr["parent_id"]; depth += 1
            path = " -> ".join(reversed(path_parts))
            direct = c.execute("SELECT COUNT(*) n FROM taxonomy_nodes WHERE parent_id=?", (nid,)).fetchone()["n"]
            descendants = c.execute("""
            WITH RECURSIVE d(id) AS (
              SELECT id FROM taxonomy_nodes WHERE parent_id=?
              UNION ALL SELECT n.id FROM taxonomy_nodes n JOIN d ON n.parent_id=d.id
            ) SELECT COUNT(*) n FROM d
            """,(nid,)).fetchone()["n"]
            items = c.execute("SELECT COUNT(*) n FROM memory_items WHERE taxonomy_node_id=?", (nid,)).fetchone()["n"]
            titles = [x["title"] for x in c.execute("SELECT title FROM taxonomy_nodes WHERE parent_id=? ORDER BY title",(nid,))]
            c.execute("""
            INSERT INTO taxonomy_overview(taxonomy_node_id,path,depth,direct_children_count,
            descendant_count,item_count,titles_json,updated_at)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(taxonomy_node_id) DO UPDATE SET path=excluded.path,depth=excluded.depth,
            direct_children_count=excluded.direct_children_count,descendant_count=excluded.descendant_count,
            item_count=excluded.item_count,titles_json=excluded.titles_json,updated_at=excluded.updated_at
            """,(nid,path,depth,direct,descendants,items,json_dumps(titles),t))

def taxonomy_tree():
    with conn() as c:
        nodes = [dict(x) for x in c.execute("SELECT * FROM taxonomy_nodes ORDER BY title")]
        items = [dict(x) for x in c.execute("SELECT * FROM memory_items ORDER BY id")]
    by_parent={}
    for n in nodes: by_parent.setdefault(n["parent_id"],[]).append(n)
    by_node={}
    for it in items: by_node.setdefault(it["taxonomy_node_id"],[]).append(it)
    def build(parent):
        out=[]
        for n in by_parent.get(parent,[]):
            x=dict(n)
            x["items"]=by_node.get(n["id"],[])
            x["children"]=build(n["id"])
            out.append(x)
        return out
    return build(None)

def taxonomy_overview_rows():
    with conn() as c:
        return [dict(x) for x in c.execute("SELECT * FROM taxonomy_overview ORDER BY depth,path")]

def retrieve_paths(requests):
    """
    requests: [{"path":["احساسی","روابط احساسی"],"include_descendants":true,"limit":...}]
    No semantic depth limit is applied.
    """
    results=[]
    with conn() as c:
        for req in requests or []:
            path=[str(x).strip() for x in req.get("path",[]) if str(x).strip()]
            current=None; found=True
            for title in path:
                r=c.execute("SELECT * FROM taxonomy_nodes WHERE parent_id IS ? AND lower(title)=lower(?)",
                            (current,title)).fetchone()
                if not r: found=False; break
                current=r["id"]
            if not found:
                results.append({"requested_path":path,"found":False,"data":[]})
                continue
            ids=[current]
            if req.get("include_descendants",True):
                rows=c.execute("""
                WITH RECURSIVE d(id) AS (
                  SELECT id FROM taxonomy_nodes WHERE id=?
                  UNION ALL SELECT n.id FROM taxonomy_nodes n JOIN d ON n.parent_id=d.id
                ) SELECT id FROM d
                """,(current,)).fetchall()
                ids=[r["id"] for r in rows]
            qmarks=",".join("?"*len(ids))
            mem=c.execute(f"""
            SELECT mi.*, tn.title node_title, tn.parent_id
            FROM memory_items mi JOIN taxonomy_nodes tn ON tn.id=mi.taxonomy_node_id
            WHERE mi.taxonomy_node_id IN ({qmarks})
            ORDER BY mi.event_time DESC, mi.id DESC LIMIT ?
            """,(*ids,int(req.get("limit",500)))).fetchall()
            node_rows=c.execute(f"SELECT id,parent_id,title,description,node_type FROM taxonomy_nodes WHERE id IN ({qmarks})",ids).fetchall()
            results.append({
                "requested_path":path,"found":True,
                "nodes":[dict(r) for r in node_rows],
                "data":[dict(r) for r in mem]
            })
    return results

def add_log(**kw):
    with conn() as c:
        c.execute("""
        INSERT INTO ai_logs(conversation_id,message_id,model_role,direction,stage,request_json,
        response_text,status,duration_ms,error_text,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,(kw.get("conversation_id"),kw.get("message_id"),kw["model_role"],kw["direction"],
             kw["stage"],json_dumps(kw.get("request",{})),kw.get("response",""),kw.get("status","ok"),
             kw.get("duration_ms"),kw.get("error",""),now_iso()))

def get_logs(cid=None, limit=300):
    with conn() as c:
        if cid:
            rows=c.execute("SELECT * FROM ai_logs WHERE conversation_id=? ORDER BY id DESC LIMIT ?",(cid,limit))
        else:
            rows=c.execute("SELECT * FROM ai_logs ORDER BY id DESC LIMIT ?",(limit,))
        return [dict(r) for r in rows]

def add_generated(conversation_id, user_id, assistant_id, content):
    t=now_iso()
    source_sent_at=None
    with conn() as c:
        if user_id:
            r=c.execute("SELECT sent_at FROM messages WHERE id=?", (user_id,)).fetchone()
            source_sent_at=r["sent_at"] if r else None
        c.execute("""
        INSERT INTO generated_data(conversation_id,source_user_message_id,source_assistant_message_id,
        label,content,created_at,updated_at,source_user_message_sent_at)
        VALUES(?,?,?,?,?,?,?,?)
        """,(conversation_id,user_id,assistant_id,"داده های تولید شده توسط مدل",content,t,t,source_sent_at))

def create_thought(conversation_id,title,content,path,context,expires_at):
    t=now_iso()
    with conn() as c:
        cur=c.execute("""
        INSERT INTO temporary_thoughts(conversation_id,title,content,path_json,source_context_json,
        status,expires_at,created_at,updated_at) VALUES(?,?,?,?,?,'pending',?,?,?)
        """,(conversation_id,title,json_dumps(content),json_dumps(path),json_dumps(context),expires_at,t,t))
        tid=cur.lastrowid
        c.execute("INSERT INTO notifications(conversation_id,thought_id,text,created_at) VALUES(?,?,?,?)",
                  (conversation_id,tid,title,t))
        return tid

def get_thoughts(cid=None):
    with conn() as c:
        if cid:
            rows=c.execute("SELECT * FROM temporary_thoughts WHERE conversation_id=? ORDER BY id DESC",(cid,))
        else:
            rows=c.execute("SELECT * FROM temporary_thoughts ORDER BY id DESC")
        return [dict(x) for x in rows]

def update_thought(tid,status):
    with conn() as c:
        c.execute("UPDATE temporary_thoughts SET status=?,updated_at=? WHERE id=?",(status,now_iso(),tid))

def expire_thoughts():
    t=now_iso()
    with conn() as c:
        c.execute("UPDATE temporary_thoughts SET status='expired',updated_at=? WHERE status='pending' AND expires_at IS NOT NULL AND expires_at<?",(t,t))

def get_unread_notifications(cid):
    with conn() as c:
        return [dict(x) for x in c.execute(
            "SELECT * FROM notifications WHERE conversation_id=? AND read=0 ORDER BY id DESC",(cid,))]

def mark_notification_read(nid):
    with conn() as c:
        c.execute("UPDATE notifications SET read=1 WHERE id=?",(nid,))
