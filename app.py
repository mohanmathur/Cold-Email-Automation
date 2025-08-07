from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
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
import threading
import schedule
from werkzeug.utils import secure_filename
from scheduler_settings import scheduler_manager

# Load credentials
load_dotenv()
EMAIL = os.getenv("EMAIL_ADDRESS")
APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")
MANAGER_EMAIL = os.getenv("MANAGER_EMAIL")

DB_FILE = "contacts.db"
UPLOAD_FOLDER = "uploads"
ALLOWED_EXTENSIONS = {'csv'}

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "your-secret-key-here")
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Ensure upload folder exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Timing config
FOLLOWUP_DELAY_DAYS = 3
MAX_FOLLOWUPS = 2

# Initialize database
def init_db():
    conn = sqlite3.connect('contacts.db')
    cur = conn.cursor() # Use cursor for executing statements
    cur.execute('''
    CREATE TABLE IF NOT EXISTS contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE,
        name TEXT,
        initial_sent_date TEXT,
        followup_sent_count INTEGER DEFAULT 0,
        last_activity_date TEXT,
        replied INTEGER DEFAULT 0,
        custom_followup_time TEXT
    )
    ''')

    # Add missing columns if they don't exist
    try:
        cur.execute("ALTER TABLE contacts ADD COLUMN custom_followup_time TEXT")
        print("Added custom_followup_time column")
    except sqlite3.OperationalError:
        pass
    
    try:
        cur.execute("ALTER TABLE contacts ADD COLUMN reply_date TEXT")
        print("Added reply_date column")
    except sqlite3.OperationalError:
        pass
        
    try:
        cur.execute("ALTER TABLE contacts ADD COLUMN last_followup_date TEXT")
        print("Added last_followup_date column")
    except sqlite3.OperationalError:
        pass

    cur.execute('''
    CREATE TABLE IF NOT EXISTS log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contact_id INTEGER,
        action TEXT,
        date TEXT,
        details TEXT
    )
    ''')

    # Create recent activity table
    cur.execute('''
        CREATE TABLE IF NOT EXISTS recent_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_email TEXT NOT NULL,
            action TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def dashboard():
    conn = get_db_connection()

    # Get recent contacts
    contacts = conn.execute('''
        SELECT * FROM contacts 
        ORDER BY id DESC 
        LIMIT 10
    ''').fetchall()

    # Get recent logs (assuming log table is used for activity)
    # If recent_activity table is the primary source for dashboard activities, use that.
    # For now, let's get recent logs as a fallback if recent_activity is not yet populated widely.
    recent_logs = conn.execute('''
        SELECT l.id, l.contact_id, l.action, l.date, l.details, c.email, c.name 
        FROM log l 
        LEFT JOIN contacts c ON l.contact_id = c.id 
        ORDER BY l.date DESC 
        LIMIT 10
    ''').fetchall()

    # Get recent activities from the new table
    recent_activities = conn.execute('''
        SELECT * FROM recent_activity 
        ORDER BY timestamp DESC 
        LIMIT 10
    ''').fetchall()

    # Get stats
    scheduler_settings = scheduler_manager.get_settings()
    stats = {
        'total_contacts': conn.execute('SELECT COUNT(*) FROM contacts').fetchone()[0],
        'sent_initial': conn.execute('SELECT COUNT(*) FROM contacts WHERE initial_sent_date IS NOT NULL').fetchone()[0],
        'replied': conn.execute('SELECT COUNT(*) FROM contacts WHERE replied = 1').fetchone()[0],
        'pending_followup': conn.execute('SELECT COUNT(*) FROM contacts WHERE replied = 0 AND initial_sent_date IS NOT NULL AND followup_sent_count < ?', (scheduler_settings['max_followups'],)).fetchone()[0]
    }

    conn.close()

    return render_template('dashboard.html', stats=stats, contacts=contacts, logs=recent_logs, recent_activities=recent_activities)

@app.route('/contacts')
def contacts():
    conn = get_db_connection()
    all_contacts = conn.execute('SELECT * FROM contacts ORDER BY id DESC').fetchall()
    conn.close()
    return render_template('contacts.html', contacts=all_contacts)

@app.route('/upload', methods=['GET', 'POST'])
def upload_contacts():
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file selected')
            return redirect(request.url)

        file = request.files['file']
        if file.filename == '':
            flash('No file selected')
            return redirect(request.url)

        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)

            # Import contacts from CSV
            imported = import_contacts_from_csv(file_path)
            flash(f'Successfully imported {imported} contacts')

            # Clean up uploaded file
            os.remove(file_path)

            return redirect(url_for('contacts'))
        else:
            flash('Invalid file type. Please upload a CSV file.')

    return render_template('upload.html')

def import_contacts_from_csv(file_path):
    conn = get_db_connection()
    imported_count = 0

    with open(file_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            email_addr = row.get("email", "").strip()
            name = row.get("name", "").strip()

            if email_addr:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO contacts (email, name) VALUES (?, ?)",
                        (email_addr, name)
                    )
                    imported_count += 1
                except Exception as e:
                    print(f"Error inserting contact {email_addr}: {e}")

    conn.commit()
    conn.close()
    return imported_count

@app.route('/send_initial', methods=['POST'])
def send_initial():
    from email_automation import send_initial_emails
    try:
        print("Starting initial email process...")
        send_initial_emails()
        flash('Initial emails process completed! Check console for details.')
    except Exception as e:
        print(f"Error in send_initial: {e}")
        flash(f'Error sending emails: {str(e)}')
    return redirect(url_for('dashboard'))

@app.route('/send_followups', methods=['POST'])
def send_followups():
    from email_automation import send_followups as send_followup_emails
    try:
        print("Starting follow-up email process...")
        send_followup_emails()
        flash('Follow-up emails process completed! Check console for details.')
    except Exception as e:
        print(f"Error in send_followups: {e}")
        flash(f'Error sending follow-ups: {str(e)}')
    return redirect(url_for('dashboard'))

@app.route('/check_replies', methods=['POST'])
def check_replies():
    from email_automation import check_replies_and_forward
    try:
        check_replies_and_forward(silent_mode=False)
        flash('Replies checked and forwarded!')
    except Exception as e:
        flash(f'Error checking replies: {str(e)}')
    return redirect(url_for('dashboard'))

@app.route('/templates')
def email_templates():
    # Read current templates
    try:
        with open('initial_email.txt', 'r') as f:
            initial_template = f.read()
    except:
        initial_template = ""

    try:
        with open('followup_email.txt', 'r') as f:
            followup_template = f.read()
    except:
        followup_template = ""

    return render_template('templates.html', 
                         initial_template=initial_template, 
                         followup_template=followup_template)

@app.route('/save_templates', methods=['POST'])
def save_templates():
    initial_template = request.form.get('initial_template', '')
    followup_template = request.form.get('followup_template', '')

    try:
        with open('initial_email.txt', 'w') as f:
            f.write(initial_template)

        with open('followup_email.txt', 'w') as f:
            f.write(followup_template)

        flash('Email templates saved successfully!')
    except Exception as e:
        flash(f'Error saving templates: {str(e)}')

    return redirect(url_for('email_templates'))

@app.route('/settings')
def settings():
    """Settings page for scheduler and environment variables"""
    scheduler_settings = scheduler_manager.get_settings()

    # Load environment variables
    env_settings = {
        'EMAIL_ADDRESS': os.getenv('EMAIL_ADDRESS', ''),
        'EMAIL_APP_PASSWORD': os.getenv('EMAIL_APP_PASSWORD', ''),
        'MANAGER_EMAIL': os.getenv('MANAGER_EMAIL', '')
    }

    return render_template('settings.html', 
                         scheduler_settings=scheduler_settings,
                         env_settings=env_settings)

@app.route('/save_scheduler_settings', methods=['POST'])
def save_scheduler_settings():
    """Save scheduler configuration"""
    try:
        new_settings = {
            'schedule_time': request.form.get('schedule_time'),
            'followup_delay_type': request.form.get('followup_delay_type'),
            'max_followups': int(request.form.get('max_followups')),
            'email_delay': int(request.form.get('email_delay')),
            'scheduler_enabled': 'scheduler_enabled' in request.form,
            'timezone': 'Asia/Kolkata'
        }

        # Handle follow-up delay settings based on type
        if request.form.get('followup_delay_type') == 'time':
            new_settings['followup_delay_time'] = request.form.get('followup_delay_time')
        else:
            new_settings['followup_delay_value'] = int(request.form.get('followup_delay_value'))
            new_settings['followup_delay_unit'] = request.form.get('followup_delay_unit')

        scheduler_manager.save_settings(new_settings)
        flash('Scheduler settings saved successfully!')
    except Exception as e:
        flash(f'Error saving scheduler settings: {str(e)}')

    return redirect(url_for('settings'))

@app.route('/save_env_settings', methods=['POST'])
def save_env_settings():
    """Save environment variables to .env file"""
    try:
        email_address = request.form.get('email_address')
        app_password = request.form.get('email_app_password')
        manager_email = request.form.get('manager_email')

        # Read current .env file
        env_lines = []
        if os.path.exists('.env'):
            with open('.env', 'r') as f:
                env_lines = f.readlines()

        # Update or add environment variables
        env_vars = {
            'EMAIL_ADDRESS': email_address,
            'EMAIL_APP_PASSWORD': app_password,
            'MANAGER_EMAIL': manager_email
        }

        # Create new env content
        new_env_lines = []
        updated_vars = set()

        for line in env_lines:
            if '=' in line:
                key = line.split('=')[0].strip()
                if key in env_vars:
                    new_env_lines.append(f"{key}={env_vars[key]}\n")
                    updated_vars.add(key)
                else:
                    new_env_lines.append(line)
            else:
                new_env_lines.append(line)

        # Add any new variables that weren't updated
        for key, value in env_vars.items():
            if key not in updated_vars:
                new_env_lines.append(f"{key}={value}\n")

        # Write back to .env file
        with open('.env', 'w') as f:
            f.writelines(new_env_lines)

        # Update environment variables in current process
        for key, value in env_vars.items():
            os.environ[key] = value

        flash('Environment settings saved successfully! Restart the application to apply all changes.')
    except Exception as e:
        flash(f'Error saving environment settings: {str(e)}')

    return redirect(url_for('settings'))

@app.route('/update_custom_time', methods=['POST'])
def update_custom_time():
    try:
        data = request.get_json()
        contact_id = data.get('contact_id')
        custom_time = data.get('custom_time')

        conn = get_db_connection()
        conn.execute(
            'UPDATE contacts SET custom_followup_time = ? WHERE id = ?',
            (custom_time, contact_id)
        )
        conn.commit()
        conn.close()

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/stats')
def api_stats():
    conn = get_db_connection()
    scheduler_settings = scheduler_manager.get_settings()
    stats = {
        'total_contacts': conn.execute('SELECT COUNT(*) FROM contacts').fetchone()[0],
        'sent_initial': conn.execute('SELECT COUNT(*) FROM contacts WHERE initial_sent_date IS NOT NULL').fetchone()[0],
        'replied': conn.execute('SELECT COUNT(*) FROM contacts WHERE replied = 1').fetchone()[0],
        'pending_followup': conn.execute('SELECT COUNT(*) FROM contacts WHERE replied = 0 AND initial_sent_date IS NOT NULL AND followup_sent_count < ?', (scheduler_settings['max_followups'],)).fetchone()[0]
    }
    conn.close()
    return jsonify(stats)



if __name__ == '__main__':
    init_db()
    
    # Check if running in deployment environment
    is_deployment = os.environ.get('REPLIT_DEPLOYMENT') == '1'
    port = int(os.environ.get('PORT', 5000))
    
    if not is_deployment:
        # Development mode - only start scheduler and monitoring in main process (not in debugger reloader)
        if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
            print("Starting email automation services...")
            scheduler_manager.start_scheduler()
            
            from email_automation import start_continuous_reply_monitoring
            start_continuous_reply_monitoring()
            print("Continuous reply monitoring started - replies will be forwarded automatically")
        
        app.run(host='0.0.0.0', port=port, debug=True, use_reloader=True)
    else:
        # Production deployment mode
        print("Starting email automation services...")
        scheduler_manager.start_scheduler()
        
        from email_automation import start_continuous_reply_monitoring
        start_continuous_reply_monitoring()
        print("Continuous reply monitoring started - replies will be forwarded automatically")
        
        app.run(host='0.0.0.0', port=port, debug=False)