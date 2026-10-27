from equitylens import cloud_runtime


def test_backend_source_changes_expire_cached_service_without_expiring_ui_reruns(monkeypatch):
    class Client:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    cloud_runtime.embedded_client.clear()
    monkeypatch.setattr(cloud_runtime, "EmbeddedClient", Client)
    try:
        first = cloud_runtime.embedded_client("first-backend-build")
        assert cloud_runtime.embedded_client("first-backend-build") is first
        assert not first.closed
        second = cloud_runtime.embedded_client("updated-backend-build")
        assert second is not first
        assert first.closed
        assert not second.closed
    finally:
        cloud_runtime.embedded_client.clear()


def test_revision_tracks_backend_content_but_does_not_read_secrets_or_cached_data(tmp_path):
    source = tmp_path / "service.py"
    source.write_text("version = 1\n", encoding="utf-8")
    before = cloud_runtime.backend_revision(tmp_path)
    (tmp_path / ".env").write_text("LLM_API_KEY=not-a-real-key", encoding="utf-8")
    assert cloud_runtime.backend_revision(tmp_path) == before
    source.write_text("version = 2\n", encoding="utf-8")
    assert cloud_runtime.backend_revision(tmp_path) != before
