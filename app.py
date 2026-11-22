import os, re, json, sqlite3, secrets, datetime
from flask import Flask, request, jsonify, abort, make_response
from werkzeug.security import generate_password_hash, check_password_hash
import agent

app = Flask(__name__, static_folder="static", static_url_path="")
DB = os.getenv("DB_PATH", "studypilot.db")

DB_URL = os.getenv("DATABASE_URL")   # Postgres (Neon/Supabase) when set, otherwise local SQLite
PG = bool(DB_URL)

class _DB:
    def __init__(s):
        if PG:
            import psycopg
            from psycopg.rows import dict_row
            s.c = psycopg.connect(DB_URL, row_factory=dict_row)
        else:
            s.c = sqlite3.connect(DB); s.c.row_factory = sqlite3.Row
    def execute(s, q, p=()):
        return (s.c.cursor() if PG else s.c).execute(q.replace("?", "%s") if PG else q, p)
    def __enter__(s): return s
    def __exit__(s, t, v, tb):
        (s.c.rollback if t else s.c.commit)(); s.c.close()

def db(): return _DB()

PK = "serial primary key" if PG else "integer primary key autoincrement"
with db() as c:
    for q in ["create table if not exists users(email text primary key, name text, hash text, level text, detail text, avail real default 4, ready int default 0)",
              "create table if not exists tokens(token text primary key, email text)",
              "create table if not exists subjects(id text primary key, email text, name text, exam text, hpt real, pos int)",
              f"create table if not exists topics(id {PK}, sid text, name text, done int, pos int, emb text)",
              f"create table if not exists chat(id {PK}, email text, role text, content text)",
              "create table if not exists tips(id int primary key, text text, emb text)"]:
        c.execute(q)

def fail(msg, code=400): abort(make_response(jsonify(error=msg), code))

def auth():
    t = request.headers.get("Authorization", "")[7:]
    with db() as c: r = c.execute("select email from tokens where token=?", (t,)).fetchone()
    if not r: fail("Please log in", 401)
    return r["email"]

def load(email):
    with db() as c:
        u = dict(c.execute("select * from users where email=?", (email,)).fetchone())
        subs = []
        for s in c.execute("select * from subjects where email=? order by pos", (email,)):
            ts = [{"n": t["name"], "d": bool(t["done"])} for t in c.execute("select * from topics where sid=? order by pos", (s["id"],))]
            subs.append({"id": s["id"], "name": s["name"], "exam": s["exam"], "hpt": s["hpt"], "topics": ts})
        chat = [{"r": r["role"], "t": r["content"]} for r in c.execute("select * from chat where email=? order by id", (email,))]
    return {"email": email, "name": u["name"], "level": u["level"], "detail": u["detail"], "avail": u["avail"], "ready": u["ready"], "subs": subs, "chat": chat}

def new_token(email):
    t = secrets.token_hex(24)
    with db() as c: c.execute("insert into tokens values(?,?)", (t, email))
    return t

@app.get("/")
def index(): return app.send_static_file("index.html")

@app.post("/api/signup")
def signup():
    b = request.get_json() or {}; email = (b.get("email") or "").strip().lower()
    if not email or len(b.get("password") or "") < 6 or not (b.get("name") or "").strip(): fail("Fill in all fields")
    with db() as c:
        if c.execute("select 1 from users where email=?", (email,)).fetchone(): fail("Account already exists — log in instead", 409)
        c.execute("insert into users(email,name,hash,level,detail) values(?,?,?,?,?)", (email, b["name"].strip(), generate_password_hash(b["password"]), b.get("level"), b.get("detail")))
    return jsonify(token=new_token(email), me=load(email))

@app.post("/api/login")
def login():
    b = request.get_json() or {}; email = (b.get("email") or "").strip().lower()
    with db() as c: u = c.execute("select hash from users where email=?", (email,)).fetchone()
    if not u or not check_password_hash(u["hash"], b.get("password") or ""): fail("Wrong email or password", 401)
    return jsonify(token=new_token(email), me=load(email))

@app.get("/api/state")
def get_state(): return jsonify(load(auth()))

@app.put("/api/state")
def put_state():
    email, b = auth(), request.get_json() or {}
    with db() as c:
        old = {(r["sid"], r["name"]): r["emb"] for r in c.execute("select sid,name,emb from topics where sid in (select id from subjects where email=?)", (email,))}
        c.execute("delete from topics where sid in (select id from subjects where email=?)", (email,))
        c.execute("delete from subjects where email=?", (email,))
        for i, s in enumerate(b.get("subs", [])):
            c.execute("insert into subjects values(?,?,?,?,?,?)", (s["id"], email, s["name"], s.get("exam"), s.get("hpt") or 1.5, i))
            for j, t in enumerate(s.get("topics", [])):
                c.execute("insert into topics(sid,name,done,pos,emb) values(?,?,?,?,?)", (s["id"], t["n"], int(bool(t.get("d"))), j, old.get((s["id"], t["n"]))))
        c.execute("update users set avail=?, ready=? where email=?", (b.get("avail") or 4, int(bool(b.get("ready"))), email))
        c.execute("delete from chat where email=?", (email,))
        for m in (b.get("chat") or [])[-40:]: c.execute("insert into chat(email,role,content) values(?,?,?)", (email, m["r"], m["t"]))
    return jsonify(ok=True)

def days_left(exam):
    return (datetime.date.fromisoformat(exam) - datetime.date.today()).days if exam else None

def stats(me):
    c = sum(t["d"] for s in me["subs"] for t in s["topics"]); t = sum(len(s["topics"]) for s in me["subs"])
    need = 0.0
    for s in me["subs"]:
        d = days_left(s["exam"]); r = sum(not x["d"] for x in s["topics"])
        h = agent._hours(r, d, s["hpt"] or 1.5) if d is not None else None
        need += h or 0
    return c, t, round(need, 1)

def kw_rag(email, q):
    """Keyword retrieval (no embedding model needed) over study tips and pending syllabus topics."""
    w = lambda x: {t for t in re.findall(r"[a-z0-9]+", x.lower()) if len(t) > 3}
    qw = w(q)
    def best(items, k):
        sc = sorted(((len(qw & w(t)), t) for t in items), key=lambda x: -x[0])
        return [t for n, t in sc[:k] if n]
    with db() as c:
        tops = [f'{r["sn"]}: {r["name"]}' for r in c.execute("select t.name, s.name sn from topics t join subjects s on s.id=t.sid where s.email=? and t.done=0", (email,))]
    return best(agent.TIPS, 2) + best(tops, 3)

def rag(email, me, q):
    """Retrieve the most relevant study tips and pending syllabus topics (SQLite-cached embeddings)."""
    if not agent.USE_EMBED: return kw_rag(email, q)
    try:
        qv = agent.embed(q)
        with db() as c:
            tips = []
            for i, t in enumerate(agent.TIPS):
                r = c.execute("select emb from tips where id=?", (i,)).fetchone()
                if not r:
                    e = json.dumps(agent.embed(t)); c.execute("insert into tips values(?,?,?)", (i, t, e)); r = {"emb": e}
                tips.append((t, json.loads(r["emb"])))
            tops = []
            for r in c.execute("select t.id,t.name,t.emb,s.name sn from topics t join subjects s on s.id=t.sid where s.email=? and t.done=0 limit 60", (email,)):
                e = r["emb"] or json.dumps(agent.embed(r["name"])); c.execute("update topics set emb=? where id=?", (e, r["id"]))
                tops.append((f'{r["sn"]}: {r["name"]}', json.loads(e)))
        return agent.top_k(qv, tips, 2) + agent.top_k(qv, tops, 3)
    except Exception:
        return []

@app.post("/api/chat")
def chat():
    email, b = auth(), request.get_json() or {}
    q = (b.get("message") or "").strip()[:1000]
    if not q: fail("Say something")
    me = load(email); c, t, need = stats(me)
    subs = "; ".join(f'{s["name"]} (exam {s["exam"] or "not set"}, {sum(not x["d"] for x in s["topics"])} topics left, {s["hpt"]} h/topic, {days_left(s["exam"])} days left)' for s in me["subs"])
    system = (f'You are StudyPilot, a warm, concise study coach for {me["name"]} ({me["detail"]}). Never calculate yourself: call check_exam_readiness for readiness and '
              f'hours_needed_per_day for daily hours, and call only the tools the question needs. Plain text, short.\nSubjects: {subs}\nUseful context: ' + " | ".join(rag(email, me, q)))
    hist = [{"role": m["role"], "content": m["content"]} for m in (b.get("history") or [])[-6:] if m.get("role") in ("user", "assistant")]
    def fallback():
        r = agent.check_exam_readiness(c, t)
        return f"{r}." + (f" You need about {need} hours a day to finish on time." if need else " Set exam dates in Planner to see daily hours.")
    return jsonify(text=agent.run_agent(system, hist + [{"role": "user", "content": q}], fallback))

@app.post("/api/negotiate")
def negotiate():
    me = load(auth()); _, _, need = stats(me)
    if not need: fail("Set exam dates and add topics first")
    final, log = agent.negotiate(need, me["avail"] or 4)
    ok, verdict = agent.readiness_report(final, need)
    return jsonify(final=final, need=need, feasible=ok, verdict=verdict, transcript=log)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False)
