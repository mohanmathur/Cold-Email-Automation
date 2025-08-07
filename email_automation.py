import sqlite3
import smtplib
import imaplib
import email
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import time
import pytz
from email.header import decode_header


def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect('contacts.db')
    conn.row_factory = sqlite3.Row
    return conn


def get_email_credentials():
    """Get email credentials from environment variables"""
    return {
        'smtp_server': os.getenv('SMTP_SERVER', 'smtp.gmail.com'),
        'smtp_port': int(os.getenv('SMTP_PORT', 587)),
        'imap_server': os.getenv('IMAP_SERVER', 'imap.gmail.com'),
        'imap_port': int(os.getenv('IMAP_PORT', 993)),
        'email': os.getenv('EMAIL_ADDRESS'),
        'password': os.getenv('EMAIL_PASSWORD'),
        'manager_emails':
        os.getenv('MANAGER_EMAIL',
                  '').split(',')  # Split by comma and allow multiple
    }


def send_email(to_email, subject, body, sender_name="Test"):
    """Send an email"""
    credentials = get_email_credentials()

    if not credentials['email'] or not credentials['password']:
        print("Email credentials not configured")
        return False

    try:
        msg = MIMEMultipart()
        msg['From'] = f"{sender_name} <{credentials['email']}>"
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        with smtplib.SMTP(credentials['smtp_server'],
                          credentials['smtp_port']) as server:
            server.starttls()
            server.login(credentials['email'], credentials['password'])
            server.send_message(msg)

        print(f"Email sent successfully to {to_email}")
        return True
    except Exception as e:
        print(f"Failed to send email to {to_email}: {e}")
        return False


def send_initial_emails():
    """Send initial emails to contacts who haven't been contacted yet"""
    conn = get_db_connection()

    # Get template
    try:
        with open('initial_email.txt', 'r') as f:
            template = f.read()
    except:
        template = "Subject: Initial Contact\n\nHi {name},\n\nThis is an initial contact email.\n\nBest regards,\nTest"

    # Extract subject and body from template
    if template.startswith('Subject:'):
        lines = template.split('\n', 2)
        subject = lines[0].replace('Subject:', '').strip()
        body_template = lines[2] if len(lines) > 2 else lines[1]
    else:
        subject = "Initial Contact"
        body_template = template

    # Get contacts who haven't received initial emails
    contacts = conn.execute(
        'SELECT * FROM contacts WHERE initial_sent_date IS NULL').fetchall()

    sent_count = 0
    for contact in contacts:
        name = contact['name'] or 'there'
        body = body_template.format(name=name)
        email_subject = subject.format(name=name)

        if send_email(contact['email'], email_subject, body):
            # Update database
            conn.execute(
                'UPDATE contacts SET initial_sent_date = ? WHERE id = ?',
                (datetime.now().isoformat(), contact['id']))
            conn.commit()
            sent_count += 1

            # Add delay between emails
            time.sleep(5)

    conn.close()
    print(f"Sent {sent_count} initial emails")
    return sent_count


def send_followups():
    """Send follow-up emails based on settings"""
    from scheduler_settings import scheduler_manager

    conn = get_db_connection()
    settings = scheduler_manager.get_settings()

    # Get template
    try:
        with open('followup_email.txt', 'r') as f:
            template = f.read()
    except:
        template = "Subject: Follow-up\n\nHi {name},\n\nFollowing up on my previous email.\n\nBest regards,\nTest"

    # Extract subject and body from template
    if template.startswith('Subject:'):
        lines = template.split('\n', 2)
        subject = lines[0].replace('Subject:', '').strip()
        body_template = lines[2] if len(lines) > 2 else lines[1]
    else:
        subject = "Follow-up"
        body_template = template

    ist_tz = pytz.timezone('Asia/Kolkata')
    current_time = datetime.now(ist_tz)

    # Get contacts ready for follow-up
    max_followups = settings.get('max_followups', 2)
    followup_delay = settings.get('followup_delay_value', 3)
    followup_unit = settings.get('followup_delay_unit', 'days')

    # Calculate delay in days
    if followup_unit == 'hours':
        delay_days = followup_delay / 24
    elif followup_unit == 'minutes':
        delay_days = followup_delay / (24 * 60)
    else:  # days
        delay_days = followup_delay

    contacts = conn.execute(
        '''
        SELECT * FROM contacts 
        WHERE replied = 0 
        AND initial_sent_date IS NOT NULL 
        AND followup_sent_count < ?
    ''', (max_followups, )).fetchall()

    sent_count = 0
    for contact in contacts:
        # Check if enough time has passed since last contact
        last_contact_date = contact['last_followup_date'] or contact[
            'initial_sent_date']
        if last_contact_date:
            last_date = datetime.fromisoformat(last_contact_date)
            if last_date.tzinfo is None:
                last_date = ist_tz.localize(last_date)

            # For interval-based follow-ups, check if enough time has passed
            if settings.get('followup_delay_type') == 'interval':
                time_diff = (current_time - last_date
                             ).total_seconds() / 86400  # Convert to days
                if time_diff < delay_days:
                    print(
                        f"Skipping {contact['email']} - only {time_diff:.1f} days since last contact, need {delay_days} days"
                    )
                    continue
            else:
                # For time-based follow-ups, this function should only run at the scheduled time
                # so we don't need additional time checks here
                pass

        name = contact['name'] or 'there'
        body = body_template.format(name=name)
        email_subject = subject.format(name=name)

        if send_email(contact['email'], email_subject, body):
            # Update database
            new_count = (contact['followup_sent_count'] or 0) + 1
            conn.execute(
                '''
                UPDATE contacts 
                SET followup_sent_count = ?, last_followup_date = ?
                WHERE id = ?
            ''', (new_count, current_time.isoformat(), contact['id']))
            conn.commit()
            sent_count += 1

            # Add recent activity
            conn.execute(
                '''
                INSERT INTO recent_activity (contact_email, action, timestamp)
                VALUES (?, ?, ?)
            ''', (contact['email'], f'Follow-up #{new_count} sent',
                  current_time.isoformat()))
            conn.commit()

            time.sleep(settings.get('email_delay', 5))

    conn.close()
    print(f"Sent {sent_count} follow-up emails")
    return sent_count


def check_replies_and_forward(silent_mode=False):
    """Check for replies and forward them to manager"""
    credentials = get_email_credentials()

    if not credentials['email'] or not credentials[
            'password'] or not credentials['manager_emails']:
        if not silent_mode:
            print("Email credentials or manager emails not configured")
            print(
                f"Email: {bool(credentials['email'])}, Password: {bool(credentials['password'])}, Manager Emails: {bool(credentials['manager_emails'])}"
            )
        return

    try:
        if not silent_mode:
            print(
                f"Connecting to IMAP server: {credentials['imap_server']}:{credentials['imap_port']}"
            )
        # Connect to IMAP
        with imaplib.IMAP4_SSL(credentials['imap_server'],
                               credentials['imap_port']) as mail:
            mail.login(credentials['email'], credentials['password'])
            mail.select('inbox')

            # Search for unread emails
            status, messages = mail.search(None, 'UNSEEN')

            if not messages[0]:
                if not silent_mode:
                    print("No unread emails found")
                return

            conn = get_db_connection()
            replies_found = 0

            for msg_id in messages[0].split():
                try:
                    status, msg_data = mail.fetch(msg_id, '(RFC822)')
                    email_body = msg_data[0][1]
                    email_message = email.message_from_bytes(email_body)

                    # Get sender email
                    sender = email_message['From']
                    subject = email_message['Subject'] or "No Subject"

                    # Decode header if needed
                    if sender:
                        decoded_sender = decode_header(sender)[0]
                        if isinstance(decoded_sender[0], bytes):
                            sender = decoded_sender[0].decode(decoded_sender[1]
                                                              or 'utf-8')

                    # Extract email address from sender
                    sender_email = sender
                    if '<' in sender and '>' in sender:
                        sender_email = sender.split('<')[1].split(
                            '>')[0].strip()
                    elif ' ' in sender:
                        # Handle cases like "name@email.com"
                        parts = sender.split()
                        for part in parts:
                            if '@' in part:
                                sender_email = part.strip()
                                break

                    print(f"Processing email from: {sender_email}")

                    # Check if this is from one of our contacts
                    contact = conn.execute(
                        'SELECT * FROM contacts WHERE email = ?',
                        (sender_email, )).fetchone()

                    if contact:
                        print(
                            f"Found contact: {contact['name']} ({contact['email']})"
                        )

                        # Mark as replied if not already marked
                        if not contact['replied']:
                            conn.execute(
                                'UPDATE contacts SET replied = 1, reply_date = ? WHERE email = ?',
                                (datetime.now().isoformat(), sender_email))

                            # Add to recent activity
                            conn.execute(
                                '''
                                INSERT INTO recent_activity (contact_email, action, timestamp)
                                VALUES (?, ?, ?)
                            ''', (sender_email, 'Reply received',
                                  datetime.now().isoformat()))

                            conn.commit()
                            print(
                                f"Marked {sender_email} as replied in database"
                            )

                        # Get email body
                        body = ""
                        if email_message.is_multipart():
                            for part in email_message.walk():
                                if part.get_content_type() == "text/plain":
                                    payload = part.get_payload(decode=True)
                                    if payload:
                                        try:
                                            body = payload.decode('utf-8')
                                        except UnicodeDecodeError:
                                            body = payload.decode(
                                                'utf-8', errors='ignore')
                                    break
                        else:
                            payload = email_message.get_payload(decode=True)
                            if payload:
                                try:
                                    body = payload.decode('utf-8')
                                except UnicodeDecodeError:
                                    body = payload.decode('utf-8',
                                                          errors='ignore')

                        # Forward to manager
                        forward_subject = f"Reply from {contact['name'] or sender_email}: {subject}"

                        forward_body = f"""Reply received from contact in your email campaign:

Contact Name: {contact['name'] or 'Unknown'}
Contact Email: {sender_email}
Original Subject: {subject}
Reply Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

--- Reply Content ---
{body}

--- End of Reply ---

This is an automated forwarded message from your email campaign system.
"""
                        # Send to all manager emails
                        for manager_email in credentials['manager_emails']:
                            if send_email(manager_email, forward_subject,
                                          forward_body,
                                          "Email Campaign System"):
                                timestamp = datetime.now().strftime(
                                    '%Y-%m-%d %H:%M:%S')
                                print(
                                    f"✓ [{timestamp}] Reply from {sender_email} forwarded to manager ({manager_email})"
                                )
                                replies_found += 1
                            else:
                                print(
                                    f"✗ Failed to forward reply from {sender_email} to manager ({manager_email})"
                                )

                    else:
                        print(
                            f"Email from {sender_email} is not from a known contact"
                        )

                except Exception as e:
                    print(f"Error processing individual email: {e}")
                    continue

            conn.close()
            print(
                f"Processed {replies_found} replies and forwarded to manager")

    except Exception as e:
        print(f"Error checking replies: {e}")
        import traceback
        traceback.print_exc()


def run_initial_email_automation():
    """Main automation function for initial emails only"""
    print("=" * 50)
    print("Starting INITIAL email automation...")
    print(f"Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    try:
        credentials = get_email_credentials()
        print(f"Email configured: {bool(credentials['email'])}")
        print(f"Password configured: {bool(credentials['password'])}")
        print(
            f"Manager emails configured: {len(credentials['manager_emails'])} email(s)"
        )

        # Check for replies first
        print("\n1. Checking for replies...")
        check_replies_and_forward()

        # Send initial emails only
        print("\n2. Sending initial emails...")
        initial_count = send_initial_emails()

        print(
            f"\n✓ Initial email automation completed. Initial emails sent: {initial_count}"
        )
        print("=" * 50)

    except Exception as e:
        print(f"✗ Error in initial email automation: {e}")
        import traceback
        traceback.print_exc()
        print("=" * 50)


def run_followup_email_automation():
    """Automation function for follow-up emails only"""
    print("=" * 50)
    print("Starting FOLLOW-UP email automation...")
    print(f"Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    try:
        credentials = get_email_credentials()
        print(f"Email configured: {bool(credentials['email'])}")
        print(f"Password configured: {bool(credentials['password'])}")
        print(
            f"Manager emails configured: {len(credentials['manager_emails'])} email(s)"
        )

        # Check for replies first
        print("\n1. Checking for replies...")
        check_replies_and_forward()

        # Send follow-ups only
        print("\n2. Sending follow-up emails...")
        followup_count = send_followups()

        print(
            f"\n✓ Follow-up email automation completed. Follow-up emails sent: {followup_count}"
        )
        print("=" * 50)

    except Exception as e:
        print(f"✗ Error in follow-up email automation: {e}")
        import traceback
        traceback.print_exc()
        print("=" * 50)


def run_email_automation():
    """Legacy function - now only runs initial emails"""
    run_initial_email_automation()


def start_continuous_reply_monitoring():
    """Start continuous monitoring for email replies in a separate thread"""
    import threading

    def monitor_replies():
        """Continuously monitor for replies every 30 seconds"""
        print("Starting continuous reply monitoring...")
        while True:
            try:
                check_replies_and_forward(silent_mode=True)
                time.sleep(30)  # Check every 30 seconds
            except Exception as e:
                print(f"Error in continuous reply monitoring: {e}")
                time.sleep(60)  # Wait longer on error

    monitor_thread = threading.Thread(target=monitor_replies, daemon=True)
    monitor_thread.start()
    return monitor_thread


if __name__ == "__main__":
    run_email_automation()
