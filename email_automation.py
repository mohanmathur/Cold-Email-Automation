import os
import sqlite3
import yagmail
import csv
import datetime
import time
import imaplib
import email
from email.header import decode_header
from dotenv import load_dotenv

# Load credentials
load_dotenv()
EMAIL = os.getenv("EMAIL_ADDRESS")
APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")
MANAGER_EMAIL = os.getenv("MANAGER_EMAIL")

DB_FILE = "contacts.db"
NEW_CONTACTS_CSV = "new_contacts.csv"

# Timing config
FOLLOWUP_DELAY_DAYS = 3  # if no reply in 3 days, send follow-up
MAX_FOLLOWUPS = 2  # number of follow-ups after initial

# Initialize or connect DB
conn = sqlite3.connect(DB_FILE)
cur = conn.cursor()

# Create tables if not exist
cur.execute("""
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE,
    name TEXT,
    initial_sent_date TEXT,
    followup_sent_count INTEGER DEFAULT 0,
    last_activity_date TEXT,
    replied INTEGER DEFAULT 0
)
""")
cur.execute("""
CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER,
    action TEXT,
    date TEXT,
    details TEXT
)
""")
conn.commit()

def log_action(contact_id, action, details=""):
    now = datetime.datetime.utcnow().isoformat()
    cur.execute("INSERT INTO log (contact_id, action, date, details) VALUES (?, ?, ?, ?)",
                (contact_id, action, now, details))
    conn.commit()

def import_new_contacts():
    if not os.path.exists(NEW_CONTACTS_CSV):
        print(f"{NEW_CONTACTS_CSV} not found. Skipping import.")
        return
    with open(NEW_CONTACTS_CSV, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            email_addr = row["email"].strip()
            name = row.get("name", "").strip()
            try:
                cur.execute("INSERT OR IGNORE INTO contacts (email, name) VALUES (?, ?)", (email_addr, name))
            except Exception as e:
                print("Error inserting contact", email_addr, e)
    conn.commit()

def read_template(filename):
    with open(filename, encoding='utf-8') as f:
        return f.read()

def send_email(to_email, subject, body):
    yag = yagmail.SMTP(EMAIL, APP_PASSWORD)
    yag.send(to=to_email, subject=subject, contents=body)

def send_initial_emails():
    template = read_template("initial_email.txt")
    cur.execute("SELECT id, email, name FROM contacts WHERE initial_sent_date IS NULL")
    rows = cur.fetchall()
    for cid, email_addr, name in rows:
        subject_line = f"Hi {name or ''}, quick question for you [#{cid}]"
        body = template.format(name=name or "", id=cid)
        try:
            send_email(email_addr, subject_line, body)
            now = datetime.datetime.utcnow().isoformat()
            cur.execute("UPDATE contacts SET initial_sent_date=?, last_activity_date=? WHERE id=?",
                        (now, now, cid))
            log_action(cid, "initial_sent", f"Subject: {subject_line}")
            print(f"Initial email sent to {email_addr}")
            time.sleep(5)  # small delay to avoid rate limits
        except Exception as e:
            print(f"Failed to send initial email to {email_addr}:", e)
    conn.commit()

def send_followups():
    template = read_template("followup_email.txt")
    threshold = datetime.datetime.utcnow() - datetime.timedelta(days=FOLLOWUP_DELAY_DAYS)
    cur.execute("""
        SELECT id, email, name, initial_sent_date, followup_sent_count 
        FROM contacts 
        WHERE replied=0 AND initial_sent_date IS NOT NULL AND followup_sent_count < ?
    """, (MAX_FOLLOWUPS,))
    rows = cur.fetchall()
    for cid, email_addr, name, initial_date_str, followup_count in rows:
        try:
            initial_date = datetime.datetime.fromisoformat(initial_date_str)
        except:
            continue
        # Only send follow-up if enough days passed since last activity
        if initial_date + datetime.timedelta(days=FOLLOWUP_DELAY_DAYS * (followup_count + 1)) > datetime.datetime.utcnow():
            continue
        subject_line = f"Just following up, {name or ''} [#{cid}]"
        body = template.format(name=name or "", id=cid)
        try:
            send_email(email_addr, subject_line, body)
            now = datetime.datetime.utcnow().isoformat()
            cur.execute("UPDATE contacts SET followup_sent_count=followup_sent_count+1, last_activity_date=? WHERE id=?",
                        (now, cid))
            log_action(cid, "followup_sent", f"Count: {followup_count+1} Subject: {subject_line}")
            print(f"Follow-up email sent to {email_addr} (#{followup_count+1})")
            time.sleep(5)
        except Exception as e:
            print(f"Failed to send follow-up to {email_addr}:", e)
    conn.commit()

def check_replies_and_forward():
    # Connect to IMAP
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(EMAIL, APP_PASSWORD)
    imap.select("INBOX")
    # Fetch contacts that are pending reply
    cur.execute("SELECT id, email FROM contacts WHERE replied=0 AND initial_sent_date IS NOT NULL")
    pending = cur.fetchall()
    for cid, email_addr in pending:
        # Search unseen messages from that email with the contact tag in subject
        typ, msgnums = imap.search(None,
                                   '(UNSEEN FROM "{}" SUBJECT "#{cid}")'.format(email_addr).replace("#{cid}", f"#{cid}"))
        if typ != "OK":
            continue
        for num in msgnums[0].split():
            typ2, data = imap.fetch(num, "(RFC822)")
            if typ2 != "OK":
                continue
            raw = data[0][1]
            msg = email.message_from_bytes(raw)
            subject = decode_header(msg.get("Subject"))[0][0]
            if isinstance(subject, bytes):
                try:
                    subject = subject.decode()
                except:
                    subject = subject.decode("utf-8", errors="ignore")
            # Mark replied
            now = datetime.datetime.utcnow().isoformat()
            cur.execute("UPDATE contacts SET replied=1, last_activity_date=? WHERE id=?", (now, cid))
            log_action(cid, "reply_received", f"Subject: {subject}")
            print(f"Reply found from {email_addr}, forwarding to manager.")

            # Build forward body
            body_lines = [
                f"Forwarding a reply from {email_addr}",
                f"Original subject: {subject}",
                "------ Message content below ------",
            ]
            # Extract human-readable body
            content = ""
            if msg.is_multipart():
                for part in msg.walk():
                    ctype = part.get_content_type()
                    disp = str(part.get("Content-Disposition"))
                    if ctype == "text/plain" and "attachment" not in disp:
                        try:
                            content = part.get_payload(decode=True).decode()
                        except:
                            content = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                        break
            else:
                try:
                    content = msg.get_payload(decode=True).decode()
                except:
                    content = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
            body_lines.append(content)
            body = "\n".join(body_lines)
            forward_subject = f"FWD: Reply from {email_addr} — {subject}"
            try:
                send_email(MANAGER_EMAIL, forward_subject, body)
                log_action(cid, "forwarded_to_manager", f"Forwarded subject: {forward_subject}")
            except Exception as e:
                print("Failed to forward reply:", e)
            # Mark this email seen so not reprocessed
            imap.store(num, '+FLAGS', '\\Seen')
    conn.commit()
    imap.logout()

def main():
    import_new_contacts()
    send_initial_emails()
    send_followups()
    check_replies_and_forward()
    print("Run complete.")

if __name__ == "__main__":
    main()
