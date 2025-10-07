# VC Research from `.mbox` (End-to-End)

This tool ingests an email **mbox** file, filters out noise, analyzes the **most recent message per conversation (by sender domain)** with **Gemini 2.5 Pro**, researches whether it’s a good investment lead, then drafts a personalized outreach email with **Gemini 2.5 Pro**. Results are exported to a CSV. You can use this for all sorts of other purposes that require you to do a deep dive in your email inbox, do research, and draft personalized follow-ups. 

Another good use case would be to do research on LPs, but up to you. 

All you have to do is change the `draft_email_prompt.txt` and the `email_analyzer_investment_prompt.txt` to your liking.

Look at `draft_email_prompt_phillip_example.txt` and `email_analyzer_investment_prompt_phillip_example.txt` for examples of we used this tool at KindredPM, probably to reach out to you as well. 

## What it does (pipeline)

1. **Load prompts**  
   - `email_analyzer_investment_prompt.txt`: LLM prompt used to judge/research a lead from a single latest email.  
   - `draft_email_prompt.txt`: LLM prompt used to draft the outreach email for each accepted lead.

2. **Parse + filter emails (mbox)**  
   - Ignores spam/trash, specific domains/senders, and subjects containing certain phrases/keywords.  
   - Extracts: from name/email, domain, to/cc lists, subject, ISO date, full chain text (HTML stripped).

3. **Sort + dedupe per conversation**  
   - Sorts by ISO `date` descending.  
   - Keeps **only the latest email per sender domain** as the “conversation representative”.

4. **LLM research pass** (`gemini-2.5-pro`)  
   - Calls `client.models.generate_content` with a Google Search grounding tool.  
   - Expects the model to return a single **JSON object** (in ```json fenced code block) describing the lead and a **decision** (e.g., `"decision": "1"` to keep).

5. **Draft email pass** (`gemini-2.5-pro`)  
   - For accepted leads, drafts a personalized outreach email using your second prompt.

6. **Export CSV**  
   - Flattens each kept lead (plus `investment_email_draft`) into `output.csv`.

---

## Quickstart

### 1) Install

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2) Configure your API key (pick one)

**Option A — .env file**  
Create a `.env` in the project root:

```
GOOGLE_AI_API_KEY=your_gemini_key_here
```

**Option B (recommended in secure/personal environments) — Hardcode in the script**  
If you prefer, edit the script and set `HARDCODED_GOOGLE_AI_API_KEY = "your_gemini_key_here"`.

> The script will use the hardcoded key first (if present), otherwise it falls back to `GOOGLE_AI_API_KEY` from the environment.

### 3) Provide inputs

- Export your mailbox via google takeout to an .mbox file at `input.mbox` (or change `MBOX_FILE`). Make sure it is saved in the same directory as the script.
- Add two prompt files:
  - `email_analyzer_investment_prompt.txt`
  - `draft_email_prompt.txt`
- Search for PHILLIP throughout the entire folder. Replace all instances of PHILLIP with what the custom instructions tell you (like firm name, your name, your firm's philosophy, etc.) so that the AI can speak in your voice and understand your priorities. 

### 4) Run

```bash
python end_to_end_vc_research.py
```

Logs will show pipeline progress.  
Final results are written to `output.csv`.

---

## Configuration & knobs

- **Files**
  - `MBOX_FILE = "input.mbox"`
  - `OUTPUT_CSV_FILE = "output.csv"`
  - `EMAIL_ANALYZER_PROMPT_FILE = "email_analyzer_investment_prompt.txt"`
  - `DRAFT_EMAIL_PROMPT_FILE = "draft_email_prompt.txt"`

- **Models**
  - `MODEL_NAME_ANALYSIS = "gemini-2.5-pro"` (or `gemini-2.5-flash` if you want something a bit faster)
  - `MODEL_NAME_DRAFT = "gemini-2.5-pro"`

- **Filters**
  - `IGNORE_DOMAINS = {...}`
  - `IGNORE_SENDERS = {...}`
  - `SUBJECT_PHRASE_IGNORE = "kindred just flagged"`
  - `SUBJECT_KEYWORDS_IGNORE = {"maintenance", "wo_id"}`

---

## Output schema

`output.csv` includes:

- `name`, `firm`, `role`, `email`
- `subject`, `date`, `sender_domain`, `from_name`, `to`, `cc`
- `thesis_fit`, `recent_fund_news`, other research fields
- `investment_email_draft`

---

## Error handling

- Skips emails if JSON not returned correctly.
- Continues on API errors.
- Prompts must enforce strict JSON output.

---

## Requirements

- Python 3.10+
- Dependencies in `requirements.txt`

---

## License

No license! Feel free to use, sell, modify, etc. to your liking. Hope it helps! If something breaks, just text me at 971-238-2109 or email me at phillip@kindredpm.ai