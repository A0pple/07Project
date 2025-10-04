"""Flask powered web interface for the Wikipedia Red Link scanner."""

from __future__ import annotations

import csv
import io
import queue
import threading
import time
from typing import Dict, List, Optional

from flask import Flask, jsonify, render_template, request, send_file

from scanner_logic import (
    CATEGORY_DISPLAY_ORDER,
    DEFAULT_RPM,
    RedLinkScanner,
    ScannerState,
    ShortArticleScanner,
    normalize_category_label,
)


class RedLinkController:
    """Manage the lifecycle and state of the red link scanner worker."""

    def __init__(self) -> None:
        self.state = ScannerState()
        self._rpm = DEFAULT_RPM
        self._category = ""
        self._links_per_page = 25
        self._seen_pairs: set[str] = set()

    # --------------------------- lifecycle --------------------------- #

    def start(self, rpm: float, category: str, links_per_page: int) -> Optional[str]:
        if self.state.running:
            return "Scanner is already running."

        self._rpm = max(1.0, float(rpm))
        self._category = str(category or "")
        self._links_per_page = max(0, int(links_per_page))

        self.state.stop_event = threading.Event()
        self.state.message_queue = queue.Queue()
        self.state.worker = RedLinkScanner(
            self.state.message_queue,
            self.state.stop_event,
            lambda: self._rpm,
            lambda: self._category,
            lambda: self._links_per_page,
        )
        self.state.worker.start()
        self.state.running = True
        self.state.start_time = time.time()
        self.state.status_message = "Scanner started."
        self.state.append_debug("Scanner started.")
        return None

    def stop(self) -> None:
        if not self.state.running:
            return
        if self.state.stop_event:
            self.state.stop_event.set()
        if self.state.worker:
            self.state.worker.join(timeout=3)
        self.state.running = False
        self.state.worker = None
        self.state.status_message = "Scanner stopped."
        self.state.append_debug("Scanner stopped.")

    def clear(self) -> None:
        self.stop()
        self.state.clear_results()
        self._seen_pairs.clear()
        self.state.status_message = "Results cleared."
        self.state.append_debug("Results cleared.")

    # --------------------------- processing -------------------------- #

    def _process_messages(self) -> None:
        while True:
            try:
                message = self.state.message_queue.get_nowait()
            except queue.Empty:
                break

            msg_type = message.get("type")
            if msg_type == "status":
                self.state.status_message = str(message.get("message", ""))
            elif msg_type == "scanned":
                self.state.scanned_count += 1
            elif msg_type == "links":
                self._handle_links(message)
            elif msg_type == "error":
                self.state.error_count += 1
                self.state.status_message = str(message.get("message", "Error"))
                self.state.append_debug(self.state.status_message)
            elif msg_type == "debug":
                self.state.append_debug(str(message.get("message", "")))

    def _handle_links(self, message: Dict[str, object]) -> None:
        source = str(message.get("source", ""))
        links: List[Dict[str, object]] = message.get("links", [])  # type: ignore[assignment]
        for info in links:
            title_text = str(info.get("title", ""))
            if not title_text:
                continue
            entry_key = f"{source} -> {title_text}"
            if entry_key in self._seen_pairs:
                continue
            self._seen_pairs.add(entry_key)
            sources_value = info.get("sources")
            sources_display = (
                f"{int(sources_value):,}" if isinstance(sources_value, int) else "?"
            )
            raw_category = info.get("category")
            category_input = str(raw_category) if isinstance(raw_category, str) else ""
            category = normalize_category_label(category_input)
            self.state.category_counts[category] += 1
            self.state.results.append(
                {
                    "category": category,
                    "missing": title_text,
                    "source": source,
                    "sources": sources_display,
                    "sources_raw": sources_value,
                }
            )
            self.state.result_count += 1

        if len(self.state.results) > 2000:
            self.state.results = self.state.results[-2000:]

    # --------------------------- public api -------------------------- #

    def get_status(self) -> Dict[str, object]:
        self._process_messages()
        elapsed = None
        if self.state.start_time and self.state.running:
            elapsed = int(time.time() - self.state.start_time)
        return {
            "running": self.state.running,
            "status": self.state.status_message,
            "scanned": self.state.scanned_count,
            "red_links": self.state.result_count,
            "errors": self.state.error_count,
            "category_counts": self.state.category_counts,
            "elapsed": elapsed,
            "results": self.state.results,
            "debug": self.state.debug_log[-150:],
            "config": {
                "rpm": self._rpm,
                "category": self._category,
                "links_per_page": self._links_per_page,
            },
        }

    def export_csv(self) -> io.BytesIO:
        self._process_messages()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Type", "Missing Article", "Found On", "Redirects"])
        for row in self.state.results:
            writer.writerow(
                [row.get("category"), row.get("missing"), row.get("source"), row.get("sources")]
            )
        buffer = io.BytesIO()
        buffer.write(output.getvalue().encode("utf-8"))
        buffer.seek(0)
        return buffer


class ShortArticleController:
    """Manage the short article scanner worker and results."""

    def __init__(self) -> None:
        self.state = ScannerState()
        self._rpm = DEFAULT_RPM
        self._category = ""
        self._max_bytes = 8000
        self._seen_titles: set[str] = set()

    def start(self, rpm: float, category: str, max_bytes: int) -> Optional[str]:
        if self.state.running:
            return "Short article scanner is already running."

        self._rpm = max(1.0, float(rpm))
        self._category = str(category or "")
        self._max_bytes = max(1, int(max_bytes))

        self.state.stop_event = threading.Event()
        self.state.message_queue = queue.Queue()
        self.state.worker = ShortArticleScanner(
            self.state.message_queue,
            self.state.stop_event,
            lambda: self._rpm,
            lambda: self._category,
            lambda: self._max_bytes,
        )
        self.state.worker.start()
        self.state.running = True
        self.state.start_time = time.time()
        self.state.status_message = "Short article scanner started."
        self.state.append_debug("Short article scanner started.")
        return None

    def stop(self) -> None:
        if not self.state.running:
            return
        if self.state.stop_event:
            self.state.stop_event.set()
        if self.state.worker:
            self.state.worker.join(timeout=3)
        self.state.running = False
        self.state.worker = None
        self.state.status_message = "Short article scanner stopped."
        self.state.append_debug("Short article scanner stopped.")

    def clear(self) -> None:
        self.stop()
        self.state.clear_results()
        self._seen_titles.clear()
        self.state.status_message = "Results cleared."
        self.state.append_debug("Results cleared.")

    def _process_messages(self) -> None:
        while True:
            try:
                message = self.state.message_queue.get_nowait()
            except queue.Empty:
                break

            msg_type = message.get("type")
            if msg_type == "status":
                self.state.status_message = str(message.get("message", ""))
            elif msg_type == "scanned_short":
                self.state.scanned_count += 1
            elif msg_type == "short_article":
                self._handle_short_article(message)
            elif msg_type == "error":
                self.state.error_count += 1
                self.state.status_message = str(message.get("message", "Error"))
                self.state.append_debug(self.state.status_message)
            elif msg_type == "debug":
                self.state.append_debug(str(message.get("message", "")))

    def _handle_short_article(self, message: Dict[str, object]) -> None:
        article = message.get("article", {})
        title = str(article.get("title", ""))
        if not title or title in self._seen_titles:
            return
        length_value = article.get("length")
        length_display = f"{int(length_value):,}" if isinstance(length_value, int) else "?"
        touched = str(article.get("touched", ""))
        url = str(article.get("url", ""))
        self.state.results.append(
            {
                "title": title,
                "bytes": length_display,
                "touched": touched,
                "url": url,
            }
        )
        self._seen_titles.add(title)
        self.state.result_count += 1
        if len(self.state.results) > 2000:
            self.state.results = self.state.results[-2000:]

    def get_status(self) -> Dict[str, object]:
        self._process_messages()
        elapsed = None
        if self.state.start_time and self.state.running:
            elapsed = int(time.time() - self.state.start_time)
        return {
            "running": self.state.running,
            "status": self.state.status_message,
            "scanned": self.state.scanned_count,
            "matches": self.state.result_count,
            "errors": self.state.error_count,
            "elapsed": elapsed,
            "results": self.state.results,
            "debug": self.state.debug_log[-150:],
            "config": {
                "rpm": self._rpm,
                "category": self._category,
                "max_bytes": self._max_bytes,
            },
        }

    def export_csv(self) -> io.BytesIO:
        self._process_messages()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Article", "Bytes", "Touched", "URL"])
        for row in self.state.results:
            writer.writerow([row.get("title"), row.get("bytes"), row.get("touched"), row.get("url")])
        buffer = io.BytesIO()
        buffer.write(output.getvalue().encode("utf-8"))
        buffer.seek(0)
        return buffer


app = Flask(__name__)
redlink_controller = RedLinkController()
short_controller = ShortArticleController()


@app.route("/")
def index() -> str:
    return render_template(
        "index.html",
        default_rpm=int(DEFAULT_RPM),
        categories=CATEGORY_DISPLAY_ORDER,
    )


# --------------------------- Red link endpoints ------------------------------ #


@app.post("/api/redlinks/start")
def start_redlinks():
    payload = request.get_json(force=True) or {}
    rpm = float(payload.get("rpm", DEFAULT_RPM))
    category = str(payload.get("category", ""))
    links_per_page = int(payload.get("links_per_page", 25))
    error = redlink_controller.start(rpm, category, links_per_page)
    status = redlink_controller.get_status()
    return jsonify({"error": error, "status": status})


@app.post("/api/redlinks/stop")
def stop_redlinks():
    redlink_controller.stop()
    status = redlink_controller.get_status()
    return jsonify({"status": status})


@app.post("/api/redlinks/clear")
def clear_redlinks():
    redlink_controller.clear()
    status = redlink_controller.get_status()
    return jsonify({"status": status})


@app.get("/api/redlinks/status")
def redlinks_status():
    status = redlink_controller.get_status()
    return jsonify(status)


@app.get("/api/redlinks/export")
def export_redlinks():
    buffer = redlink_controller.export_csv()
    filename = f"redlinks_{int(time.time())}.csv"
    return send_file(
        buffer,
        mimetype="text/csv",
        as_attachment=True,
        download_name=filename,
    )


# --------------------------- Short article endpoints ------------------------- #


@app.post("/api/short/start")
def start_short():
    payload = request.get_json(force=True) or {}
    rpm = float(payload.get("rpm", DEFAULT_RPM))
    category = str(payload.get("category", ""))
    max_bytes = int(payload.get("max_bytes", 8000))
    error = short_controller.start(rpm, category, max_bytes)
    status = short_controller.get_status()
    return jsonify({"error": error, "status": status})


@app.post("/api/short/stop")
def stop_short():
    short_controller.stop()
    status = short_controller.get_status()
    return jsonify({"status": status})


@app.post("/api/short/clear")
def clear_short():
    short_controller.clear()
    status = short_controller.get_status()
    return jsonify({"status": status})


@app.get("/api/short/status")
def short_status():
    status = short_controller.get_status()
    return jsonify(status)


@app.get("/api/short/export")
def export_short():
    buffer = short_controller.export_csv()
    filename = f"short_articles_{int(time.time())}.csv"
    return send_file(
        buffer,
        mimetype="text/csv",
        as_attachment=True,
        download_name=filename,
    )


def create_app() -> Flask:
    """Factory primarily used for testing."""

    return app


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)

