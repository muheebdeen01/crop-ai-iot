CROP DISEASE IoT PROTOTYPE - HOW TO RUN

A) On your laptop (best for project defence, no internet needed)
   pip install flask requests
   python app.py                      -> open http://127.0.0.1:5000
   (other phones on the same Wi-Fi: HOST=0.0.0.0 python app.py, then open http://<laptop-ip>:5000)
   In a second window:  python simulate.py http://127.0.0.1:5000

B) Online with Render.com (free)
   1. Put this folder on GitHub (new repo, upload the files).
   2. Render > New > Web Service > connect the repo.
      Build command: pip install -r requirements.txt
      Start command: gunicorn app:app --workers 1 --threads 4
   3. Open the URL Render gives you.
   4. Fill it with data:  python simulate.py https://YOUR-APP.onrender.com
   Free plan sleeps when idle and its disk resets on restart, so re-run simulate.py before showing it.

C) Online with PythonAnywhere (free)
   Upload the files, Web tab > Add new web app > Flask (manual config), set the WSGI file to:
      import sys; sys.path.insert(0, '/home/YOURNAME/crop-ai-iot')
      from app import app as application
   Reload, then run simulate.py against your yourname.pythonanywhere.com address.

Do not set CROP_TEST online. Keep --workers 1 (SQLite).
