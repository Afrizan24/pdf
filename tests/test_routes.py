"""
Tests for Flask routes — /preview, /compress/stream, /compress/download.
Uses Flask test client, no real HTTP.
"""

from __future__ import annotations

import io
import json
import os

import fitz
import pytest

from app import app as flask_app


@pytest.fixture
def client():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def _pdf_bytes(pages: int = 2, text: str = "Hello world. " * 30) -> bytes:
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        if text:
            page.insert_text((72, 100), text, fontsize=11)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _upload(client, endpoint: str, pdf_data: bytes, extra_fields: dict = None):
    data = {"pdf": (io.BytesIO(pdf_data), "test.pdf")}
    if extra_fields:
        data.update(extra_fields)
    return client.post(endpoint, data=data, content_type="multipart/form-data")


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------

class TestStatus:
    def test_returns_200(self, client):
        res = client.get("/status")
        assert res.status_code == 200

    def test_has_gs_available_key(self, client):
        data = json.loads(res := client.get("/status").data)
        assert "gs_available" in data

    def test_gs_available_is_bool(self, client):
        data = json.loads(client.get("/status").data)
        assert isinstance(data["gs_available"], bool)


# ---------------------------------------------------------------------------
# /preview
# ---------------------------------------------------------------------------

class TestPreview:
    def test_valid_pdf_returns_200(self, client):
        res = _upload(client, "/preview", _pdf_bytes())
        assert res.status_code == 200

    def test_response_has_required_fields(self, client):
        res = _upload(client, "/preview", _pdf_bytes())
        data = json.loads(res.data)
        for key in ("pages", "file_size_bytes", "total_text_len",
                    "total_images", "avg_text_len_per_page", "avg_images_per_page"):
            assert key in data, f"Missing key: {key}"

    def test_page_count_correct(self, client):
        res = _upload(client, "/preview", _pdf_bytes(pages=4))
        data = json.loads(res.data)
        assert data["pages"] == 4

    def test_no_file_returns_400(self, client):
        res = client.post("/preview", data={}, content_type="multipart/form-data")
        assert res.status_code == 400

    def test_non_pdf_returns_400(self, client):
        data = {"pdf": (io.BytesIO(b"not a pdf"), "file.txt")}
        res = client.post("/preview", data=data, content_type="multipart/form-data")
        assert res.status_code == 400

    def test_empty_filename_returns_400(self, client):
        data = {"pdf": (io.BytesIO(b"%PDF-1.4"), "")}
        res = client.post("/preview", data=data, content_type="multipart/form-data")
        assert res.status_code == 400


# ---------------------------------------------------------------------------
# /compress/stream  (SSE)
# ---------------------------------------------------------------------------

class TestCompressStream:
    def _stream_to_events(self, response) -> list[dict]:
        """Parse SSE response into list of {event, data} dicts."""
        events = []
        current_event = None
        for line in response.data.decode().splitlines():
            if line.startswith("event: "):
                current_event = line[7:].strip()
            elif line.startswith("data: ") and current_event:
                events.append({
                    "event": current_event,
                    "data": json.loads(line[6:]),
                })
                current_event = None
        return events

    def test_valid_pdf_returns_200(self, client):
        res = _upload(client, "/compress/stream", _pdf_bytes(),
                      {"mode": "DIGITAL"})
        assert res.status_code == 200

    def test_content_type_is_sse(self, client):
        res = _upload(client, "/compress/stream", _pdf_bytes())
        assert "text/event-stream" in res.content_type

    def test_stream_ends_with_done_event(self, client):
        res = _upload(client, "/compress/stream", _pdf_bytes(),
                      {"mode": "DIGITAL"})
        events = self._stream_to_events(res)
        event_names = [e["event"] for e in events]
        assert "done" in event_names

    def test_done_event_has_token(self, client):
        res = _upload(client, "/compress/stream", _pdf_bytes(),
                      {"mode": "DIGITAL"})
        events = self._stream_to_events(res)
        done = next(e for e in events if e["event"] == "done")
        assert "token" in done["data"]

    def test_done_event_has_info(self, client):
        res = _upload(client, "/compress/stream", _pdf_bytes(),
                      {"mode": "DIGITAL"})
        events = self._stream_to_events(res)
        done = next(e for e in events if e["event"] == "done")
        assert "info" in done["data"]
        assert "saving_pct" in done["data"]["info"]

    def test_progress_events_emitted(self, client):
        res = _upload(client, "/compress/stream", _pdf_bytes(),
                      {"mode": "DIGITAL"})
        events = self._stream_to_events(res)
        progress = [e for e in events if e["event"] == "progress"]
        assert len(progress) > 0

    def test_no_file_returns_400(self, client):
        res = client.post("/compress/stream", data={},
                          content_type="multipart/form-data")
        assert res.status_code == 400

    def test_invalid_mode_returns_400(self, client):
        res = _upload(client, "/compress/stream", _pdf_bytes(),
                      {"mode": "BADMODE"})
        assert res.status_code == 400

    def test_scan_mode(self, client):
        res = _upload(client, "/compress/stream", _pdf_bytes(),
                      {"mode": "SCAN", "dpi": "100", "jpeg_q": "60"})
        events = self._stream_to_events(res)
        assert any(e["event"] == "done" for e in events)


# ---------------------------------------------------------------------------
# /compress/download/<token>
# ---------------------------------------------------------------------------

class TestCompressDownload:
    def _get_token(self, client) -> str:
        res = _upload(client, "/compress/stream", _pdf_bytes(),
                      {"mode": "DIGITAL"})
        for line in res.data.decode().splitlines():
            if line.startswith("data: "):
                d = json.loads(line[6:])
                if "token" in d:
                    return d["token"]
        pytest.fail("No token found in SSE stream")

    def test_valid_token_returns_pdf(self, client):
        token = self._get_token(client)
        res = client.get(f"/compress/download/{token}")
        assert res.status_code == 200
        assert res.content_type == "application/pdf"

    def test_downloaded_pdf_is_valid(self, client):
        token = self._get_token(client)
        res = client.get(f"/compress/download/{token}")
        doc = fitz.open(stream=res.data, filetype="pdf")
        assert doc.page_count > 0
        doc.close()

    def test_token_consumed_after_download(self, client):
        token = self._get_token(client)
        client.get(f"/compress/download/{token}")
        # Second request with same token should 404
        res2 = client.get(f"/compress/download/{token}")
        assert res2.status_code == 404

    def test_invalid_token_returns_404(self, client):
        res = client.get("/compress/download/nonexistent-token-xyz")
        assert res.status_code == 404

    def test_path_traversal_rejected(self, client):
        res = client.get("/compress/download/../../../etc/passwd")
        assert res.status_code in (400, 404)
