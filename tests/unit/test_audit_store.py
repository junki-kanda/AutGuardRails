"""Tests for Audit Store."""

from datetime import datetime, timedelta
from uuid import uuid4

import boto3
import pytest
from moto import mock_aws

from src.guardrails.audit_store import AuditStore, create_audit_table
from src.guardrails.models import ActionExecution


@pytest.fixture
def mock_dynamodb():
    """Set up mocked DynamoDB."""
    with mock_aws():
        # Create table
        create_audit_table(table_name="test-audit", region="us-east-1")
        yield


@pytest.fixture
def audit_store(mock_dynamodb):
    """Create AuditStore instance with mocked DynamoDB."""
    return AuditStore(table_name="test-audit", region="us-east-1")


@pytest.fixture
def sample_execution():
    """Create sample execution for testing."""
    return ActionExecution(
        execution_id=f"exec-{uuid4()}",
        policy_id="test-policy",
        event_id="evt-123",
        status="executed",
        executed_at=datetime.utcnow(),
        executed_by="test-user",
        action="attach_deny_policy",
        target="arn:aws:iam::123456789012:role/test",
        diff={"policy_arn": "arn:aws:iam::123456789012:policy/test"},
        ttl_expires_at=datetime.utcnow() + timedelta(hours=2),
    )


class TestAuditStoreInit:
    """Test AuditStore initialization."""

    def test_init_with_defaults(self, mock_dynamodb):
        """Test initialization with default values."""
        store = AuditStore(table_name="test-audit")
        assert store.table_name == "test-audit"
        assert store.region == "us-east-1"

    def test_init_with_custom_values(self, mock_dynamodb):
        """Test initialization with custom values."""
        store = AuditStore(table_name="custom-table", region="eu-west-1")
        assert store.table_name == "custom-table"
        assert store.region == "eu-west-1"


class TestSaveExecution:
    """Test saving execution records."""

    def test_save_execution_success(self, audit_store, sample_execution):
        """Test successful save."""
        result = audit_store.save_execution(sample_execution)
        assert result is True

        # Verify it was saved
        retrieved = audit_store.get_execution(sample_execution.execution_id)
        assert retrieved is not None
        assert retrieved.execution_id == sample_execution.execution_id
        assert retrieved.policy_id == sample_execution.policy_id
        assert retrieved.status == sample_execution.status

    def test_save_execution_minimal_fields(self, audit_store):
        """Test saving execution with minimal fields."""
        execution = ActionExecution(
            execution_id=f"exec-{uuid4()}",
            policy_id="test-policy",
            event_id="evt-123",
            status="planned",
            executed_at=None,  # Not executed yet
            executed_by="system",
            action="notify_only",
            target="arn:aws:iam::123456789012:role/test",
            diff={},
        )

        result = audit_store.save_execution(execution)
        assert result is True

        # Verify
        retrieved = audit_store.get_execution(execution.execution_id)
        assert retrieved is not None
        assert retrieved.status == "planned"
        assert retrieved.executed_at is None

    def test_save_execution_with_all_fields(self, audit_store):
        """Test saving execution with all optional fields."""
        execution = ActionExecution(
            execution_id=f"exec-{uuid4()}",
            policy_id="test-policy",
            event_id="evt-123",
            status="rolled_back",
            executed_at=datetime.utcnow() - timedelta(hours=2),
            executed_by="admin@example.com",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/test",
            diff={"before": [], "after": ["arn:aws:iam::123456789012:policy/deny"]},
            ttl_expires_at=datetime.utcnow() + timedelta(hours=1),
            rolled_back_at=datetime.utcnow(),
        )

        result = audit_store.save_execution(execution)
        assert result is True

        # Verify all fields
        retrieved = audit_store.get_execution(execution.execution_id)
        assert retrieved is not None
        assert retrieved.status == "rolled_back"
        assert retrieved.rolled_back_at is not None
        assert retrieved.ttl_expires_at is not None


class TestGetExecution:
    """Test retrieving execution records."""

    def test_get_existing_execution(self, audit_store, sample_execution):
        """Test retrieving existing execution."""
        audit_store.save_execution(sample_execution)

        retrieved = audit_store.get_execution(sample_execution.execution_id)

        assert retrieved is not None
        assert retrieved.execution_id == sample_execution.execution_id
        assert retrieved.policy_id == sample_execution.policy_id
        assert retrieved.event_id == sample_execution.event_id
        assert retrieved.status == sample_execution.status
        assert retrieved.action == sample_execution.action
        assert retrieved.target == sample_execution.target

    def test_get_nonexistent_execution(self, audit_store):
        """Test retrieving non-existent execution."""
        retrieved = audit_store.get_execution("exec-does-not-exist")
        assert retrieved is None

    def test_get_execution_preserves_datetime(self, audit_store):
        """Test that datetime fields are preserved correctly."""
        execution = ActionExecution(
            execution_id=f"exec-{uuid4()}",
            policy_id="test-policy",
            event_id="evt-123",
            status="executed",
            executed_at=datetime(2024, 1, 15, 10, 30, 0),
            executed_by="test",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/test",
            diff={},
            ttl_expires_at=datetime(2024, 1, 15, 12, 30, 0),
        )

        audit_store.save_execution(execution)
        retrieved = audit_store.get_execution(execution.execution_id)

        assert retrieved is not None
        # Compare timestamps (may have microsecond differences due to ISO format)
        assert abs((retrieved.executed_at - execution.executed_at).total_seconds()) < 1
        assert abs((retrieved.ttl_expires_at - execution.ttl_expires_at).total_seconds()) < 1


class TestUpdateExecution:
    """Test updating execution records."""

    def test_update_execution_status(self, audit_store, sample_execution):
        """Test updating execution status."""
        # Save initial
        audit_store.save_execution(sample_execution)

        # Update status
        sample_execution.status = "rolled_back"
        sample_execution.rolled_back_at = datetime.utcnow()

        result = audit_store.update_execution(sample_execution)
        assert result is True

        # Verify update
        retrieved = audit_store.get_execution(sample_execution.execution_id)
        assert retrieved.status == "rolled_back"
        assert retrieved.rolled_back_at is not None

    def test_update_nonexistent_creates_new(self, audit_store):
        """Test that updating non-existent execution creates it."""
        execution = ActionExecution(
            execution_id=f"exec-{uuid4()}",
            policy_id="test-policy",
            event_id="evt-123",
            status="executed",
            executed_at=datetime.utcnow(),
            executed_by="test",
            action="notify_only",
            target="arn:aws:iam::123456789012:role/test",
            diff={},
        )

        # Update without saving first
        result = audit_store.update_execution(execution)
        assert result is True

        # Should exist now
        retrieved = audit_store.get_execution(execution.execution_id)
        assert retrieved is not None


class TestQueryExecutionsByPolicy:
    """Test querying executions by policy."""

    def test_query_executions_for_policy(self, audit_store):
        """Test querying all executions for a policy."""
        policy_id = "test-policy-123"

        # Create multiple executions
        for i in range(5):
            execution = ActionExecution(
                execution_id=f"exec-{i}",
                policy_id=policy_id,
                event_id=f"evt-{i}",
                status="executed",
                executed_at=datetime.utcnow() - timedelta(minutes=i),
                executed_by="test",
                action="attach_deny_policy",
                target="arn:aws:iam::123456789012:role/test",
                diff={},
            )
            audit_store.save_execution(execution)

        # Query
        results = audit_store.query_executions_by_policy(policy_id)

        assert len(results) == 5
        # Should be sorted by executed_at descending (newest first)
        assert results[0].execution_id == "exec-0"
        assert results[4].execution_id == "exec-4"

    def test_query_executions_with_limit(self, audit_store):
        """Test querying with limit."""
        policy_id = "test-policy-456"

        # Create 10 executions
        for i in range(10):
            execution = ActionExecution(
                execution_id=f"exec-{i}",
                policy_id=policy_id,
                event_id=f"evt-{i}",
                status="executed",
                executed_at=datetime.utcnow(),
                executed_by="test",
                action="notify_only",
                target="arn:aws:iam::123456789012:role/test",
                diff={},
            )
            audit_store.save_execution(execution)

        # Query with limit
        results = audit_store.query_executions_by_policy(policy_id, limit=5)

        assert len(results) <= 5

    def test_query_nonexistent_policy(self, audit_store):
        """Test querying for policy with no executions."""
        results = audit_store.query_executions_by_policy("nonexistent-policy")
        assert results == []


class TestQueryExpiredExecutions:
    """Test querying expired executions."""

    def test_query_expired_executions(self, audit_store):
        """Test finding executions with expired TTL."""
        current_time = datetime.utcnow()

        # Create expired execution
        expired_execution = ActionExecution(
            execution_id="exec-expired",
            policy_id="test-policy",
            event_id="evt-123",
            status="executed",
            executed_at=current_time - timedelta(hours=3),
            executed_by="test",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/test",
            diff={},
            ttl_expires_at=current_time - timedelta(hours=1),  # Expired 1 hour ago
        )
        audit_store.save_execution(expired_execution)

        # Create not-yet-expired execution
        active_execution = ActionExecution(
            execution_id="exec-active",
            policy_id="test-policy",
            event_id="evt-124",
            status="executed",
            executed_at=current_time - timedelta(hours=1),
            executed_by="test",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/test",
            diff={},
            ttl_expires_at=current_time + timedelta(hours=1),  # Expires in 1 hour
        )
        audit_store.save_execution(active_execution)

        # Query expired
        results = audit_store.query_expired_executions(current_time)

        assert len(results) >= 1
        execution_ids = [e.execution_id for e in results]
        assert "exec-expired" in execution_ids
        assert "exec-active" not in execution_ids

    def test_query_expired_ignores_rolled_back(self, audit_store):
        """Test that query ignores already rolled-back executions."""
        current_time = datetime.utcnow()

        # Create expired but already rolled-back execution
        rolled_back_execution = ActionExecution(
            execution_id="exec-rolled-back",
            policy_id="test-policy",
            event_id="evt-125",
            status="rolled_back",  # Already rolled back
            executed_at=current_time - timedelta(hours=3),
            executed_by="test",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/test",
            diff={},
            ttl_expires_at=current_time - timedelta(hours=1),
            rolled_back_at=current_time - timedelta(minutes=30),
        )
        audit_store.save_execution(rolled_back_execution)

        # Query expired
        results = audit_store.query_expired_executions(current_time)

        # Should not include rolled-back execution
        execution_ids = [e.execution_id for e in results]
        assert "exec-rolled-back" not in execution_ids


class TestListRecentExecutions:
    """Test listing recent executions."""

    def test_list_recent_executions(self, audit_store):
        """Test listing recent executions."""
        # Create multiple executions
        for i in range(5):
            execution = ActionExecution(
                execution_id=f"exec-{i}",
                policy_id=f"policy-{i}",
                event_id=f"evt-{i}",
                status="executed",
                executed_at=datetime.utcnow() - timedelta(minutes=i),
                executed_by="test",
                action="attach_deny_policy",
                target="arn:aws:iam::123456789012:role/test",
                diff={},
            )
            audit_store.save_execution(execution)

        results = audit_store.list_recent_executions(limit=10)

        assert len(results) == 5

    def test_list_recent_with_status_filter(self, audit_store):
        """Test listing recent executions with status filter."""
        # Create executions with different statuses
        statuses = ["planned", "executed", "executed", "failed", "rolled_back"]
        for i, status in enumerate(statuses):
            execution = ActionExecution(
                execution_id=f"exec-{i}",
                policy_id="test-policy",
                event_id=f"evt-{i}",
                status=status,
                executed_at=datetime.utcnow() if status != "planned" else None,
                executed_by="test",
                action="attach_deny_policy",
                target="arn:aws:iam::123456789012:role/test",
                diff={},
            )
            audit_store.save_execution(execution)

        # Query only executed
        results = audit_store.list_recent_executions(limit=10, status="executed")

        assert len(results) == 2
        assert all(e.status == "executed" for e in results)

    def test_list_recent_respects_limit(self, audit_store):
        """Test that limit is respected."""
        # Create 20 executions
        for i in range(20):
            execution = ActionExecution(
                execution_id=f"exec-{i}",
                policy_id="test-policy",
                event_id=f"evt-{i}",
                status="executed",
                executed_at=datetime.utcnow(),
                executed_by="test",
                action="notify_only",
                target="arn:aws:iam::123456789012:role/test",
                diff={},
            )
            audit_store.save_execution(execution)

        results = audit_store.list_recent_executions(limit=5)

        assert len(results) == 5


class TestCreateAuditTable:
    """Test table creation."""

    def test_create_table_success(self):
        """Test successful table creation."""
        with mock_aws():
            result = create_audit_table(table_name="new-audit-table", region="us-east-1")
            assert result is True

            # Verify table exists
            dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
            table = dynamodb.Table("new-audit-table")
            assert table.table_status in ["ACTIVE", "CREATING"]

    def test_create_table_already_exists(self):
        """Test creating table that already exists."""
        with mock_aws():
            # Create first time
            create_audit_table(table_name="existing-table", region="us-east-1")

            # Try to create again
            result = create_audit_table(table_name="existing-table", region="us-east-1")
            assert result is True  # Should succeed (idempotent)


class TestExecutionToItem:
    """Test conversion from ActionExecution to DynamoDB item."""

    def test_execution_to_item_complete(self, audit_store):
        """Test converting complete execution to item."""
        execution = ActionExecution(
            execution_id="exec-123",
            policy_id="test-policy",
            event_id="evt-123",
            status="executed",
            executed_at=datetime(2024, 1, 15, 10, 30, 0),
            executed_by="test-user",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/test",
            diff={"before": [], "after": ["policy-arn"]},
            ttl_expires_at=datetime(2024, 1, 15, 12, 30, 0),
            rolled_back_at=None,
        )

        item = audit_store._execution_to_item(execution)

        assert item["execution_id"] == "exec-123"
        assert item["policy_id"] == "test-policy"
        assert item["status"] == "executed"
        assert "executed_at" in item
        assert "ttl_expires_at" in item
        assert "rolled_back_at" not in item  # Should not be present if None

    def test_execution_to_item_minimal(self, audit_store):
        """Test converting minimal execution to item."""
        execution = ActionExecution(
            execution_id="exec-456",
            policy_id="test-policy",
            event_id="evt-456",
            status="planned",
            executed_at=None,
            executed_by="system",
            action="notify_only",
            target="arn:aws:iam::123456789012:role/test",
            diff={},
        )

        item = audit_store._execution_to_item(execution)

        assert item["execution_id"] == "exec-456"
        assert item["status"] == "planned"
        assert "executed_at" not in item  # Should not be present if None


class TestItemToExecution:
    """Test conversion from DynamoDB item to ActionExecution."""

    def test_item_to_execution_complete(self, audit_store):
        """Test converting complete item to execution."""
        item = {
            "execution_id": "exec-789",
            "timestamp": "2024-01-15T10:30:00",
            "policy_id": "test-policy",
            "event_id": "evt-789",
            "status": "executed",
            "executed_at": "2024-01-15T10:30:00",
            "executed_by": "admin@example.com",
            "action": "attach_deny_policy",
            "target": "arn:aws:iam::123456789012:role/admin",
            "diff": {"before": [], "after": ["policy-arn"]},
            "ttl_expires_at": "2024-01-15T12:30:00",
        }

        execution = audit_store._item_to_execution(item)

        assert execution.execution_id == "exec-789"
        assert execution.policy_id == "test-policy"
        assert execution.status == "executed"
        assert execution.executed_at == datetime(2024, 1, 15, 10, 30, 0)
        assert execution.ttl_expires_at == datetime(2024, 1, 15, 12, 30, 0)
        assert execution.rolled_back_at is None

    def test_item_to_execution_minimal(self, audit_store):
        """Test converting minimal item to execution."""
        item = {
            "execution_id": "exec-999",
            "timestamp": "2024-01-15T10:30:00",
            "policy_id": "test-policy",
            "event_id": "evt-999",
            "status": "planned",
            "executed_by": "system",
            "action": "notify_only",
            "target": "arn:aws:iam::123456789012:role/test",
            "diff": {},
        }

        execution = audit_store._item_to_execution(item)

        assert execution.execution_id == "exec-999"
        assert execution.status == "planned"
        assert execution.executed_at is None
        assert execution.ttl_expires_at is None
        assert execution.rolled_back_at is None
