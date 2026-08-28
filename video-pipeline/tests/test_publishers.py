"""Publisher factory and re-upload destinations.

The brief requires the pipeline to *re-upload* videos, so these back the
claim that the upload paths are real rather than decorative. The S3 test
uploads to a genuine in-process S3 server, not a mock of boto3.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.publishers import get_publisher
from pipeline.publishers.local import LocalPublisher
from pipeline.publishers.s3 import S3Publisher
from pipeline.publishers.youtube import YouTubePublisher


class TestFactory:
    @pytest.mark.parametrize("name,cls", [
        ("local", LocalPublisher), ("s3", S3Publisher), ("youtube", YouTubePublisher),
    ])
    def test_builds_each_publisher_with_shared_kwargs(self, name, cls, tmp_path):
        """Regression: the factory splatted every kwarg into every publisher,
        so `--publisher youtube` died with
        `TypeError: __init__() got an unexpected keyword argument 'output_dir'`
        before a single video was processed. Callers pass a superset of
        options; each publisher takes only what it uses."""
        pub = get_publisher(
            name, output_dir=tmp_path, bucket="b", endpoint_url="http://x",
            access_key="k", secret_key="s", credentials_file=tmp_path / "c.json",
        )
        assert isinstance(pub, cls)

    def test_case_insensitive(self, tmp_path):
        assert isinstance(get_publisher("LOCAL", output_dir=tmp_path), LocalPublisher)

    def test_unknown_name_lists_the_valid_ones(self, tmp_path):
        with pytest.raises(ValueError, match="local"):
            get_publisher("carrier-pigeon", output_dir=tmp_path)


class TestLocalPublisher:
    def test_writes_video_and_manifest(self, tmp_path, enrichment):
        src = tmp_path / "in.mp4"
        src.write_bytes(b"\x00" * 2048)

        result = LocalPublisher(output_dir=tmp_path / "out").publish(
            "testvid123", src, enrichment
        )

        assert result.success
        assert (tmp_path / "out" / "videos" / "testvid123.mp4").stat().st_size == 2048
        manifest = json.loads(
            (tmp_path / "out" / "manifests" / "testvid123.json").read_text()
        )
        assert manifest["ai_title"] == "A Test Video"

    def test_is_idempotent(self, tmp_path, enrichment):
        """Re-publishing must overwrite, never duplicate."""
        src = tmp_path / "in.mp4"
        src.write_bytes(b"\x00" * 512)
        pub = LocalPublisher(output_dir=tmp_path / "out")
        pub.publish("v1", src, enrichment)
        pub.publish("v1", src, enrichment)
        assert len(list((tmp_path / "out" / "videos").glob("*.mp4"))) == 1


class TestYouTubePublisher:
    def test_defaults_to_private(self):
        """Re-uploading third-party content public-by-default is a copyright
        incident waiting to happen. Public must be a deliberate choice."""
        assert YouTubePublisher().privacy_status == "private"

    def test_missing_file_fails_before_auth(self, tmp_path):
        result = YouTubePublisher().publish("v", tmp_path / "nope.mp4", None)
        assert not result.success and "not found" in result.error

    def test_missing_credentials_gives_actionable_error(self, tmp_path, enrichment):
        video = tmp_path / "v.mp4"
        video.write_bytes(b"\x00" * 128)
        result = YouTubePublisher(
            credentials_file=tmp_path / "absent.json",
            token_file=tmp_path / "absent_token.json",
        ).publish("v", video, enrichment)
        assert not result.success
        # The message must say what to do, not just that it failed.
        assert "OAuth" in result.error or "google-api-python-client" in result.error


class TestS3Publisher:
    """Real upload against a genuine S3 HTTP server (moto), not a boto3 mock."""

    @pytest.fixture
    def s3_server(self):
        moto_server = pytest.importorskip("moto.server")
        import threading
        import boto3
        from werkzeug.serving import make_server

        app = moto_server.DomainDispatcherApplication(moto_server.create_backend_app)
        srv = make_server("127.0.0.1", 0, app, threaded=True)
        port = srv.socket.getsockname()[1]
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()

        endpoint = f"http://127.0.0.1:{port}"
        client = boto3.client(
            "s3", endpoint_url=endpoint, region_name="us-east-1",
            aws_access_key_id="test", aws_secret_access_key="test",
        )
        client.create_bucket(Bucket="test-bucket")
        try:
            yield endpoint, client
        finally:
            srv.shutdown()

    def test_uploads_video_and_manifest(self, s3_server, tmp_path, enrichment):
        endpoint, client = s3_server
        video = tmp_path / "v.mp4"
        video.write_bytes(b"\x00" * 4096)

        result = S3Publisher(
            bucket="test-bucket", endpoint_url=endpoint,
            access_key="test", secret_key="test",
        ).publish("testvid123", video, enrichment)

        assert result.success
        assert result.remote_path == "s3://test-bucket/republished/testvid123.mp4"

        keys = {o["Key"] for o in client.list_objects_v2(Bucket="test-bucket")["Contents"]}
        assert keys == {
            "republished/testvid123.mp4", "republished/testvid123.json"
        }

        head = client.head_object(Bucket="test-bucket", Key="republished/testvid123.mp4")
        assert head["ContentLength"] == 4096
        assert head["ContentType"] == "video/mp4"

    def test_failure_is_reported_not_raised(self, tmp_path, enrichment):
        """A publish failure must return a result the agent can route on."""
        video = tmp_path / "v.mp4"
        video.write_bytes(b"\x00" * 64)
        result = S3Publisher(
            bucket="nonexistent", endpoint_url="http://127.0.0.1:1",
            access_key="k", secret_key="s",
        ).publish("v", video, enrichment)
        assert not result.success and result.error


class TestGoogleDrivePublisher:
    """Drive is the default target. Live upload needs OAuth, so these cover
    construction, safety defaults, and the failure paths - which is where a
    publisher usually goes wrong in production anyway."""

    def test_registered_in_factory(self, tmp_path):
        from pipeline.publishers.gdrive import GoogleDrivePublisher

        pub = get_publisher("gdrive", output_dir=tmp_path, subfolder="brand_a")
        assert isinstance(pub, GoogleDrivePublisher)
        assert pub.subfolder == "brand_a"

    def test_not_shareable_by_default(self):
        """A link anyone can open is a data decision, not a convenience."""
        from pipeline.publishers.gdrive import GoogleDrivePublisher

        assert GoogleDrivePublisher().make_shareable is False

    def test_uses_least_privilege_scope(self):
        """drive.file grants access only to files this app creates. The
        broader `drive` scope would expose the user's entire Drive to a tool
        that only ever needs to write its own outputs."""
        from pipeline.google_auth import DRIVE_SCOPE

        assert DRIVE_SCOPE.endswith("/drive.file")

    def test_missing_file_fails_before_auth(self, tmp_path, enrichment):
        """Cheap local checks must run before the expensive OAuth dance."""
        from pipeline.publishers.gdrive import GoogleDrivePublisher

        result = GoogleDrivePublisher().publish(
            "v", tmp_path / "absent.mp4", enrichment
        )
        assert not result.success and "not found" in result.error

    def test_missing_credentials_gives_setup_instructions(self, tmp_path, enrichment):
        from pipeline.publishers.gdrive import GoogleDrivePublisher

        video = tmp_path / "v.mp4"
        video.write_bytes(b"\x00" * 128)
        result = GoogleDrivePublisher(
            credentials_file=tmp_path / "absent.json",
            token_file=tmp_path / "absent_token.json",
        ).publish("v", video, enrichment)

        assert not result.success
        # Must tell the operator what to do, not just that it failed.
        assert "console.cloud.google.com" in result.error
        assert "Drive API" in result.error

    def test_failures_are_returned_not_raised(self, tmp_path, enrichment):
        """The agent routes on the result; an exception would kill the run."""
        from pipeline.publishers.gdrive import GoogleDrivePublisher

        video = tmp_path / "v.mp4"
        video.write_bytes(b"\x00" * 64)
        result = GoogleDrivePublisher(
            credentials_file=tmp_path / "nope.json",
            token_file=tmp_path / "nope_token.json",
        ).publish("v", video, enrichment)
        assert result.success is False


class TestGoogleAuth:
    def test_scopes_are_narrow(self):
        from pipeline import google_auth

        # youtube.upload cannot read or modify the channel; the bare `youtube`
        # scope could.
        assert google_auth.YOUTUBE_UPLOAD_SCOPE.endswith("/youtube.upload")
        assert google_auth.DRIVE_SCOPE.endswith("/drive.file")

    def test_setup_help_names_every_required_api(self):
        from pipeline.google_auth import SETUP_HELP

        for api in ("Google Drive API", "Google Sheets API", "YouTube Data API"):
            assert api in SETUP_HELP


class TestEnrichmentRoundTrip:
    """The manifest shipped beside each video must explain why it was
    published. That means the enrichment has to survive serialisation intact.
    """

    def _full(self):
        from pipeline.ai_analyzer import AIEnrichment
        from safety.content_safety import SafetyVerdict

        return AIEnrichment(
            video_id="v1", ai_title="T", ai_description="D", ai_tags=["a", "b"],
            ai_category="Howto",
            safety=SafetyVerdict("review", 0.8, ["off-brand"], "relevance_gate"),
            reasoning="because", summary="What the video shows",
            summary_source="transcript", relevance=0.25, model="m",
            cost_usd=0.0031, prompt_tokens=100, completion_tokens=50,
            skipped_reason=None,
        )

    def test_round_trip_loses_nothing(self):
        """Regression: the publish node rebuilt this object field-by-field and
        silently dropped relevance, summary and skipped_reason - so every
        published manifest was missing the basis of the publish decision."""
        import dataclasses

        from pipeline.ai_analyzer import AIEnrichment

        original = self._full()
        restored = AIEnrichment.from_dict(original.to_dict())
        for field in dataclasses.fields(original):
            assert getattr(restored, field.name) == getattr(original, field.name), (
                f"{field.name} did not survive the round trip"
            )

    def test_governance_fields_reach_the_manifest(self, tmp_path):
        """relevance and summary must be in the file that ships with the video."""
        import json

        from pipeline.publishers.local import LocalPublisher

        video = tmp_path / "v1.mp4"
        video.write_bytes(b"\x00" * 256)
        LocalPublisher(output_dir=tmp_path / "out").publish("v1", video, self._full())

        manifest = json.loads(
            (tmp_path / "out" / "manifests" / "v1.json").read_text()
        )
        assert manifest["relevance"] == 0.25
        assert manifest["summary"] == "What the video shows"
        assert manifest["summary_source"] == "transcript"
        assert manifest["safety"]["verdict"] == "review"

    def test_from_dict_tolerates_missing_keys(self):
        """Older manifests must still load."""
        from pipeline.ai_analyzer import AIEnrichment

        restored = AIEnrichment.from_dict({"ai_title": "Only a title"}, "v9")
        assert restored.video_id == "v9"
        assert restored.relevance == 1.0
        assert restored.summary == ""
