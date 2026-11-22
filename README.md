# StudyPilot – Study Schedule Optimizer

Python · Flask · LangChain `create_agent` (LangGraph) · Ollama qwen2.5:3b or OpenRouter · SQLite / Postgres · HTML/CSS/JS

## Features
- Agent with two tools (`check_exam_readiness`, `hours_needed_per_day`); the model chooses, Python does the maths. Hop limit 4. If the model is unreachable the Python results are still shown.
- Short-term memory (chat) + long-term memory (preferences, subjects, topics in the database).
- RAG over 12 study tips and the student's pending syllabus topics (Ollama embeddings locally, keyword retrieval when hosted).
- Syllabus upload (PDF/TXT) or paste; guided setup; Next step / Up next cards.
- Unit 4 Ambitious vs Realistic negotiation, 4-round cap, readiness report (Planner → Check my plan).
- Accounts with hashed passwords + tokens, work on any device.

## Run locally (SQLite + Ollama)
```
pip install -r requirements.txt
ollama pull qwen2.5:3b && ollama pull nomic-embed-text
python app.py        # http://localhost:5000
```

## Deploy publicly (free)
1. **Database** – neon.tech → create project → copy the connection string (`postgresql://...`).
2. **AI key** – openrouter.ai → create an API key.
3. **GitHub** – `git init && git add . && git commit -m "StudyPilot" && git branch -M main && git remote add origin <your repo url> && git push -u origin main`
4. **Render** – render.com → New → Blueprint (uses `render.yaml`) → pick the repo → set `DATABASE_URL` and `OPENROUTER_API_KEY` → Deploy.
5. Open the Render URL; on a phone use Add to Home Screen.

Free Render sleeps after ~15 min idle; the first visit then takes ~30–60 s. Without `OPENROUTER_API_KEY` the app still works and answers come from the Python tools.
