#!/usr/bin/env python3
"""
An end-to-end pipeline to take an mbox file as input, filter and process
emails, and generate an output csv file with research and a drafted email.
"""


from datetime import datetime, timezone
from zoneinfo import ZoneInfo  # Python 3.9+
import csv
import mailbox
import re
import os
import sys
import json
import time
from email import policy
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime, getaddresses
from html.parser import HTMLParser

from dotenv import load_dotenv
from google import genai
from google.genai import types

# Load environment variables from .env file
load_dotenv()

# Configure the Generative AI client
# try:
#     client = genai.Client(api_key=os.getenv("GOOGLE_AI_API_KEY"))
# except Exception as e:
#     print(f"Error initializing Google Generative AI client: {e}")
#     sys.exit(1)


# Increase CSV field size limit
try:
    csv.field_size_limit(sys.maxsize)
except OverflowError:
    csv.field_size_limit(2**31 - 1)

# --- Configuration ---

CUTOFF_LOCAL_TZ = ZoneInfo("Asia/Seoul")
CUTOFF_LOCAL = datetime(2025, 9, 19, 0, 0, 0, tzinfo=CUTOFF_LOCAL_TZ)
CUTOFF_UTC = CUTOFF_LOCAL.astimezone(timezone.utc)


# Option A: HARDCODED_GOOGLE_AI_API_KEY
HARDCODED_GOOGLE_AI_API_KEY = ""  # e.g., "AIza...your_key_here..."

# Option B: .env / environment variable fallback
# GOOGLE_AI_API_KEY from environment (loaded via python-dotenv).

# Configure the Generative AI client
try:
    effective_api_key = HARDCODED_GOOGLE_AI_API_KEY.strip() or os.getenv("GOOGLE_AI_API_KEY") or os.getenv("GOOGLE_AI_API_KEY".replace("GOOGLE_AI_API_KEY", "GOOGLE_AI_API_KEY"))
    # Note: primary expected var is GOOGLE_AI_API_KEY
    if not effective_api_key:
        raise RuntimeError(
            "No Gemini API key found. Set HARDCODED_GOOGLE_AI_API_KEY "
            'or define GOOGLE_AI_API_KEY in environment/.env'
        )
    client = genai.Client(api_key=effective_api_key)
except Exception as e:
    print(f"Error initializing Google Generative AI client: {e}")
    sys.exit(1)


# Hardcoded filenames
MBOX_FILE = "input.mbox"
OUTPUT_CSV_FILE = "output.csv"
EMAIL_ANALYZER_PROMPT_FILE = "email_analyzer_investment_prompt_phillip_example.txt"
DRAFT_EMAIL_PROMPT_FILE = "draft_email_prompt_phillip_example.txt"


MODEL_NAME_ANALYSIS = "gemini-2.5-pro"
MODEL_NAME_DRAFT = "gemini-2.5-pro"

# Filtering rules 
# PHILLIP: what domains/senders to ignore 
IGNORE_DOMAINS = {"integrityokc.com", "rosenbaumrealtygroup.com", "coloradorpm.com"}

#Ignore certain senders
IGNORE_SENDERS = {
    "support@integrityokc.com",
    "demo@kindredpm.ai",
    "kindredsnyder@gmail.com",
}

#PHILLIP: what subject phrases/keywords to ignore
SUBJECT_PHRASE_IGNORE = "kindred just flagged"
SUBJECT_KEYWORDS_IGNORE = {"maintenance", "wo_id"}


# --- Helper Functions for Email Parsing ---

class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []

    def handle_data(self, d):
        self._parts.append(d)

    def get_text(self):
        return "".join(self._parts)

def _html_to_text(html: str) -> str:
    stripper = _HTMLStripper()
    try:
        stripper.feed(html)
        return stripper.get_text()
    except Exception:
        return re.sub(r"<[^>]+>", "", html)

def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value

def _extract_addresses(msg, header_name: str) -> str:
    raw = msg.get_all(header_name, [])
    addrs = [a for _, a in getaddresses(raw)]
    return ", ".join(sorted(set(a for a in addrs if a)))

def _extract_name_and_email(addr_header: str) -> tuple[str, str]:
    name, email = parseaddr(addr_header or "")
    return name or "", (email or "").lower()

def _sender_domain(email: str) -> str:
    return email.split("@", 1)[-1].lower() if "@" in email else ""

def _message_datetime(msg):
    date_hdr = msg.get("Date")
    if not date_hdr:
        return ""
    try:
        dt = parsedate_to_datetime(date_hdr)
        return dt.isoformat()
    except Exception:
        return ""

def _get_body_text(msg) -> str:
    text_chunks = []
    if msg.is_multipart():
        for part in msg.walk():
            cdisp = str(part.get("Content-Disposition") or "").lower()
            ctype = str(part.get_content_type() or "").lower()
            if "attachment" in cdisp:
                continue
            if ctype == "text/plain":
                try:
                    text_chunks.append(part.get_content().strip())
                except Exception:
                    try:
                        text_chunks.append(part.get_payload(decode=True).decode(errors="replace").strip())
                    except Exception:
                        pass
            elif ctype == "text/html":
                try:
                    html = part.get_content()
                except Exception:
                    html = part.get_payload(decode=True).decode(errors="replace")
                text_chunks.append(_html_to_text(html).strip())
    else:
        try:
            text_chunks.append(msg.get_content().strip())
        except Exception:
            try:
                text_chunks.append(msg.get_payload(decode=True).decode(errors="replace").strip())
            except Exception:
                pass
    return "\n\n".join(p for p in text_chunks if p)

def subject_matches_ignores(subject: str) -> bool:
    s = subject.lower()
    if SUBJECT_PHRASE_IGNORE in s:
        return True
    for w in SUBJECT_KEYWORDS_IGNORE:
        if w in s:
            return True
    return False

def is_in_spam_or_trash(msg) -> bool:
    """Check if the email is labeled as Spam or Trash."""
    for header in ("X-GM-LABELS", "X-Gmail-Labels", "X-Folder", "Folder"):
        vals = msg.get_all(header, [])
        for v in vals:
            if "spam" in v.lower() or "trash" in v.lower():
                return True
    return False

def json_match(text):
    """Extracts a JSON object from a string."""
    match = re.search(r"```json\n({.*?})\n```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
    return None


# --- Main Pipeline Functions ---

def parse_mbox_file(mbox_file: str) -> list:
    """
    Parses an mbox file, filters emails based on defined rules,
    and returns a list of email data.
    """
    print(f"Starting to parse mbox file: {mbox_file}")
    mbox = mailbox.mbox(mbox_file, factory=mailbox.mboxMessage)
    rows = []
    for i,msg in enumerate(mbox):
        if i % 10000 == 9999:
            print(f"Processing email {i+1}/{len(mbox)}")
        from_name, from_email = _extract_name_and_email(msg.get("From", ""))
        subj = _decode_header(msg.get("Subject", ""))
        date_iso = _message_datetime(msg)

        # Ignore rules
        if is_in_spam_or_trash(msg):
            continue
        dom = _sender_domain(from_email)
        if dom in IGNORE_DOMAINS:
            continue
        if from_email in IGNORE_SENDERS:
            continue
        if subject_matches_ignores(subj):
            continue

        body_text = _get_body_text(msg)

        row = {
            "email": from_email,
            "subject": subj,
            "date": date_iso,
            "full_email_chain": body_text,
            "from_name": from_name,
            "sender_domain": dom,
            "to": _extract_addresses(msg, "To"),
            "cc": _extract_addresses(msg, "Cc"),
        }
        rows.append(row)
    print(f"Found {len(rows)} emails after initial filtering.")
    return rows

def analyze_and_research_emails(emails: list, prompt_template: str) -> list:
    """
    Analyzes a list of the MOST RECENT emails from each conversation, performs research,
    and returns a list of potential investment leads.
    """
    print("Starting email analysis and research phase...")
    investment_leads = []
    
    grounding_tool = types.Tool(google_search=types.GoogleSearch())
    config = types.GenerateContentConfig(tools=[grounding_tool])

    # This function now receives a pre-filtered list of the latest emails.
    # No need for the 'current_lists' check anymore.
    for i, row in enumerate(emails):
        print(f"Analyzing conversation {i+1}/{len(emails)} with {row['sender_domain']}...")
        
        # The prompt template can be simplified, as we no longer pass 'current_lists'
        contents = prompt_template.replace("((input))", str(row))
        
        try:
            resp = client.models.generate_content(
                model=MODEL_NAME_ANALYSIS,
                contents=contents,
                config=config,
            )
            answer_dict = json_match(resp.text)

            if not answer_dict or answer_dict == "0":
                print("  - Not a good match, skipping.")
                continue
            
            key = list(answer_dict.keys())[0]
            lead_data = answer_dict[key]

            if lead_data.get("decision", "0") == "0":
                print("  - Model decision was '0', skipping.")
                continue

            print(f"  + Found potential lead: {key}")
            investment_leads.append(answer_dict)

        except Exception as e:
            print(f"  - An error occurred while processing email {i+1}: {e}")
            time.sleep(2)

    print(f"Found {len(investment_leads)} potential investment leads.")
    return investment_leads


def draft_outreach_emails(leads: list, prompt_template: str) -> list:
    """
    Drafts personalized outreach emails for a list of investment leads.
    """
    print("Starting email drafting phase...")
    drafted_leads = []
    for i, lead_data in enumerate(leads):
        key = list(lead_data.keys())[0]
        lead_details = lead_data[key]
        print(f"Drafting email {i+1}/{len(leads)} for {lead_details.get('name', 'Unknown')}...")
        
        contents = prompt_template.replace("((lead_details))", str(lead_details))
        
        try:
            resp = client.models.generate_content(
                model=MODEL_NAME_DRAFT,
                contents=contents,
            )
            drafted_email = resp.text
            lead_details['investment_email_draft'] = drafted_email
            drafted_leads.append(lead_details)
            print("  - Draft created successfully.")
            
        except Exception as e:
            print(f"  - An error occurred while drafting email {i+1}: {e}")
            lead_details['investment_email_draft'] = f"Error generating draft: {e}"
            drafted_leads.append(lead_details)
            time.sleep(2)

    print("Email drafting phase complete.")
    return drafted_leads

def write_to_csv(data: list, filename: str):
    """
    Writes a list of dictionaries to a CSV file.
    """
    if not data:
        print("No data to write to CSV.")
        return

    print(f"Writing {len(data)} rows to {filename}...")
    # Flatten the data and get all possible fieldnames
    fieldnames = set()
    for row in data:
        fieldnames.update(row.keys())
    
    sorted_fieldnames = sorted(list(fieldnames))

    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=sorted_fieldnames)
        writer.writeheader()
        writer.writerows(data)
    print("Successfully wrote data to CSV.")


def main():
    """
    Main function to run the entire pipeline.
    """
    print("--- Starting Email Processing Pipeline ---")

    # Step 1: Read prompts from files
    try:
        with open(EMAIL_ANALYZER_PROMPT_FILE, "r", encoding="utf-8") as f:
            email_analyzer_prompt = f.read()
        with open(DRAFT_EMAIL_PROMPT_FILE, "r", encoding="utf-8") as f:
            draft_email_prompt = f.read()
    except FileNotFoundError as e:
        print(f"Error: Prompt file not found - {e}. Please create the prompt files.")
        sys.exit(1)

    # Step 2: Parse the mbox file
    filtered_emails = parse_mbox_file(MBOX_FILE)
    if not filtered_emails:
        print("No emails to process after filtering. Exiting.")
        return

    # Step 3: Sort emails by date (newest first). This is the key change.
    # The ISO date format (e.g., "2025-10-02T...") sorts correctly as a string.
    filtered_emails.sort(key=lambda x: x.get('date', ''), reverse=True)
    print(f"Sorted {len(filtered_emails)} emails by date.")

    # Step 4: Create a new list containing only the most recent email from each conversation
    latest_emails_from_conversations = []
    processed_domains = set()
    for email in filtered_emails:
        domain = email.get('sender_domain')
        if domain and domain not in processed_domains:
            latest_emails_from_conversations.append(email)
            processed_domains.add(domain)

    print(f"Identified {len(latest_emails_from_conversations)} unique conversations to analyze.")

    # Step 5: Analyze ONLY the latest emails and research potential investors
    investment_leads = analyze_and_research_emails(latest_emails_from_conversations, email_analyzer_prompt)
    if not investment_leads:
        print("No investment leads found after analysis. Exiting.")
        return

    # Step 6: Draft outreach emails for the identified leads
    final_data = draft_outreach_emails(investment_leads, draft_email_prompt)

    # Step 7: Write the final data to a CSV file
    write_to_csv(final_data, OUTPUT_CSV_FILE)

    print("--- Pipeline Finished Successfully ---")

if __name__ == "__main__":
    main()