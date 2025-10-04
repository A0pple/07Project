# Wikipedia Red Link Scanner (Web)

This project converts the original Tkinter-based Wikipedia Red Link scanner into a modern web
application powered by Flask. It allows you to explore missing (red link) articles and discover
short existing articles directly from your browser while the heavy lifting happens on the server.

## Features

- Start/stop the red link scanner with adjustable request rate caps and optional category filters.
- Track red link results with per-type counts, live filtering, and quick links to Wikipedia, Google,
  and Bing searches.
- Find short existing articles under a configurable byte limit – great for spotting stubs that need
  improvement.
- Export the results of either scanner to CSV for offline review.

## Getting Started

1. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

2. **Run the development server**

   ```bash
   python app.py
   ```

   The application listens on <http://localhost:5000>. Open this URL in your browser to use the
   scanners.

3. **Usage tips**

   - Leave the category field blank to scan random articles.
   - Set the “Red links per page” control to `0` for no per-page limit.
   - Adjust the requests-per-minute cap to respect the MediaWiki API rate guidelines.
   - Use the filter boxes under each scanner to quickly search within the collected results.

## Project Structure

- `app.py` – Flask application with REST endpoints and worker management.
- `scanner_logic.py` – Core scanning, classification, caching, and threading utilities reused from
  the desktop version.
- `templates/index.html` – Main HTML template for the single-page UI.
- `static/css/style.css` & `static/js/app.js` – Front-end styling and behaviour.
- `requirements.txt` – Python dependencies.

## Notes

- All network calls use a shared rate limiter across both scanners to respect the configured RPM
  limit.
- Results and debug logs are capped in-memory to keep the UI responsive during long sessions.
