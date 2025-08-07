
import os
import json
import schedule
import threading
import time
from datetime import datetime, timedelta
import pytz

SETTINGS_FILE = "scheduler_settings.json"

DEFAULT_SETTINGS = {
    "schedule_time": "08:00",
    "followup_delay_type": "interval",
    "followup_delay_value": 3,
    "followup_delay_unit": "days",
    "followup_delay_time": "14:00",
    "max_followups": 2,
    "email_delay": 5,
    "scheduler_enabled": True,
    "timezone": "Asia/Kolkata"
}

class SchedulerManager:
    def __init__(self):
        self.settings = self.load_settings()
        self.scheduler_thread = None
        self.running = False
        
    def load_settings(self):
        """Load scheduler settings from file or create with defaults"""
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, 'r') as f:
                    settings = json.load(f)
                # Ensure all required keys exist
                for key, value in DEFAULT_SETTINGS.items():
                    if key not in settings:
                        settings[key] = value
                return settings
            except:
                pass
        return DEFAULT_SETTINGS.copy()
    
    def save_settings(self, new_settings):
        """Save scheduler settings to file"""
        self.settings.update(new_settings)
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(self.settings, f, indent=2)
        
        # Restart scheduler with new settings
        self.restart_scheduler()
    
    def get_settings(self):
        """Get current scheduler settings"""
        return self.settings.copy()
    
    def start_scheduler(self):
        """Start the scheduler thread"""
        if self.settings.get("scheduler_enabled", True):
            self.stop_scheduler()  # Stop existing if running
            self.running = True
            self.scheduler_thread = threading.Thread(target=self._run_scheduler, daemon=True)
            self.scheduler_thread.start()
            print(f"Scheduler started - emails will be sent daily at {self.settings['schedule_time']}")
    
    def stop_scheduler(self):
        """Stop the scheduler thread"""
        self.running = False
        schedule.clear()
        if self.scheduler_thread and self.scheduler_thread.is_alive():
            # Give thread time to finish
            time.sleep(1)
        print("Scheduler stopped")
    
    def restart_scheduler(self):
        """Restart scheduler with current settings"""
        self.stop_scheduler()
        time.sleep(1)  # Brief pause
        self.start_scheduler()
    
    def _run_scheduler(self):
        """Internal scheduler loop with proper IST timezone handling"""
        from email_automation import run_initial_email_automation, run_followup_email_automation
        
        # Clear existing schedules
        schedule.clear()
        
        timezone = self.settings.get("timezone", "Asia/Kolkata")
        ist_tz = pytz.timezone(timezone)
        
        def scheduled_initial_job():
            """Wrapper function for initial emails"""
            current_time = datetime.now(ist_tz)
            print(f"🚀 Running scheduled INITIAL email automation at {current_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            try:
                run_initial_email_automation()
                print(f"✅ Scheduled INITIAL email automation completed at {datetime.now(ist_tz).strftime('%Y-%m-%d %H:%M:%S %Z')}")
            except Exception as e:
                print(f"❌ Error in scheduled INITIAL email automation: {e}")
        
        def scheduled_followup_job():
            """Wrapper function for follow-up emails"""
            current_time = datetime.now(ist_tz)
            print(f"🚀 Running scheduled FOLLOW-UP email automation at {current_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            try:
                run_followup_email_automation()
                print(f"✅ Scheduled FOLLOW-UP email automation completed at {datetime.now(ist_tz).strftime('%Y-%m-%d %H:%M:%S %Z')}")
            except Exception as e:
                print(f"❌ Error in scheduled FOLLOW-UP email automation: {e}")
        
        # Schedule initial emails at the main scheduled time
        schedule.every().day.at(self.settings["schedule_time"]).do(scheduled_initial_job)
        
        # Schedule follow-up emails if time-based follow-up is enabled
        if self.settings.get("followup_delay_type") == "time":
            followup_time = self.settings.get("followup_delay_time", "14:00")
            schedule.every().day.at(followup_time).do(scheduled_followup_job)
            print(f"📧 Follow-up emails scheduled for {followup_time} daily (IST)")
        
        print(f"📅 Initial emails scheduled for {self.settings['schedule_time']} daily (IST)")
        current_time = datetime.now(ist_tz)
        print(f"🕒 Current IST time: {current_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        
        # Calculate next run time manually for IST (initial emails)
        schedule_hour, schedule_minute = map(int, self.settings["schedule_time"].split(":"))
        today_ist = current_time.replace(hour=schedule_hour, minute=schedule_minute, second=0, microsecond=0)
        
        if today_ist <= current_time:
            # If scheduled time has passed today, schedule for tomorrow
            next_run_ist = today_ist + timedelta(days=1)
        else:
            next_run_ist = today_ist
        
        time_until = (next_run_ist - current_time).total_seconds()
        hours_until = int(time_until // 3600)
        minutes_until = int((time_until % 3600) // 60)
        print(f"⏰ Next initial email run in {hours_until}h {minutes_until}m at {next_run_ist.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Calculate follow-up timing if time-based
        followup_hour = followup_minute = None
        if self.settings.get("followup_delay_type") == "time":
            followup_time = self.settings.get("followup_delay_time", "14:00")
            followup_hour, followup_minute = map(int, followup_time.split(":"))
            today_followup = current_time.replace(hour=followup_hour, minute=followup_minute, second=0, microsecond=0)
            
            if today_followup <= current_time:
                next_followup_ist = today_followup + timedelta(days=1)
            else:
                next_followup_ist = today_followup
            
            followup_time_until = (next_followup_ist - current_time).total_seconds()
            followup_hours_until = int(followup_time_until // 3600)
            followup_minutes_until = int((followup_time_until % 3600) // 60)
            print(f"📧 Next follow-up email run in {followup_hours_until}h {followup_minutes_until}m at {next_followup_ist.strftime('%Y-%m-%d %H:%M:%S')}")
        
        last_status_time = 0
        last_check = 0
        
        while self.running:
            try:
                current_time = datetime.now(ist_tz)
                current_timestamp = current_time.timestamp()
                
                # Check if it's time to run jobs manually (every minute for precision)
                if current_timestamp - last_check >= 60:  # Check every minute
                    current_hour = current_time.hour
                    current_minute = current_time.minute
                    
                    # Check for initial email time
                    if current_hour == schedule_hour and current_minute == schedule_minute:
                        print(f"⏰ Initial email scheduled time reached! Triggering initial email automation...")
                        scheduled_initial_job()
                    
                    # Check for follow-up email time if enabled
                    if (followup_hour is not None and followup_minute is not None and 
                        current_hour == followup_hour and current_minute == followup_minute):
                        print(f"📧 Follow-up scheduled time reached! Triggering follow-up email automation...")
                        scheduled_followup_job()
                    
                    last_check = current_timestamp
                
                # Show status every 5 minutes (300 seconds)
                if current_timestamp - last_status_time >= 300:
                    # Recalculate next run time for initial emails
                    today_ist = current_time.replace(hour=schedule_hour, minute=schedule_minute, second=0, microsecond=0)
                    
                    if today_ist <= current_time:
                        next_run_ist = today_ist + timedelta(days=1)
                    else:
                        next_run_ist = today_ist
                    
                    time_until = (next_run_ist - current_time).total_seconds()
                    
                    if time_until > 0:
                        hours_until = int(time_until // 3600)
                        minutes_until = int((time_until % 3600) // 60)
                        print(f"⏳ Next initial email automation in {hours_until}h {minutes_until}m (at {next_run_ist.strftime('%H:%M:%S')})")
                    
                    # Show follow-up timing if enabled
                    if followup_hour is not None and followup_minute is not None:
                        today_followup = current_time.replace(hour=followup_hour, minute=followup_minute, second=0, microsecond=0)
                        
                        if today_followup <= current_time:
                            next_followup_ist = today_followup + timedelta(days=1)
                        else:
                            next_followup_ist = today_followup
                        
                        followup_time_until = (next_followup_ist - current_time).total_seconds()
                        
                        if followup_time_until > 0:
                            followup_hours_until = int(followup_time_until // 3600)
                            followup_minutes_until = int((followup_time_until % 3600) // 60)
                            print(f"📧 Next follow-up email automation in {followup_hours_until}h {followup_minutes_until}m (at {next_followup_ist.strftime('%H:%M:%S')})")
                        
                    last_status_time = current_timestamp
                
                # Also run schedule.run_pending() as backup
                schedule.run_pending()
                time.sleep(10)  # Check every 10 seconds for better precision
                
            except Exception as e:
                print(f"❌ Scheduler error: {e}")
                time.sleep(60)

# Global scheduler manager instance
scheduler_manager = SchedulerManager()
