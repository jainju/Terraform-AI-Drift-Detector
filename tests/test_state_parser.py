"""Tests for the state parser module."""

import json
from pathlib import Path

import pytest

from drift_detector.state_parser import StateParser


SAMPLE_STATE_PATH = str(Path(__file__).parent / "sample_state.json")


@pytest.fixture
def parser():
    return StateParser(ignore_attributes=["arn", "id", "self_link", "etag"])


@pytest.fixture
def sample_state():
    with open(SAMPLE_STATE_PATH, "r") as f:
        return json.load(f)


class TestStateParser:
    def test_parse_file(self, parser):
        """Test parsing a state file from disk."""
        resources = parser.parse_file(SAMPLE_STATE_PATH)
        assert len(resources) > 0

    def test_parse_state_skips_data_sources(self, parser, sample_state):
        """Test that data sources are excluded."""
        resources = parser.parse_state(sample_state)
        resource_types = [r.resource_type for r in resources]
        assert "aws_ami" not in resource_types

    def test_parse_state_extracts_managed_resources(self, parser, sample_state):
        """Test that managed resources are correctly extracted."""
        resources = parser.parse_state(sample_state)
        assert len(resources) == 4  # instance, bucket, sg, lambda

    def test_resource_metadata_fields(self, parser, sample_state):
        """Test that resource metadata fields are populated."""
        resources = parser.parse_state(sample_state)
        instance = next(r for r in resources if r.resource_type == "aws_instance")

        assert instance.resource_id == "i-0abc123def456789"
        assert instance.resource_name == "web_server"  # Falls back to TF block name
        assert instance.provider == "aws"
        assert instance.tags.get("Environment") == "production"

    def test_provider_extraction(self, parser, sample_state):
        """Test provider name extraction from provider string."""
        resources = parser.parse_state(sample_state)
        for r in resources:
            assert r.provider == "aws"

    def test_tags_extraction(self, parser, sample_state):
        """Test tags are correctly extracted."""
        resources = parser.parse_state(sample_state)
        bucket = next(r for r in resources if r.resource_type == "aws_s3_bucket")
        assert bucket.tags == {"Name": "my-app-data-bucket", "Environment": "production"}

    def test_ignore_attributes(self, parser, sample_state):
        """Test that ignored attributes are filtered from attributes dict."""
        resources = parser.parse_state(sample_state)
        instance = next(r for r in resources if r.resource_type == "aws_instance")
        assert "arn" not in instance.attributes
        assert "id" not in instance.attributes

    def test_unsupported_version_raises(self, parser):
        """Test that unsupported state versions raise ValueError."""
        bad_state = {"version": 3, "resources": []}
        with pytest.raises(ValueError, match="Unsupported state version"):
            parser.parse_state(bad_state)

    def test_file_not_found_raises(self, parser):
        """Test that missing file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            parser.parse_file("/nonexistent/path.tfstate")
