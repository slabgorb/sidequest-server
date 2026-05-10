from unittest.mock import patch

from fastapi.testclient import TestClient

from sidequest.server.app import create_app


def test_watcher_emit_endpoint_publishes_to_hub():
    app = create_app()
    client = TestClient(app)
    with patch("sidequest.server.app.publish_event") as mock_publish:
        resp = client.post("/internal/watcher/emit", json={
            "event_type": "test.event",
            "fields": {"k": "v"},
            "component": "daemon",
        })
        assert resp.status_code == 204
        mock_publish.assert_called_once_with(
            event_type="test.event",
            fields={"k": "v"},
            component="daemon",
        )
