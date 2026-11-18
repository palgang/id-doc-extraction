import json
import sys
import types
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import main
from app.mrz import build_td1
from app.pipeline import Pipeline
from tests.test_pipeline import FakeExtractor, FakeOcr, viz

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
MRZ = build_td1("L01X00T47", "830812", "<", "310630", "SCHAEFER", "JUERGEN")


@pytest.fixture
def client(monkeypatch):
    pipeline = Pipeline(FakeOcr("\n".join(MRZ)), FakeExtractor(viz()), today=date(2026, 11, 17))
    monkeypatch.setattr(main, "get_pipeline", lambda: pipeline)
    return TestClient(main.app)


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_index_page(client):
    assert "ID document extraction" in client.get("/").text


def test_extract_front_and_back(client):
    files = [("files", ("front.png", PNG, "image/png")), ("files", ("back.png", PNG, "image/png"))]
    r = client.post("/v1/extract", files=files)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "OK"
    assert body["fields"]["given_names"]["value"] == "Jürgen"
    assert body["processing"]["pages"] == 2


def test_rejects_unknown_file_type(client):
    r = client.post("/v1/extract", files=[("files", ("x.png", b"not an image", "image/png"))])
    assert r.status_code == 415


def test_rejects_too_many_files(client):
    files = [("files", (f"{i}.png", PNG, "image/png")) for i in range(5)]
    assert client.post("/v1/extract", files=files).status_code == 400


class _Blob:
    def __init__(self, store, bucket, name):
        self.store, self.key = store, (bucket, name)

    def download_as_bytes(self):
        return self.store[self.key]

    def upload_from_string(self, data, content_type=None):
        self.store[self.key] = data

    def delete(self):
        del self.store[self.key]


class _Bucket:
    def __init__(self, store, name):
        self.store, self.name = store, name

    def blob(self, name):
        return _Blob(self.store, self.name, name)


def test_gcs_event_writes_result_and_deletes_input(client, monkeypatch):
    store = {("in", "uploads/doc.png"): PNG}
    fake_storage = types.SimpleNamespace(Client=lambda project=None: types.SimpleNamespace(bucket=lambda n: _Bucket(store, n)))
    monkeypatch.setitem(sys.modules, "google.cloud.storage", fake_storage)
    import google.cloud
    monkeypatch.setattr(google.cloud, "storage", fake_storage, raising=False)
    monkeypatch.setenv("OUTPUT_BUCKET", "out")
    main.get_settings.cache_clear()
    try:
        r = client.post("/events/gcs", json={"bucket": "in", "name": "uploads/doc.png"}, headers={"ce-id": "evt-1"})
    finally:
        main.get_settings.cache_clear()
    assert r.status_code == 204
    result = json.loads(store[("out", "uploads/doc.png.json")])
    assert result["status"] == "OK" and result["source"] == "gs://in/uploads/doc.png"
    assert ("in", "uploads/doc.png") not in store


@pytest.fixture
def model_env(monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash")
    monkeypatch.setenv("GEMINI_ALLOWED_MODELS", "gemini-3.5-flash-lite, gemini-3.1-pro-preview")
    main.get_settings.cache_clear()
    yield
    main.get_settings.cache_clear()


def _pipeline(client):
    return main.get_pipeline()


def test_models_lists_default_first(client, model_env):
    body = client.get("/v1/models").json()
    assert body == {"default": "gemini-3.5-flash",
                    "models": ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-pro-preview"]}


def test_models_empty_when_gemini_disabled(client, model_env, monkeypatch):
    monkeypatch.setenv("GEMINI_ENABLED", "false")
    main.get_settings.cache_clear()
    assert client.get("/v1/models").json() == {"default": None, "models": []}


def test_extract_with_selected_model(client, model_env):
    r = client.post("/v1/extract", files=[("files", ("a.png", PNG, "image/png"))], data={"model": "gemini-3.1-pro-preview"})
    assert r.status_code == 200, r.text
    assert r.json()["processing"]["llm_model"] == "gemini-3.1-pro-preview"
    assert _pipeline(client).extractor.called_with_model == "gemini-3.1-pro-preview"


def test_extract_without_model_uses_default(client, model_env):
    r = client.post("/v1/extract", files=[("files", ("a.png", PNG, "image/png"))])
    assert r.status_code == 200, r.text
    assert r.json()["processing"]["llm_model"] == "fake-gemini"
    assert _pipeline(client).extractor.called_with_model is None


def test_extract_rejects_unknown_model(client, model_env):
    r = client.post("/v1/extract", files=[("files", ("a.png", PNG, "image/png"))], data={"model": "gpt-4o"})
    assert r.status_code == 422
    assert "gemini-3.5-flash" in r.json()["detail"]
