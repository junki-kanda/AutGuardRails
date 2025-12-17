"""End-to-End Integration Tests for Phase 3 (Auto Mode + TTL Cleanup).

These tests verify the complete auto mode and TTL cleanup flow:
1. AWS Budget event arrives
2. Policy engine matches auto mode policy
3. Guardrail executed immediately (no approval)
4. IAM deny policy attached
5. Execution saved to DynamoDB with TTL
6. Confirmation notification sent
7. TTL expires
8. TTL cleanup handler runs
9. Policy is rolled back
10. Execution status updated to 'rolled_back'
"""

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from moto import mock_aws

from src.guardrails.handlers.budgets_event import lambda_handler
from src.guardrails.handlers.ttl_cleanup import TTLCleanupHandler


@pytest.fixture
def temp_policies_dir():
    """Create temporary directory with auto mode policy."""
    import yaml

    with tempfile.TemporaryDirectory() as tmpdir:
        policies_path = Path(tmpdir)

        # Create auto mode policy
        auto_policy = {
            "policy_id": "test-auto-ec2-spike",
            "enabled": True,
            "mode": "auto",
            "ttl_minutes": 60,  # 1 hour TTL
            "match": {
                "source": ["budgets"],
                "account_ids": ["123456789012"],
                "min_amount_usd": 500.0,
            },
            "scope": {
                "principals": [
                    {
                        "type": "iam_role",
                        "arn": "arn:aws:iam::123456789012:role/ci-deployer",
                    }
                ]
            },
            "actions": [
                {
                    "type": "attach_deny_policy",
                    "deny": ["ec2:RunInstances", "ec2:CreateNatGateway"],
                }
            ],
            "notify": {"slack_webhook_ssm_param": "/test/webhook"},
        }

        (policies_path / "auto-mode.yaml").write_text(yaml.dump(auto_policy))

        yield str(policies_path)


class TestE2EAutoMode:
    """Test complete auto mode execution flow."""

    @mock_aws
    def test_auto_mode_end_to_end(self, temp_policies_dir):
        """Test full auto mode flow from event to execution.

        Flow:
        1. Budget event triggers lambda
        2. Policy matches (auto mode)
        3. Guardrail executed immediately
        4. IAM deny policy attached
        5. Execution saved to DynamoDB
        6. Confirmation sent to Slack
        """
        import boto3

        # Setup mocked AWS resources
        iam = boto3.client("iam", region_name="us-east-1")
        dynamodb = boto3.client("dynamodb", region_name="us-east-1")

        # Create IAM role
        iam.create_role(
            RoleName="ci-deployer",
            AssumeRolePolicyDocument=json.dumps({
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "ec2.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            }),
        )

        # Create DynamoDB audit table
        dynamodb.create_table(
            TableName="autoguardrails-audit",
            KeySchema=[{"AttributeName": "execution_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "execution_id", "AttributeType": "S"},
                {"AttributeName": "policy_id", "AttributeType": "S"},
                {"AttributeName": "executed_at", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "policy_id-executed_at-index",
                    "KeySchema": [
                        {"AttributeName": "policy_id", "KeyType": "HASH"},
                        {"AttributeName": "executed_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        # Budget event
        event = {
            "Records": [
                {
                    "EventSource": "aws:sns",
                    "Sns": {
                        "Message": json.dumps({
                            "budgetName": "monthly-budget",
                            "notificationType": "ACTUAL",
                            "thresholdType": "PERCENTAGE",
                            "threshold": 90,
                            "calculatedSpend": {
                                "actualSpend": {"amount": 600.0, "unit": "USD"}
                            },
                            "notificationArn": "arn:aws:budgets::123456789012:budget/monthly",
                            "time": "2024-01-15T10:30:00Z",
                        })
                    },
                }
            ]
        }

        context = MagicMock()

        with patch.dict(
            os.environ,
            {
                "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/test",
                "POLICIES_PATH": temp_policies_dir,
                "DYNAMODB_TABLE_NAME": "autoguardrails-audit",
                "AWS_DEFAULT_REGION": "us-east-1",
            },
        ):
            with patch("requests.post") as mock_post:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_post.return_value = mock_response

                # Execute budget event handler
                response = lambda_handler(event, context)

                # Verify Lambda response
                assert response["statusCode"] == 200
                body = json.loads(response["body"])
                assert body["status"] == "success"
                assert body["mode"] == "auto"
                assert body["policy_id"] == "test-auto-ec2-spike"
                assert "result" in body
                assert "execution_id" in body["result"]
                assert body["result"]["action"] == "executed"

                execution_id = body["result"]["execution_id"]

                # Verify IAM policy was attached
                policies = iam.list_attached_role_policies(RoleName="ci-deployer")
                attached_policies = policies["AttachedPolicies"]
                assert len(attached_policies) > 0

                # Find guardrails policy
                guardrails_policy = None
                for policy in attached_policies:
                    if "guardrails-deny" in policy["PolicyName"]:
                        guardrails_policy = policy
                        break

                assert guardrails_policy is not None

                # Verify policy document
                policy_arn = guardrails_policy["PolicyArn"]
                policy_version = iam.get_policy(PolicyArn=policy_arn)["Policy"][
                    "DefaultVersionId"
                ]
                policy_doc = iam.get_policy_version(
                    PolicyArn=policy_arn, VersionId=policy_version
                )["PolicyVersion"]["Document"]

                assert "Statement" in policy_doc
                statement = policy_doc["Statement"][0]
                assert statement["Effect"] == "Deny"
                assert "ec2:RunInstances" in statement["Action"]
                assert "ec2:CreateNatGateway" in statement["Action"]

                # Verify execution saved to DynamoDB
                from src.guardrails.audit_store import AuditStore

                audit_store = AuditStore(table_name="autoguardrails-audit")
                execution = audit_store.get_execution(execution_id)

                assert execution is not None
                assert execution.status == "executed"
                assert execution.policy_id == "test-auto-ec2-spike"
                assert execution.action == "attach_deny_policy"
                assert execution.executed_by == "system:auto"
                assert execution.ttl_expires_at is not None

                # Verify confirmation notification sent
                assert mock_post.call_count >= 1

    @mock_aws
    def test_auto_mode_with_ttl_cleanup(self, temp_policies_dir):
        """Test full TTL cleanup flow after auto mode execution.

        Flow:
        1. Auto mode executes guardrail
        2. TTL expires (simulated)
        3. TTL cleanup handler runs
        4. Policy is rolled back
        5. Execution status updated to 'rolled_back'
        """
        import boto3

        # Setup mocked AWS resources
        iam = boto3.client("iam", region_name="us-east-1")
        dynamodb = boto3.client("dynamodb", region_name="us-east-1")

        iam.create_role(
            RoleName="ci-deployer",
            AssumeRolePolicyDocument=json.dumps({
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "ec2.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            }),
        )

        dynamodb.create_table(
            TableName="autoguardrails-audit",
            KeySchema=[{"AttributeName": "execution_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "execution_id", "AttributeType": "S"},
                {"AttributeName": "policy_id", "AttributeType": "S"},
                {"AttributeName": "executed_at", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "policy_id-executed_at-index",
                    "KeySchema": [
                        {"AttributeName": "policy_id", "KeyType": "HASH"},
                        {"AttributeName": "executed_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        event = {
            "Records": [
                {
                    "EventSource": "aws:sns",
                    "Sns": {
                        "Message": json.dumps({
                            "budgetName": "monthly-budget",
                            "notificationType": "ACTUAL",
                            "thresholdType": "PERCENTAGE",
                            "threshold": 90,
                            "calculatedSpend": {
                                "actualSpend": {"amount": 600.0, "unit": "USD"}
                            },
                            "notificationArn": "arn:aws:budgets::123456789012:budget/monthly",
                            "time": "2024-01-15T10:30:00Z",
                        })
                    },
                }
            ]
        }

        context = MagicMock()

        # Step 1: Execute auto mode
        with patch.dict(
            os.environ,
            {
                "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/test",
                "POLICIES_PATH": temp_policies_dir,
                "DYNAMODB_TABLE_NAME": "autoguardrails-audit",
                "AWS_DEFAULT_REGION": "us-east-1",
            },
        ):
            with patch("requests.post") as mock_post:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_post.return_value = mock_response

                response = lambda_handler(event, context)
                body = json.loads(response["body"])
                execution_id = body["result"]["execution_id"]

                # Verify policy is attached
                policies_before = iam.list_attached_role_policies(RoleName="ci-deployer")
                assert len(policies_before["AttachedPolicies"]) > 0

        # Step 2: Simulate TTL expiration
        from src.guardrails.audit_store import AuditStore

        audit_store = AuditStore(table_name="autoguardrails-audit")
        execution = audit_store.get_execution(execution_id)

        # Force TTL to be expired
        execution.ttl_expires_at = datetime.utcnow() - timedelta(minutes=1)
        audit_store.update_execution(execution)

        # Step 3: Run TTL cleanup
        with patch.dict(
            os.environ,
            {
                "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/test",
                "DYNAMODB_TABLE_NAME": "autoguardrails-audit",
                "AWS_DEFAULT_REGION": "us-east-1",
            },
        ):
            with patch("requests.post") as mock_post:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_post.return_value = mock_response

                cleanup_handler = TTLCleanupHandler()
                cleanup_result = cleanup_handler.cleanup_expired_executions()

                # Verify cleanup results
                assert cleanup_result["total_found"] == 1
                assert cleanup_result["rolled_back"] == 1
                assert cleanup_result["failed"] == 0

                # Verify policy was detached
                policies_after = iam.list_attached_role_policies(RoleName="ci-deployer")
                assert len(policies_after["AttachedPolicies"]) == 0

                # Verify execution status updated
                execution = audit_store.get_execution(execution_id)
                assert execution.status == "rolled_back"
                assert execution.rolled_back_at is not None

                # Verify rollback notification sent
                assert mock_post.call_count >= 1

    @mock_aws
    def test_auto_mode_respects_ttl_zero(self, temp_policies_dir):
        """Test auto mode with TTL=0 (no auto-rollback)."""
        import boto3
        import yaml

        # Remove default policy and create policy with TTL=0
        policies_path = Path(temp_policies_dir)
        # Remove existing policy
        for policy_file in policies_path.glob("*.yaml"):
            policy_file.unlink()

        no_ttl_policy = {
            "policy_id": "test-auto-no-ttl",
            "enabled": True,
            "mode": "auto",
            "ttl_minutes": 0,  # No TTL
            "match": {
                "source": ["budgets"],
                "account_ids": ["123456789012"],
                "min_amount_usd": 500.0,
            },
            "scope": {
                "principals": [
                    {
                        "type": "iam_role",
                        "arn": "arn:aws:iam::123456789012:role/ci-deployer",
                    }
                ]
            },
            "actions": [
                {
                    "type": "attach_deny_policy",
                    "deny": ["ec2:RunInstances"],
                }
            ],
            "notify": {"slack_webhook_ssm_param": "/test/webhook"},
        }

        (policies_path / "auto-no-ttl.yaml").write_text(yaml.dump(no_ttl_policy))

        # Setup AWS
        iam = boto3.client("iam", region_name="us-east-1")
        dynamodb = boto3.client("dynamodb", region_name="us-east-1")

        iam.create_role(
            RoleName="ci-deployer",
            AssumeRolePolicyDocument=json.dumps({
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "ec2.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            }),
        )

        dynamodb.create_table(
            TableName="autoguardrails-audit",
            KeySchema=[{"AttributeName": "execution_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "execution_id", "AttributeType": "S"},
                {"AttributeName": "policy_id", "AttributeType": "S"},
                {"AttributeName": "executed_at", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "policy_id-executed_at-index",
                    "KeySchema": [
                        {"AttributeName": "policy_id", "KeyType": "HASH"},
                        {"AttributeName": "executed_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        event = {
            "Records": [
                {
                    "EventSource": "aws:sns",
                    "Sns": {
                        "Message": json.dumps({
                            "budgetName": "monthly-budget",
                            "notificationType": "ACTUAL",
                            "thresholdType": "PERCENTAGE",
                            "threshold": 90,
                            "calculatedSpend": {
                                "actualSpend": {"amount": 600.0, "unit": "USD"}
                            },
                            "notificationArn": "arn:aws:budgets::123456789012:budget/monthly",
                            "time": "2024-01-15T10:30:00Z",
                        })
                    },
                }
            ]
        }

        context = MagicMock()

        with patch.dict(
            os.environ,
            {
                "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/test",
                "POLICIES_PATH": temp_policies_dir,
                "DYNAMODB_TABLE_NAME": "autoguardrails-audit",
                "AWS_DEFAULT_REGION": "us-east-1",
            },
        ):
            with patch("requests.post") as mock_post:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_post.return_value = mock_response

                response = lambda_handler(event, context)
                body = json.loads(response["body"])

                # Verify execution created
                execution_id = body["result"]["execution_id"]
                assert body["result"]["ttl_expires_at"] is None

                # Verify execution has no TTL
                from src.guardrails.audit_store import AuditStore

                audit_store = AuditStore(table_name="autoguardrails-audit")
                execution = audit_store.get_execution(execution_id)
                assert execution.ttl_expires_at is None

                # TTL cleanup should not find this execution
                cleanup_handler = TTLCleanupHandler()
                cleanup_result = cleanup_handler.cleanup_expired_executions()
                assert cleanup_result["total_found"] == 0


class TestTTLCleanupIntegration:
    """Test TTL cleanup handler integration scenarios."""

    @mock_aws
    def test_ttl_cleanup_multiple_executions(self):
        """Test TTL cleanup with multiple expired executions."""
        import boto3

        from src.guardrails.audit_store import AuditStore
        from src.guardrails.models import ActionExecution

        # Setup AWS
        iam = boto3.client("iam", region_name="us-east-1")
        dynamodb = boto3.client("dynamodb", region_name="us-east-1")

        # Create roles
        for i in range(3):
            iam.create_role(
                RoleName=f"test-role-{i}",
                AssumeRolePolicyDocument=json.dumps({
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "ec2.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }),
            )

        dynamodb.create_table(
            TableName="autoguardrails-audit",
            KeySchema=[{"AttributeName": "execution_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "execution_id", "AttributeType": "S"},
                {"AttributeName": "policy_id", "AttributeType": "S"},
                {"AttributeName": "executed_at", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "policy_id-executed_at-index",
                    "KeySchema": [
                        {"AttributeName": "policy_id", "KeyType": "HASH"},
                        {"AttributeName": "executed_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        # Create executions and attach policies
        audit_store = AuditStore(table_name="autoguardrails-audit")

        for i in range(3):
            # Create and attach policy
            policy_name = f"guardrails-deny-test-{i}"
            policy_arn = iam.create_policy(
                PolicyName=policy_name,
                PolicyDocument=json.dumps({
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Deny",
                            "Action": ["ec2:RunInstances"],
                            "Resource": "*",
                        }
                    ],
                }),
            )["Policy"]["Arn"]

            iam.attach_role_policy(RoleName=f"test-role-{i}", PolicyArn=policy_arn)

            # Create execution record
            execution = ActionExecution(
                execution_id=f"exec-{i}",
                policy_id="test-policy",
                event_id=f"evt-{i}",
                status="executed",
                executed_at=datetime.utcnow() - timedelta(hours=2),
                executed_by="system:auto",
                action="attach_deny_policy",
                target=f"arn:aws:iam::123456789012:role/test-role-{i}",
                diff={
                    "before": [],
                    "after": [policy_arn],
                    "policy_arn": policy_arn,
                    "policy_name": policy_name,
                    "principal_type": "role",
                    "principal_name": f"test-role-{i}",
                    "principal_arn": f"arn:aws:iam::123456789012:role/test-role-{i}",
                    "denied_actions": ["ec2:RunInstances"],
                },
                ttl_expires_at=datetime.utcnow() - timedelta(minutes=1),
            )

            audit_store.save_execution(execution)

        # Run TTL cleanup
        with patch.dict(
            os.environ,
            {
                "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/test",
                "DYNAMODB_TABLE_NAME": "autoguardrails-audit",
                "AWS_DEFAULT_REGION": "us-east-1",
            },
        ):
            with patch("requests.post") as mock_post:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_post.return_value = mock_response

                cleanup_handler = TTLCleanupHandler()
                result = cleanup_handler.cleanup_expired_executions()

                # Verify all were rolled back
                assert result["total_found"] == 3
                assert result["rolled_back"] == 3
                assert result["failed"] == 0

                # Verify all policies detached
                for i in range(3):
                    policies = iam.list_attached_role_policies(RoleName=f"test-role-{i}")
                    assert len(policies["AttachedPolicies"]) == 0

    @mock_aws
    def test_ttl_cleanup_idempotency(self):
        """Test that running TTL cleanup twice is safe (idempotency)."""
        import boto3

        from src.guardrails.audit_store import AuditStore
        from src.guardrails.models import ActionExecution

        # Setup AWS
        iam = boto3.client("iam", region_name="us-east-1")
        dynamodb = boto3.client("dynamodb", region_name="us-east-1")

        iam.create_role(
            RoleName="test-role",
            AssumeRolePolicyDocument=json.dumps({
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "ec2.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            }),
        )

        dynamodb.create_table(
            TableName="autoguardrails-audit",
            KeySchema=[{"AttributeName": "execution_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "execution_id", "AttributeType": "S"},
                {"AttributeName": "policy_id", "AttributeType": "S"},
                {"AttributeName": "executed_at", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "policy_id-executed_at-index",
                    "KeySchema": [
                        {"AttributeName": "policy_id", "KeyType": "HASH"},
                        {"AttributeName": "executed_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        # Create execution and policy
        audit_store = AuditStore(table_name="autoguardrails-audit")

        policy_arn = iam.create_policy(
            PolicyName="guardrails-deny-test",
            PolicyDocument=json.dumps({
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Deny",
                        "Action": ["ec2:RunInstances"],
                        "Resource": "*",
                    }
                ],
            }),
        )["Policy"]["Arn"]

        iam.attach_role_policy(RoleName="test-role", PolicyArn=policy_arn)

        execution = ActionExecution(
            execution_id="exec-123",
            policy_id="test-policy",
            event_id="evt-456",
            status="executed",
            executed_at=datetime.utcnow() - timedelta(hours=2),
            executed_by="system:auto",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/test-role",
            diff={
                "before": [],
                "after": [policy_arn],
                "policy_arn": policy_arn,
                "policy_name": "guardrails-deny-test",
                "principal_type": "role",
                "principal_name": "test-role",
                "principal_arn": "arn:aws:iam::123456789012:role/test-role",
                "denied_actions": ["ec2:RunInstances"],
            },
            ttl_expires_at=datetime.utcnow() - timedelta(minutes=1),
        )

        audit_store.save_execution(execution)

        with patch.dict(
            os.environ,
            {
                "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/test",
                "DYNAMODB_TABLE_NAME": "autoguardrails-audit",
                "AWS_DEFAULT_REGION": "us-east-1",
            },
        ):
            with patch("requests.post") as mock_post:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_post.return_value = mock_response

                cleanup_handler = TTLCleanupHandler()

                # First run
                result1 = cleanup_handler.cleanup_expired_executions()
                assert result1["rolled_back"] == 1

                # Second run (should not find anything - status changed to rolled_back)
                result2 = cleanup_handler.cleanup_expired_executions()
                assert result2["total_found"] == 0  # Doesn't find it (status=rolled_back)
                assert result2["rolled_back"] == 0  # Nothing to roll back
                assert result2["skipped"] == 0  # Nothing to skip

                # Verify status
                execution = audit_store.get_execution("exec-123")
                assert execution.status == "rolled_back"
