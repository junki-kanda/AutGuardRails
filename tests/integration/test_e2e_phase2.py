"""End-to-End Integration Tests for Phase 2 (Manual Approval).

These tests verify the complete manual approval flow:
1. AWS Budget event arrives
2. Policy engine matches manual mode policy
3. Slack notification sent with approval button
4. ActionExecution saved to DynamoDB (audit trail)
5. User clicks approval button
6. Approval webhook verifies signature and executes guardrail
7. IAM deny policy attached to target principal
8. Confirmation notification sent to Slack
9. Audit trail updated

This validates that all Phase 2 components (IAM Executor, Audit Store, Approval Webhook)
work together correctly for the manual approval workflow.
"""

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from moto import mock_aws

from src.guardrails.handlers.approval_webhook import ApprovalWebhookHandler
from src.guardrails.handlers.budgets_event import lambda_handler


@pytest.fixture
def temp_policies_dir():
    """Create temporary directory with manual approval policy."""
    import yaml

    with tempfile.TemporaryDirectory() as tmpdir:
        policies_path = Path(tmpdir)

        # Create manual approval policy
        manual_policy = {
            "policy_id": "test-manual-ec2-spike",
            "enabled": True,
            "mode": "manual",
            "ttl_minutes": 180,
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

        (policies_path / "manual-approval.yaml").write_text(yaml.dump(manual_policy))

        yield str(policies_path)


class TestE2EManualApprovalFlow:
    """Test complete manual approval workflow."""

    @mock_aws
    def test_manual_approval_end_to_end(self, temp_policies_dir):
        """Test full manual approval flow from event to execution.

        Flow:
        1. Budget event triggers lambda
        2. Policy matches (manual mode)
        3. Execution saved to DynamoDB (status: planned)
        4. Slack notification sent with approval button
        5. User clicks approval
        6. Approval webhook executes guardrail
        7. IAM deny policy attached
        8. Execution updated (status: executed)
        9. Confirmation sent to Slack
        """
        import boto3

        # Setup mocked AWS resources
        iam = boto3.client("iam", region_name="us-east-1")
        dynamodb = boto3.client("dynamodb", region_name="us-east-1")

        # Create IAM role
        iam.create_role(
            RoleName="ci-deployer",
            AssumeRolePolicyDocument=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "ec2.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
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

        # === Step 1-4: Budget event → Policy match → Slack notification ===
        event = {
            "Records": [
                {
                    "EventSource": "aws:sns",
                    "Sns": {
                        "Message": json.dumps(
                            {
                                "budgetName": "monthly-budget",
                                "notificationType": "ACTUAL",
                                "thresholdType": "PERCENTAGE",
                                "threshold": 90,
                                "calculatedSpend": {
                                    "actualSpend": {"amount": 600.0, "unit": "USD"}
                                },
                                "notificationArn": "arn:aws:budgets::123456789012:budget/monthly",
                                "time": "2024-01-15T10:30:00Z",
                            }
                        )
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
                assert body["mode"] == "manual"
                assert body["policy_id"] == "test-manual-ec2-spike"
                assert "result" in body
                assert "execution_id" in body["result"]

                execution_id = body["result"]["execution_id"]

                # Verify Slack notification sent
                assert mock_post.call_count >= 1

                # Find the approval request notification
                approval_notification = None
                for call in mock_post.call_args_list:
                    payload = call[1]["json"]
                    blocks = payload.get("blocks", [])
                    for block in blocks:
                        if block.get("type") == "header":
                            if "Approval Required" in block.get("text", {}).get("text", ""):
                                approval_notification = payload
                                break

                assert approval_notification is not None, "No approval request notification found"

                # Verify approval button exists
                has_button = False
                for block in approval_notification["blocks"]:
                    if block.get("type") == "actions":
                        for element in block.get("elements", []):
                            if element.get("type") == "button":
                                has_button = True
                                break

                assert has_button, "No approval button found in notification"

                # Verify execution saved to DynamoDB
                from src.guardrails.audit_store import AuditStore

                audit_store = AuditStore(table_name="autoguardrails-audit")
                execution = audit_store.get_execution(execution_id)

                assert execution is not None
                assert execution.status == "planned"
                assert execution.policy_id == "test-manual-ec2-spike"
                assert execution.action == "attach_deny_policy"

        # === Step 5-9: User approval → Execution → Confirmation ===
        with patch.dict(
            os.environ,
            {
                "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/test",
                "APPROVAL_SECRET": "test-secret-key",
                "DYNAMODB_TABLE_NAME": "autoguardrails-audit",
                "AWS_DEFAULT_REGION": "us-east-1",
            },
        ):
            with patch("requests.post") as mock_post:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_post.return_value = mock_response

                # Generate approval URL
                handler = ApprovalWebhookHandler(approval_secret="test-secret-key")
                approval_data = handler.generate_approval_url(
                    execution_id=execution_id,
                    base_url="https://api.example.com",
                )

                # Simulate user clicking approval button
                approval_response = handler.handle_approval(
                    execution_id=execution_id,
                    signature=approval_data["signature"],
                    timestamp=approval_data["timestamp"],
                    user="alice",
                )

                # Verify approval succeeded
                assert approval_response["statusCode"] == 200
                assert "successfully" in approval_response["body"].lower()

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

                assert guardrails_policy is not None, "Guardrails policy not attached"

                # Verify policy document contains deny actions
                policy_arn = guardrails_policy["PolicyArn"]
                policy_version = iam.get_policy(PolicyArn=policy_arn)["Policy"]["DefaultVersionId"]
                policy_doc = iam.get_policy_version(PolicyArn=policy_arn, VersionId=policy_version)[
                    "PolicyVersion"
                ]["Document"]

                assert "Statement" in policy_doc
                statement = policy_doc["Statement"][0]
                assert statement["Effect"] == "Deny"
                assert "ec2:RunInstances" in statement["Action"]
                assert "ec2:CreateNatGateway" in statement["Action"]

                # Verify execution updated in DynamoDB
                execution = audit_store.get_execution(execution_id)
                assert execution.status == "executed"
                assert execution.executed_by == "user:alice"
                assert "before" in execution.diff
                assert "after" in execution.diff

                # Verify confirmation notification sent
                assert mock_post.call_count >= 1

    @mock_aws
    def test_approval_idempotency(self, temp_policies_dir):
        """Test that approving twice doesn't execute twice (idempotency)."""
        import boto3

        # Setup mocked AWS resources
        iam = boto3.client("iam", region_name="us-east-1")
        dynamodb = boto3.client("dynamodb", region_name="us-east-1")

        iam.create_role(
            RoleName="ci-deployer",
            AssumeRolePolicyDocument=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "ec2.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
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
                        "Message": json.dumps(
                            {
                                "budgetName": "monthly-budget",
                                "notificationType": "ACTUAL",
                                "thresholdType": "PERCENTAGE",
                                "threshold": 90,
                                "calculatedSpend": {
                                    "actualSpend": {"amount": 600.0, "unit": "USD"}
                                },
                                "notificationArn": "arn:aws:budgets::123456789012:budget/monthly",
                                "time": "2024-01-15T10:30:00Z",
                            }
                        )
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
                execution_id = body["result"]["execution_id"]

        # First approval
        with patch.dict(
            os.environ,
            {
                "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/test",
                "APPROVAL_SECRET": "test-secret",
                "DYNAMODB_TABLE_NAME": "autoguardrails-audit",
                "AWS_DEFAULT_REGION": "us-east-1",
            },
        ):
            with patch("requests.post") as mock_post:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_post.return_value = mock_response

                handler = ApprovalWebhookHandler(approval_secret="test-secret")
                approval_data = handler.generate_approval_url(
                    execution_id=execution_id, base_url="https://api.example.com"
                )

                response1 = handler.handle_approval(
                    execution_id=execution_id,
                    signature=approval_data["signature"],
                    timestamp=approval_data["timestamp"],
                    user="alice",
                )

                assert response1["statusCode"] == 200

                # Get policy count after first approval
                policies1 = iam.list_attached_role_policies(RoleName="ci-deployer")
                policy_count1 = len(policies1["AttachedPolicies"])

                # Second approval (should be rejected)
                response2 = handler.handle_approval(
                    execution_id=execution_id,
                    signature=approval_data["signature"],
                    timestamp=approval_data["timestamp"],
                    user="alice",
                )

                assert response2["statusCode"] == 409  # Conflict (already processed)
                assert "already processed" in response2["body"].lower()

                # Verify policy count didn't increase
                policies2 = iam.list_attached_role_policies(RoleName="ci-deployer")
                policy_count2 = len(policies2["AttachedPolicies"])
                assert policy_count1 == policy_count2

    @mock_aws
    def test_approval_link_expiration(self, temp_policies_dir):
        """Test that expired approval links are rejected."""
        import boto3

        dynamodb = boto3.client("dynamodb", region_name="us-east-1")
        iam = boto3.client("iam", region_name="us-east-1")

        iam.create_role(
            RoleName="ci-deployer",
            AssumeRolePolicyDocument=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "ec2.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
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
                        "Message": json.dumps(
                            {
                                "budgetName": "monthly-budget",
                                "notificationType": "ACTUAL",
                                "thresholdType": "PERCENTAGE",
                                "threshold": 90,
                                "calculatedSpend": {
                                    "actualSpend": {"amount": 600.0, "unit": "USD"}
                                },
                                "notificationArn": "arn:aws:budgets::123456789012:budget/monthly",
                                "time": "2024-01-15T10:30:00Z",
                            }
                        )
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
                execution_id = body["result"]["execution_id"]

        # Try approval with expired timestamp (2 hours ago)
        with patch.dict(
            os.environ,
            {
                "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/test",
                "APPROVAL_SECRET": "test-secret",
                "DYNAMODB_TABLE_NAME": "autoguardrails-audit",
                "AWS_DEFAULT_REGION": "us-east-1",
            },
        ):
            handler = ApprovalWebhookHandler(
                approval_secret="test-secret", approval_timeout_hours=1
            )

            # Generate signature with old timestamp
            old_timestamp = (datetime.utcnow() - timedelta(hours=2)).isoformat()
            signature = handler._generate_signature(execution_id, old_timestamp)

            response = handler.handle_approval(
                execution_id=execution_id,
                signature=signature,
                timestamp=old_timestamp,
                user="alice",
            )

            assert response["statusCode"] == 410  # Gone (expired)
            assert "expired" in response["body"].lower()

            # Verify no policy was attached
            policies = iam.list_attached_role_policies(RoleName="ci-deployer")
            assert len(policies["AttachedPolicies"]) == 0

    @mock_aws
    def test_approval_invalid_signature(self, temp_policies_dir):
        """Test that invalid signatures are rejected."""
        import boto3

        dynamodb = boto3.client("dynamodb", region_name="us-east-1")
        iam = boto3.client("iam", region_name="us-east-1")

        iam.create_role(
            RoleName="ci-deployer",
            AssumeRolePolicyDocument=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "ec2.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
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
                        "Message": json.dumps(
                            {
                                "budgetName": "monthly-budget",
                                "notificationType": "ACTUAL",
                                "thresholdType": "PERCENTAGE",
                                "threshold": 90,
                                "calculatedSpend": {
                                    "actualSpend": {"amount": 600.0, "unit": "USD"}
                                },
                                "notificationArn": "arn:aws:budgets::123456789012:budget/monthly",
                                "time": "2024-01-15T10:30:00Z",
                            }
                        )
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
                execution_id = body["result"]["execution_id"]

        # Try approval with invalid signature
        with patch.dict(
            os.environ,
            {
                "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/test",
                "APPROVAL_SECRET": "test-secret",
                "DYNAMODB_TABLE_NAME": "autoguardrails-audit",
                "AWS_DEFAULT_REGION": "us-east-1",
            },
        ):
            handler = ApprovalWebhookHandler(approval_secret="test-secret")

            timestamp = datetime.utcnow().isoformat()
            invalid_signature = "invalid-signature-12345"

            response = handler.handle_approval(
                execution_id=execution_id,
                signature=invalid_signature,
                timestamp=timestamp,
                user="alice",
            )

            assert response["statusCode"] == 403  # Forbidden
            assert "invalid signature" in response["body"].lower()

            # Verify no policy was attached
            policies = iam.list_attached_role_policies(RoleName="ci-deployer")
            assert len(policies["AttachedPolicies"]) == 0
