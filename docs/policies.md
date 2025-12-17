# Policy Specification

## 0. Overview

AutoGuardRails uses **YAML-based policies** to define when and how to respond to cost events.

A policy specifies:
- **Match conditions**: What cost events trigger this policy
- **Scope**: Which AWS resources are affected
- **Actions**: What guardrails to apply
- **Mode**: How to execute (dry-run, manual approval, or automatic)
- **TTL**: How long before automatic rollback

---

## 1. Policy Schema

### 1.1 Complete Example

```yaml
# policies/budget-spike-ci-quarantine.yaml
policy_id: "budget-spike-ci-quarantine"
description: "Quarantine CI deployment role when costs spike"
enabled: true

# Execution mode
mode: "manual"          # dry_run | manual | auto
ttl_minutes: 180        # Auto-rollback after 3 hours (0 = manual rollback only)

# Match conditions
match:
  source: ["budgets", "anomaly"]  # Cost event sources
  account_ids:
    - "123456789012"
  min_amount_usd: 200    # Trigger if cost >= $200
  max_amount_usd: null   # Optional upper limit
  services:              # Optional service filter
    - "Amazon Elastic Compute Cloud"
  regions:               # Optional region filter
    - "us-east-1"
    - "ap-northeast-1"

# Target scope (what to restrict)
scope:
  principals:
    - type: "iam_role"
      arn: "arn:aws:iam::123456789012:role/ci-deployer"
    - type: "iam_role"
      arn: "arn:aws:iam::123456789012:role/github-actions"
  regions:
    - "ap-northeast-1"

# Actions to execute
actions:
  - type: "attach_deny_policy"
    deny:
      - "ec2:RunInstances"
      - "ec2:CreateNatGateway"
      - "ec2:CreateVpc"
      - "ec2:AllocateAddress"

# Notification settings
notify:
  slack_webhook_ssm_param: "/guardrails/slack_webhook"
  channel_hint: "#cost-alerts"
  mention_users: ["@platform-team"]

# Exceptions (optional)
exceptions:
  accounts: []           # Exempt account IDs
  principals: []         # Exempt principal ARNs
  time_windows:          # Don't execute during these times
    - start: "09:00"
      end: "17:00"
      timezone: "Asia/Tokyo"
      days: ["mon", "tue", "wed", "thu", "fri"]
```

---

## 2. Field Reference

### 2.1 Top-Level Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `policy_id` | string | ✅ | Unique identifier (alphanumeric, hyphens, underscores) |
| `description` | string | ❌ | Human-readable description |
| `enabled` | boolean | ❌ | Default: `true`. Set to `false` to disable without deleting |
| `mode` | enum | ✅ | `dry_run`, `manual`, or `auto` |
| `ttl_minutes` | integer | ✅ | Minutes until auto-rollback (0 = manual only) |
| `match` | object | ✅ | Match conditions for cost events |
| `scope` | object | ✅ | Target resources to restrict |
| `actions` | array | ✅ | List of actions to execute |
| `notify` | object | ✅ | Notification settings |
| `exceptions` | object | ❌ | Allowlist/exception rules |

---

### 2.2 Mode Field

```yaml
mode: "dry_run"  # or "manual" or "auto"
```

**Values**:
- **`dry_run`** (default, safest):
  - Evaluates policy and generates action plan
  - Sends Slack notification with "what would happen"
  - Does NOT execute any AWS API calls
  - Use for: Testing, monitoring, non-critical policies

- **`manual`**:
  - Sends Slack notification with "Approve" button
  - Waits for human approval (1 hour timeout)
  - Executes action only if approved
  - Use for: Production-affecting policies, learning phase

- **`auto`**:
  - Automatically executes action without approval
  - Sends Slack notification after execution
  - Use for: Well-tested policies, non-critical roles, after confidence is established

**Recommendation**: Start with `dry_run` for 1 week, then `manual` for 1 week, then `auto` if comfortable.

---

### 2.3 Match Conditions

```yaml
match:
  source: ["budgets", "anomaly"]
  account_ids: ["123456789012"]
  min_amount_usd: 200
  max_amount_usd: 1000
  services: ["Amazon Elastic Compute Cloud"]
  regions: ["us-east-1"]
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `source` | array[string] | ✅ | Event sources: `budgets`, `anomaly` |
| `account_ids` | array[string] | ✅ | AWS account IDs to match (12 digits) |
| `min_amount_usd` | number | ✅ | Minimum cost in USD to trigger |
| `max_amount_usd` | number | ❌ | Maximum cost in USD (optional upper bound) |
| `services` | array[string] | ❌ | AWS service names (e.g., "Amazon EC2") |
| `regions` | array[string] | ❌ | AWS regions (e.g., "us-east-1") |

**Matching Logic**: All specified conditions must be satisfied (AND logic).

**Examples**:
```yaml
# Match any budget event over $100
match:
  source: ["budgets"]
  account_ids: ["123456789012"]
  min_amount_usd: 100

# Match EC2 costs in us-east-1 between $200-$500
match:
  source: ["budgets", "anomaly"]
  account_ids: ["123456789012"]
  min_amount_usd: 200
  max_amount_usd: 500
  services: ["Amazon Elastic Compute Cloud"]
  regions: ["us-east-1"]
```

---

### 2.4 Scope (Target Resources)

```yaml
scope:
  principals:
    - type: "iam_role"
      arn: "arn:aws:iam::123456789012:role/ci-deployer"
  regions: ["ap-northeast-1"]
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `principals` | array[object] | ✅ | IAM principals to restrict |
| `principals[].type` | enum | ✅ | `iam_role` or `iam_user` |
| `principals[].arn` | string | ✅ | Full ARN of principal |
| `regions` | array[string] | ❌ | Regions to restrict (optional) |

**Safety Rule**: NEVER use wildcards (`*`) in principal ARNs. Always specify exact ARNs.

**Examples**:
```yaml
# Single role
scope:
  principals:
    - type: "iam_role"
      arn: "arn:aws:iam::123456789012:role/ci-deployer"

# Multiple roles
scope:
  principals:
    - type: "iam_role"
      arn: "arn:aws:iam::123456789012:role/ci-deployer"
    - type: "iam_role"
      arn: "arn:aws:iam::123456789012:role/github-actions"
    - type: "iam_user"
      arn: "arn:aws:iam::123456789012:user/dev-sandbox"
```

---

### 2.5 Actions

```yaml
actions:
  - type: "attach_deny_policy"
    deny:
      - "ec2:RunInstances"
      - "ec2:CreateNatGateway"
```

**Supported Action Types** (MVP):

#### `attach_deny_policy`
Attaches an IAM deny policy to the target principal.

```yaml
actions:
  - type: "attach_deny_policy"
    deny:
      - "ec2:RunInstances"
      - "ec2:CreateNatGateway"
      - "ec2:CreateVpc"
      - "rds:CreateDBInstance"
```

**Behavior**:
- Creates a managed policy named `guardrails-deny-<hash>`
- Policy denies specified actions on all resources (`Resource: "*"`)
- Attaches policy to target principal(s)
- Existing resources are NOT affected (only new creation)
- Fully reversible (detach policy)

**Safety**: Does NOT delete or stop existing resources.

---

#### `notify_only`
Sends notification without executing any AWS actions.

```yaml
actions:
  - type: "notify_only"
```

**Behavior**:
- Sends Slack notification with event details
- No AWS API calls
- Always safe

---

### 2.6 Notification Settings

```yaml
notify:
  slack_webhook_ssm_param: "/guardrails/slack_webhook"
  channel_hint: "#cost-alerts"
  mention_users: ["@platform-team", "@oncall"]
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `slack_webhook_ssm_param` | string | ✅ | SSM parameter path containing webhook URL |
| `channel_hint` | string | ❌ | Channel name (informational, for display) |
| `mention_users` | array[string] | ❌ | Slack users/groups to mention (e.g., `@user`) |

**SSM Parameter Setup**:
```bash
aws ssm put-parameter \
  --name /guardrails/slack_webhook \
  --value "https://hooks.slack.com/services/T00/B00/XXX" \
  --type SecureString
```

---

### 2.7 Exceptions (Allowlist)

```yaml
exceptions:
  accounts: ["999888777666"]
  principals:
    - "arn:aws:iam::*:role/admin"
    - "arn:aws:iam::123456789012:role/production-*"
  time_windows:
    - start: "09:00"
      end: "17:00"
      timezone: "Asia/Tokyo"
      days: ["mon", "tue", "wed", "thu", "fri"]
```

| Field | Type | Description |
|-------|------|-------------|
| `accounts` | array[string] | Account IDs to always exempt |
| `principals` | array[string] | Principal ARNs to always exempt (supports `*` suffix) |
| `time_windows` | array[object] | Time periods when policy should NOT execute |
| `time_windows[].start` | string | Start time (HH:MM, 24-hour format) |
| `time_windows[].end` | string | End time (HH:MM, 24-hour format) |
| `time_windows[].timezone` | string | IANA timezone (e.g., "Asia/Tokyo", "UTC") |
| `time_windows[].days` | array[string] | Days of week: `mon`, `tue`, `wed`, `thu`, `fri`, `sat`, `sun` |

**Use Cases**:
- **Production accounts**: Never restrict
- **Admin roles**: Always allow full access
- **Business hours**: Don't restrict during working hours (deployments expected)

---

## 3. Policy Validation

### 3.1 Validation Rules

Policies are validated on load. Invalid policies are rejected with error messages.

**Required Field Validation**:
```python
# All required fields must be present
required_fields = ["policy_id", "mode", "ttl_minutes", "match", "scope", "actions", "notify"]
```

**Type Validation**:
```python
# mode must be one of the allowed values
assert policy.mode in ["dry_run", "manual", "auto"]

# ttl_minutes must be >= 0
assert policy.ttl_minutes >= 0

# account_ids must be 12 digits
assert all(len(account_id) == 12 for account_id in policy.match.account_ids)
```

**Logical Validation**:
```python
# min_amount_usd must be positive
assert policy.match.min_amount_usd > 0

# max_amount_usd (if set) must be > min_amount_usd
if policy.match.max_amount_usd:
    assert policy.match.max_amount_usd > policy.match.min_amount_usd

# principals list cannot be empty
assert len(policy.scope.principals) > 0

# actions list cannot be empty
assert len(policy.actions) > 0
```

**Safety Validation**:
```python
# No wildcard principals
for principal in policy.scope.principals:
    assert "*" not in principal.arn

# No dangerous actions in deny list
dangerous_actions = ["s3:DeleteBucket", "dynamodb:DeleteTable", "rds:DeleteDBInstance"]
for action in policy.actions:
    if action.type == "attach_deny_policy":
        assert not any(d in action.deny for d in dangerous_actions)
```

---

### 3.2 Example: Invalid Policies

**❌ Invalid: Missing required fields**
```yaml
policy_id: "invalid-1"
# Missing: mode, ttl_minutes, match, scope, actions, notify
```
**Error**: `Missing required field: mode`

---

**❌ Invalid: Wildcard principal**
```yaml
policy_id: "invalid-2"
mode: "auto"
scope:
  principals:
    - type: "iam_role"
      arn: "arn:aws:iam::123456789012:role/*"  # ❌ Wildcard
```
**Error**: `Wildcard principals not allowed in scope`

---

**❌ Invalid: Dangerous action**
```yaml
policy_id: "invalid-3"
actions:
  - type: "attach_deny_policy"
    deny:
      - "s3:DeleteBucket"  # ❌ Data deletion
```
**Error**: `Dangerous action 's3:DeleteBucket' not allowed`

---

**❌ Invalid: max < min**
```yaml
policy_id: "invalid-4"
match:
  min_amount_usd: 500
  max_amount_usd: 200  # ❌ Backwards
```
**Error**: `max_amount_usd must be greater than min_amount_usd`

---

## 4. Policy Examples

### 4.1 Example 1: Dry-Run Monitoring (Free Tier)

**Use Case**: Monitor all budget alerts, notify only

```yaml
policy_id: "free-tier-monitoring"
description: "Free tier: Notify on all budget alerts"
enabled: true
mode: "dry_run"
ttl_minutes: 0  # N/A for dry-run

match:
  source: ["budgets"]
  account_ids: ["123456789012"]
  min_amount_usd: 0  # Match any amount

scope:
  principals: []  # N/A for dry-run

actions:
  - type: "notify_only"

notify:
  slack_webhook_ssm_param: "/guardrails/slack_webhook"
  channel_hint: "#cost-alerts"
```

---

### 4.2 Example 2: CI Role Quarantine (Manual Approval)

**Use Case**: Restrict CI role when EC2 costs spike, require approval

```yaml
policy_id: "ci-ec2-spike-manual"
description: "Quarantine CI role on EC2 spike (manual approval)"
enabled: true
mode: "manual"
ttl_minutes: 180

match:
  source: ["budgets", "anomaly"]
  account_ids: ["123456789012"]
  min_amount_usd: 200
  services: ["Amazon Elastic Compute Cloud"]

scope:
  principals:
    - type: "iam_role"
      arn: "arn:aws:iam::123456789012:role/ci-deployer"

actions:
  - type: "attach_deny_policy"
    deny:
      - "ec2:RunInstances"
      - "ec2:CreateNatGateway"

notify:
  slack_webhook_ssm_param: "/guardrails/slack_webhook"
  channel_hint: "#platform-alerts"
  mention_users: ["@platform-team"]
```

---

### 4.3 Example 3: Sandbox Auto-Quarantine

**Use Case**: Automatically restrict sandbox role during off-hours

```yaml
policy_id: "sandbox-auto-quarantine"
description: "Auto-quarantine sandbox role on cost spike"
enabled: true
mode: "auto"
ttl_minutes: 60  # Short TTL for sandbox

match:
  source: ["budgets"]
  account_ids: ["123456789012"]
  min_amount_usd: 50  # Lower threshold for sandbox

scope:
  principals:
    - type: "iam_role"
      arn: "arn:aws:iam::123456789012:role/dev-sandbox"

actions:
  - type: "attach_deny_policy"
    deny:
      - "ec2:RunInstances"
      - "ec2:CreateVpc"
      - "rds:CreateDBInstance"

notify:
  slack_webhook_ssm_param: "/guardrails/slack_webhook"
  channel_hint: "#dev-alerts"

exceptions:
  time_windows:
    - start: "09:00"
      end: "18:00"
      timezone: "UTC"
      days: ["mon", "tue", "wed", "thu", "fri"]
```

---

### 4.4 Example 4: Multi-Role Protection

**Use Case**: Protect multiple CI/CD roles

```yaml
policy_id: "multi-role-protection"
description: "Protect all CI/CD roles from runaway costs"
enabled: true
mode: "manual"
ttl_minutes: 240

match:
  source: ["budgets", "anomaly"]
  account_ids: ["123456789012"]
  min_amount_usd: 300

scope:
  principals:
    - type: "iam_role"
      arn: "arn:aws:iam::123456789012:role/ci-deployer"
    - type: "iam_role"
      arn: "arn:aws:iam::123456789012:role/github-actions"
    - type: "iam_role"
      arn: "arn:aws:iam::123456789012:role/jenkins"

actions:
  - type: "attach_deny_policy"
    deny:
      - "ec2:RunInstances"
      - "ec2:CreateNatGateway"
      - "ec2:AllocateAddress"

notify:
  slack_webhook_ssm_param: "/guardrails/slack_webhook"
  channel_hint: "#cicd-alerts"
  mention_users: ["@devops", "@platform"]

exceptions:
  accounts: ["999888777666"]  # Production account exempt
```

---

## 5. Policy Management

### 5.1 Directory Structure

```
policies/
├── example-dry-run.yaml
├── ci-quarantine.yaml
├── sandbox-auto.yaml
└── disabled/
    └── old-policy.yaml  # Disabled policies
```

**Conventions**:
- One policy per file
- Filename = `policy_id.yaml`
- Disabled policies moved to `disabled/` subdirectory

---

### 5.2 Loading Policies

Policies are loaded from the `POLICY_DIR` environment variable (default: `./policies`).

```python
# policy_engine.py
def load_policies(policy_dir: str) -> List[GuardrailPolicy]:
    policies = []
    for yaml_file in Path(policy_dir).glob("*.yaml"):
        with open(yaml_file) as f:
            data = yaml.safe_load(f)
            policy = GuardrailPolicy(**data)
            if policy.enabled:
                policies.append(policy)
    return policies
```

---

### 5.3 Updating Policies

**To modify a policy**:
1. Edit YAML file
2. Validate locally:
   ```bash
   python -m src.guardrails.policy_engine --validate policies/my-policy.yaml
   ```
3. Deploy (re-deploy Lambda or restart service)
4. Monitor Slack for validation errors

**To disable a policy**:
```yaml
enabled: false
```
Or move to `policies/disabled/`.

---

## 6. Testing Policies

### 6.1 Dry-Run Testing

Test policy matching without executing actions:

```bash
# Simulate cost event
python -m src.guardrails.policy_engine \
  --policy policies/ci-quarantine.yaml \
  --event events/sample-budgets-event.json \
  --dry-run
```

**Expected Output**:
```
✅ Policy matched: ci-ec2-spike-manual
📋 Action plan:
  - Type: attach_deny_policy
  - Target: arn:aws:iam::123456789012:role/ci-deployer
  - Deny actions: ec2:RunInstances, ec2:CreateNatGateway
  - Mode: manual (requires approval)
  - TTL: 180 minutes
```

---

### 6.2 Integration Testing

Full E2E test with mocked AWS/Slack:

```python
# tests/integration/test_policy_evaluation.py
def test_ci_quarantine_policy():
    # Load policy
    policy = load_policy("policies/ci-quarantine.yaml")

    # Create test event
    event = CostEvent(
        event_id="test-123",
        source="budgets",
        account_id="123456789012",
        amount=250.0,
        time_window="2025-01",
        details={"service": "Amazon Elastic Compute Cloud"}
    )

    # Evaluate
    engine = PolicyEngine()
    plan = engine.evaluate(event, [policy])

    # Assert
    assert plan.matched_policy_id == "ci-ec2-spike-manual"
    assert plan.mode == "manual"
    assert len(plan.actions) == 1
    assert plan.actions[0].type == "attach_deny_policy"
```

---

## 7. Troubleshooting

### 7.1 Policy Not Triggering

**Symptoms**: Cost event occurs but policy doesn't match

**Debug Steps**:
1. Check policy is `enabled: true`
2. Verify match conditions:
   ```python
   # Add logging
   logger.info(f"Event account: {event.account_id}")
   logger.info(f"Policy accounts: {policy.match.account_ids}")
   logger.info(f"Event amount: {event.amount}")
   logger.info(f"Policy min amount: {policy.match.min_amount_usd}")
   ```
3. Check exceptions (time windows, allowlist)

---

### 7.2 Policy Validation Errors

**Symptoms**: Policy file rejected on load

**Common Errors**:
- YAML syntax error (use `yamllint policies/*.yaml`)
- Missing required field (check schema)
- Invalid ARN format
- Wildcard in principal ARN

**Fix**: Check CloudWatch logs for detailed error message.

---

## 8. Future Enhancements

### 8.1 Planned Features (Post-MVP)

**Advanced Match Conditions**:
```yaml
match:
  cost_rate_change: ">200%"  # Spike detection
  forecast_overage: true     # Predicted budget breach
  tags:
    - key: "Environment"
      value: "dev"
```

**Advanced Actions**:
```yaml
actions:
  - type: "set_permission_boundary"
    boundary_arn: "arn:aws:iam::123456789012:policy/guardrails-boundary"

  - type: "send_sns"
    topic_arn: "arn:aws:sns:us-east-1:123456789012:escalation"
```

**Policy Inheritance**:
```yaml
extends: "base-policy.yaml"  # Inherit from base
overrides:
  ttl_minutes: 120
```

---

**Last Updated:** 2025-01-15
**Version:** 1.0 (MVP)
**Status:** Implementation Reference
