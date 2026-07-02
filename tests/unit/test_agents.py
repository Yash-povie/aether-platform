import pytest
from unittest.mock import patch, MagicMock


class TestCoordinatorNode:
    """Unit tests for the coordinator LangGraph node."""

    def test_empty_artifacts_returns_empty(self):
        from services.agent_engine.agents.coordinator import coordinator_node

        state = {
            "job_id": "test-123",
            "artifacts": [],
            "audit_trail": [],
        }
        result = coordinator_node(state)
        assert result["artifacts"] == []
        assert any("coordinator" in str(entry).lower() for entry in result["audit_trail"])

    def test_task_description_set(self):
        from services.agent_engine.agents.coordinator import coordinator_node

        state = {
            "job_id": "test-123",
            "artifacts": [{"content_uri": "s3://bucket/file.pdf", "modality": "pdf"}],
            "audit_trail": [],
        }
        with patch(
            "services.agent_engine.agents.coordinator.fetch_artifact_content",
            return_value="test content",
        ):
            result = coordinator_node(state)
        assert "task_description" in result
        assert "test-123" in result["task_description"]

    def test_audit_trail_grows(self):
        from services.agent_engine.agents.coordinator import coordinator_node

        initial_trail = [{"node": "start", "ts": "2024-01-01T00:00:00"}]
        state = {"job_id": "abc", "artifacts": [], "audit_trail": initial_trail.copy()}
        result = coordinator_node(state)
        assert len(result["audit_trail"]) > len(initial_trail)


class TestPIIRedactorNode:
    """Unit tests for the PII redactor LangGraph node."""

    def test_email_redacted(self):
        from services.agent_engine.agents.pii_redactor import pii_redactor_node

        state = {
            "artifacts": [
                {"content": "Contact john@example.com for info", "content_uri": ""}
            ],
            "audit_trail": [],
        }
        result = pii_redactor_node(state)
        assert "john@example.com" not in result["artifacts"][0]["content"]
        assert result["artifacts"][0]["pii_redacted"] is True

    def test_ssn_redacted(self):
        from services.agent_engine.agents.pii_redactor import pii_redactor_node

        state = {
            "artifacts": [{"content": "SSN: 123-45-6789", "content_uri": ""}],
            "audit_trail": [],
        }
        result = pii_redactor_node(state)
        assert "123-45-6789" not in result["artifacts"][0]["content"]

    def test_phone_number_redacted(self):
        from services.agent_engine.agents.pii_redactor import pii_redactor_node

        state = {
            "artifacts": [{"content": "Call me at +1-800-555-1234", "content_uri": ""}],
            "audit_trail": [],
        }
        result = pii_redactor_node(state)
        assert "+1-800-555-1234" not in result["artifacts"][0]["content"]

    def test_clean_content_untouched(self):
        from services.agent_engine.agents.pii_redactor import pii_redactor_node

        state = {
            "artifacts": [{"content": "Normal text without PII", "content_uri": ""}],
            "audit_trail": [],
        }
        result = pii_redactor_node(state)
        assert result["artifacts"][0]["content"] == "Normal text without PII"
        assert result["artifacts"][0]["pii_redacted"] is False

    def test_multiple_artifacts_all_processed(self):
        from services.agent_engine.agents.pii_redactor import pii_redactor_node

        state = {
            "artifacts": [
                {"content": "Email: a@b.com", "content_uri": ""},
                {"content": "No PII here", "content_uri": ""},
            ],
            "audit_trail": [],
        }
        result = pii_redactor_node(state)
        assert len(result["artifacts"]) == 2
        assert result["artifacts"][0]["pii_redacted"] is True
        assert result["artifacts"][1]["pii_redacted"] is False


class TestEvidenceReconcilerNode:
    """Unit tests for the evidence reconciler LangGraph node."""

    def test_empty_findings_passthrough(self):
        from services.agent_engine.agents.evidence_reconciler import evidence_reconciler_node

        state = {"findings": [], "audit_trail": []}
        result = evidence_reconciler_node(state)
        assert result["reconciled_findings"] == []

    def test_single_finding_preserved(self):
        from services.agent_engine.agents.evidence_reconciler import evidence_reconciler_node

        state = {
            "findings": [
                {
                    "description": "Anomaly found in sensor data",
                    "modality": "csv",
                    "confidence": 0.85,
                }
            ],
            "audit_trail": [],
        }
        with patch(
            "services.agent_engine.agents.evidence_reconciler.llm"
        ) as mock_llm:
            mock_llm.invoke.side_effect = Exception("LLM unavailable")
            result = evidence_reconciler_node(state)
        assert len(result["reconciled_findings"]) >= 1

    def test_reconciled_findings_key_present(self):
        from services.agent_engine.agents.evidence_reconciler import evidence_reconciler_node

        state = {"findings": [{"description": "test", "confidence": 0.9}], "audit_trail": []}
        with patch("services.agent_engine.agents.evidence_reconciler.llm") as mock_llm:
            mock_llm.invoke.return_value = MagicMock(content='[{"description": "test", "confidence": 0.9}]')
            result = evidence_reconciler_node(state)
        assert "reconciled_findings" in result


class TestConfidenceScorerNode:
    """Unit tests for the confidence scorer LangGraph node."""

    def test_high_confidence_finding_not_flagged_for_hitl(self):
        from services.agent_engine.agents.confidence_scorer import confidence_scorer_node

        state = {
            "reconciled_findings": [{"description": "Clear anomaly", "confidence": 0.95}],
            "audit_trail": [],
            "hitl_threshold": 0.75,
        }
        result = confidence_scorer_node(state)
        hitl_items = [f for f in result.get("reconciled_findings", []) if f.get("requires_hitl")]
        assert len(hitl_items) == 0

    def test_low_confidence_finding_flagged_for_hitl(self):
        from services.agent_engine.agents.confidence_scorer import confidence_scorer_node

        state = {
            "reconciled_findings": [{"description": "Uncertain finding", "confidence": 0.50}],
            "audit_trail": [],
            "hitl_threshold": 0.75,
        }
        result = confidence_scorer_node(state)
        hitl_items = [f for f in result.get("reconciled_findings", []) if f.get("requires_hitl")]
        assert len(hitl_items) == 1