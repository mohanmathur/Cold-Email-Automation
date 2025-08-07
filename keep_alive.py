from flask import Flask
from threading import Thread

app = Flask('')


@app.route('/')
def home():
    return "I'm alive"


def run():
    app.run(host='0.0.0.0', port=5000)


def keep_alive():
    t = Thread(target=run)
    t.start()
    return t  # ✅ This ensures the thread object is returned for management if needed
