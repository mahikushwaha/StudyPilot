"""Agent logic: two tools, Ollama tool-calling loop with hop limit, RAG, Ambitious-vs-Realistic negotiation.
All maths is done here in Python; the model only decides which tool to call."""
import os, math, requests
from langchain.agents import create_agent
from langchain_core.tools import tool

OLLAMA = os.getenv("OLLAMA_URL", "http://localhost:11434")
MODEL = os.getenv("MODEL", "qwen2.5:3b")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
OR_KEY = os.getenv("OPENROUTER_API_KEY")   # if set, a hosted model is used instead of Ollama
OR_MODEL = os.getenv("OPENROUTER_MODEL", "qwen/qwen-2.5-72b-instruct")
USE_EMBED = not OR_KEY and os.getenv("USE_EMBEDDINGS", "1") == "1"   # embeddings via Ollama; otherwise keyword retrieval
MAX_HOPS = 4          # supervisor hop limit
MAX_ROUNDS = 4        # negotiation cap

TIPS = [
 "Study in focused 25-minute blocks with 5-minute breaks (Pomodoro).",
 "Revise a topic within 24 hours of learning it, then again after a week.",
 "Start with your weakest subject while your mind is fresh.",
 "Practise previous years' question papers under timed conditions.",
 "Explain a topic aloud in your own words; gaps show up immediately.",
 "Sleep 7-8 hours; memory consolidation happens during sleep.",
 "Plan the week on Sunday and keep one buffer day for catching up.",
 "Use active recall (self-quizzing) instead of rereading notes.",
 "Break large chapters into small topics you can finish in one sitting.",
 "Keep a short formula and key-points sheet for last-day revision.",
 "Switch subjects every two to three hours to avoid fatigue.",
 "Silence notifications and keep your phone out of reach while studying.",
]

# ---- the two tools (return str, as in the brief) ----
def check_exam_readiness(topics_covered: int, total_topics: int) -> str:
    """Readiness level from the number of topics covered and the total number of topics."""
    c, t = int(topics_covered), int(total_topics)
    p = round(c / t * 100) if t > 0 else 0
    level = "Exam ready" if p >= 90 else "Almost ready" if p >= 70 else "Getting there" if p >= 40 else "Just starting"
    return f"{p}% ({c} of {t} topics covered): {level}"

def _hours(remaining, days_left, hpt):
    return None if int(days_left) <= 0 else round(int(remaining) * float(hpt) / int(days_left), 1)

def hours_needed_per_day(remaining_topics: int, days_left: int, hours_per_topic: float) -> str:
    """Study hours needed per day to finish the remaining topics before the exam."""
    h = _hours(remaining_topics, days_left, hours_per_topic)
    return "Exam date has passed or is today" if h is None else f"{h} hours per day for {remaining_topics} topics over {days_left} days"

TOOLS = [tool(check_exam_readiness), tool(hours_needed_per_day)]

# ---- model: hosted (OpenRouter) if a key is set, otherwise local Ollama ----
def get_model():
    if OR_KEY:
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=OR_MODEL, api_key=OR_KEY, base_url="https://openrouter.ai/api/v1", temperature=0.2, timeout=60)
    from langchain_ollama import ChatOllama
    return ChatOllama(model=MODEL, base_url=OLLAMA, temperature=0.2)

def embed(text):
    r = requests.post(f"{OLLAMA}/api/embeddings", json={"model": EMBED_MODEL, "prompt": text}, timeout=60)
    r.raise_for_status()
    return r.json()["embedding"]

def cos(a, b):
    d = sum(x * y for x, y in zip(a, b))
    return d / ((math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))) or 1)

def top_k(qv, items, k):
    """items: list of (text, vector). Returns the k most similar texts."""
    return [t for t, v in sorted(items, key=lambda i: -cos(qv, i[1]))[:k]]

def run_agent(system, messages, fallback):
    """LangChain create_agent (LangGraph): the model picks tools, Python runs them. Hop-limited.
    If the model is unreachable, the Python results are still shown."""
    try:
        ag = create_agent(get_model(), TOOLS, system_prompt=system)
        out = ag.invoke({"messages": messages}, config={"recursion_limit": 2 * MAX_HOPS + 1})
        c = out["messages"][-1].content
        if isinstance(c, list):
            c = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in c)
        return (c or "").strip() or fallback()
    except Exception:
        return fallback()

# ---- Unit 4: Ambitious vs Realistic negotiation (alternating offers, capped rounds) ----
def negotiate(need, avail):
    a = min(12.0, round(max(need * 1.3, need + 1), 1))      # Ambitious opens high
    r = round(min(avail, need) * 0.8, 1)                      # Realistic opens low
    log, final, agreed = [], None, False
    for i in range(1, MAX_ROUNDS + 1):
        log.append({"round": i, "who": "Ambitious", "say": f"I propose {a} hours per day to be fully prepared."})
        log.append({"round": i, "who": "Realistic", "say": f"I can sustain {r} hours per day with my {avail} hour limit."})
        if a - r <= 0.5:
            final, agreed = round((a + r) / 2 * 2) / 2, True
            log.append({"round": i, "who": "Realistic", "say": f"Agreed at {final} hours per day."})
            break
        a, r = round(a - (a - r) * 0.4, 1), round(r + (a - r) * 0.4, 1)
    if not agreed:
        final = r
        log.append({"round": MAX_ROUNDS, "who": "Realistic", "say": f"Round limit of {MAX_ROUNDS} reached, so we stop at {final} hours per day. The cap prevents endless back-and-forth."})
    final = min(final, avail)  # nobody can study more than the hours available
    return final, log

def readiness_report(final, need):
    if final >= need * 0.95:
        return True, f"On track: {final} h/day covers the {need} h/day your exams need."
    return False, f"Short by {round(need - final, 1)} h/day against the {need} h/day needed. Add hours, trim topics or move exam dates."
