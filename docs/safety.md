# Safety Principles & IAM Permissions

## 0. Safety-First Philosophy

**AutoGuardRails is designed to prevent cost overruns WITHOUT causing outages or data loss.**

Core principle: **"First, do no harm"**

All guardrail actions must be:
1. **Reversible** - Can be undone without data loss
2. **Scoped** - Target specific resources, not entire accounts
3. **Observable** - Fully logged and auditable
4. **Gradual** - Default to dry-run, escalate with approval

---

## 1. Safety Principles

### 1.1 Default to Dry-Run

**Rule**: No actions execute without explicit opt-in

```yaml
# Default behavior (SAFE)
mode: "dry_run"  # Only notifies, never executes

# Requires explicit configuration (CAREFUL)
mode: "manual"   # Requires approval
mode: "auto"     # Executes automatically (use with caution!)
```

**Why**: Prevents accidental disruption during initial setup and testing.

---

### 1.2 Scope Minimization

**Rule**: Never apply guardrails to entire AWS accounts

**Bad ❌**:
```yaml
scope:
  principals: ["*"]  # DANGEROUS: Affects all roles
```

**Good ✅**:
```yaml
scope:
  principals:
    - type: "iam_role"
      arn: "arn:aws:iam::123456789012:role/ci-deployer"
    - type: "iam_role"
      arn: "arn:aws:iam::123456789012:role/dev-sandbox"
```

**Why**: Limits blast radius. If a guardrail is too aggressive, it only affects specific roles, not production systems.

---

### 1.3 Easy Rollback

**Rule**: All actions must be reversible, with automatic TTL expiration

**Implementation**:
```yaml
ttl_minutes: 180  # Auto-rollback after 3 hours
```

**Mechanisms**:
1. **TTL Cleanup**: Scheduled Lambda (every 5 minutes) removes expired policies
2. **Manual Rollback**: API/CLI to immediately remove guardrails
3. **Audit Trail**: `ActionExecution.diff` stores original state

**Why**: Mistakes happen. Guardrails should be temporary by default.

---

### 1.4 Allowlist Support

**Rule**: Critical systems must be exemptable

**Example**:
```yaml
exceptions:
  accounts: ["999888777666"]  # Production account
  principals:
    - "arn:aws:iam::*:role/admin"
  time_windows:
    - start: "09:00"  # Business hours
      end: "17:00"
      timezone: "Asia/Tokyo"
```

**Why**: Production systems, admin roles, and business-critical operations should never be restricted.

---

### 1.5 Two-Stage Approval (Manual Mode)

**Rule**: High-impact actions require human approval

**Flow**:
1. Cost event triggers policy match
2. Slack notification sent with "Approve" button
3. Human reviews context and approves/rejects
4. Action executes only if approved within 1 hour

**Why**: Prevents automation from causing unintended disruption.

---

### 1.6 Mandatory Audit Logs

**Rule**: Every action must be logged with full context

**Required Fields**:
```python
{
    "execution_id": "uuid",
    "policy_id": "budget-spike-ci-quarantine",
    "event_id": "cost-event-123",
    "executed_at": "2025-01-15T10:30:00Z",
    "executed_by": "user@example.com",  # or "system:auto"
    "action": "attach_deny_policy",
    "target": "arn:aws:iam::123456789012:role/ci-deployer",
    "status": "executed",
    "diff": {
        "before": [],
        "after": ["arn:aws:iam::123456789012:policy/guardrails-deny-ec2"]
    },
    "ttl_expires_at": "2025-01-15T13:30:00Z",
    "rolled_back_at": null
}
```

**Why**: Compliance, debugging, and accountability.

---

## 2. Prohibited Actions (MVP)

### 2.1 Never Allowed

These actions are **NEVER** implemented, even in future versions:

❌ **Data Deletion**
- `s3:DeleteBucket`
- `dynamodb:DeleteTable`
- `rds:DeleteDBInstance` (without snapshots)

❌ **Account-Level Restrictions**
- SCPs that affect all accounts (without explicit org-level opt-in)
- Root user restrictions

❌ **Critical Service Disruption**
- Stopping production databases
- Terminating running EC2 instances (in MVP)
- Deleting VPCs with active resources

**Why**: Risk of data loss or production outages far exceeds cost savings.

---

### 2.2 Restricted to Phase 3+ (Not in MVP)

These require additional safety mechanisms:

⚠️ **Resource Termination** (requires multi-step approval + backup verification)
- `ec2:TerminateInstances`
- `ec2:StopInstances`
- `autoscaling:SetDesiredCapacity` (scale to 0)

⚠️ **SCP Application** (requires organization-level approval)
- Service Control Policies affecting multiple accounts

**Why**: Higher risk, needs more robust safeguards.

---

## 3. Allowed Actions (MVP)

### 3.1 Safe Actions (Reversible, Read-Only Impact)

✅ **IAM Deny Policy Attachment** (MVP, recommended)
```python
actions:
  - type: "attach_deny_policy"
    deny:
      - "ec2:RunInstances"
      - "ec2:CreateNatGateway"
      - "ec2:CreateVpc"
```

**Safety**:
- Does NOT delete resources
- Does NOT stop running instances
- Only prevents NEW resource creation
- Fully reversible (detach policy)

---

✅ **Permission Boundary Modification** (Phase 2)
```python
actions:
  - type: "set_permission_boundary"
    boundary: "arn:aws:iam::123456789012:policy/guardrails-boundary"
```

**Safety**:
- Only restricts permissions, doesn't revoke
- Existing resources unaffected
- Reversible (remove boundary)

---

✅ **Notification Only** (Free Tier)
```python
actions:
  - type: "notify_only"
```

**Safety**:
- Zero risk (no AWS API calls)
- Always safe for testing

---

## 4. IAM Permissions Design

### 4.1 Separation of Concerns

**Read-Only Role** (Detection)
- Name: `AutoGuardRails-ReadOnly`
- Purpose: Detect cost anomalies, evaluate policies
- Permissions: See Section 4.2

**Write Role** (Execution)
- Name: `AutoGuardRails-Executor`
- Purpose: Apply guardrails (attach IAM policies)
- Permissions: See Section 4.3
- **Default**: Disabled (must be explicitly enabled)

---

### 4.2 Read-Only IAM Policy

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CostExplorerRead",
      "Effect": "Allow",
      "Action": [
        "ce:GetAnomalies",
        "ce:GetCostForecast",
        "ce:GetCostAndUsage"
      ],
      "Resource": "*"
    },
    {
      "Sid": "BudgetsRead",
      "Effect": "Allow",
      "Action": [
        "budgets:ViewBudget",
        "budgets:DescribeBudgetAction"
      ],
      "Resource": "*"
    },
    {
      "Sid": "SNSPublish",
      "Effect": "Allow",
      "Action": [
        "sns:Publish"
      ],
      "Resource": "arn:aws:sns:*:*:autoguardrails-*"
    },
    {
      "Sid": "SSMParameterRead",
      "Effect": "Allow",
      "Action": [
        "ssm:GetParameter"
      ],
      "Resource": "arn:aws:ssm:*:*:parameter/guardrails/*"
    },
    {
      "Sid": "DynamoDBAuditRead",
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem",
        "dynamodb:Query",
        "dynamodb:Scan"
      ],
      "Resource": "arn:aws:dynamodb:*:*:table/autoguardrails-*"
    }
  ]
}
```

**Rationale**: Allows cost monitoring and policy evaluation without any write access.

---

### 4.3 Executor IAM Policy (Opt-In Required)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "IAMPolicyManagement",
      "Effect": "Allow",
      "Action": [
        "iam:CreatePolicy",
        "iam:DeletePolicy",
        "iam:GetPolicy",
        "iam:GetPolicyVersion",
        "iam:ListPolicyVersions"
      ],
      "Resource": "arn:aws:iam::*:policy/guardrails-*",
      "Condition": {
        "StringEquals": {
          "iam:PolicyName": "guardrails-*"
        }
      }
    },
    {
      "Sid": "IAMRolePolicyAttachment",
      "Effect": "Allow",
      "Action": [
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy",
        "iam:ListAttachedRolePolicies"
      ],
      "Resource": [
        "arn:aws:iam::*:role/ci-*",
        "arn:aws:iam::*:role/dev-*",
        "arn:aws:iam::*:role/sandbox-*"
      ]
    },
    {
      "Sid": "DynamoDBAuditWrite",
      "Effect": "Allow",
      "Action": [
        "dynamodb:PutItem",
        "dynamodb:UpdateItem"
      ],
      "Resource": "arn:aws:dynamodb:*:*:table/autoguardrails-*"
    }
  ]
}
```

**Safety Features**:
1. **Policy Name Restriction**: Only `guardrails-*` policies
2. **Role Prefix Restriction**: Only `ci-*`, `dev-*`, `sandbox-*` roles
3. **No Wildcard Resources**: Explicit role/policy ARN patterns
4. **No Admin Permissions**: Cannot touch `admin`, `production`, or `root` roles

**Important**: This policy does NOT allow:
- ❌ Deleting or modifying existing policies (not created by AutoGuardRails)
- ❌ Attaching policies to production roles
- ❌ Creating/deleting roles
- ❌ Modifying trust relationships

---

### 4.4 Least Privilege Checklist

Before enabling Executor role, verify:

- [ ] **Scope is minimal**: Only target specific roles (e.g., `ci-*`, `dev-*`)
- [ ] **Production is excluded**: No `prod-*`, `admin`, or critical roles
- [ ] **Policies are namespaced**: All policies start with `guardrails-`
- [ ] **Audit logging is enabled**: DynamoDB table exists and writable
- [ ] **TTL cleanup is configured**: Lambda scheduled to remove expired policies
- [ ] **Allowlist is configured**: Critical accounts/roles are exempted

---

## 5. Testing Safety

### 5.1 Dry-Run Testing (Always Safe)

Before enabling `mode: auto` or `mode: manual`:

1. **Set mode to dry_run**:
   ```yaml
   mode: "dry_run"
   ```

2. **Trigger a test event**:
   ```bash
   # Simulate budget alert
   aws sns publish --topic-arn arn:aws:sns:... --message "$(cat test-event.json)"
   ```

3. **Verify Slack notification**:
   - Check notification received
   - Review recommended actions
   - Confirm no actual AWS API calls made

4. **Check audit logs** (should show `status: planned`, not `executed`)

---

### 5.2 Manual Mode Testing (Controlled Risk)

1. **Enable manual mode** for one policy:
   ```yaml
   mode: "manual"
   ttl_minutes: 30  # Short TTL for testing
   ```

2. **Trigger event** and **approve via Slack**

3. **Verify**:
   - Policy attached to target role
   - Audit log shows approval
   - TTL cleanup removes policy after 30 minutes

4. **Test manual rollback**:
   ```bash
   # Via CLI (to be implemented)
   autoguardrails rollback --execution-id <uuid>
   ```

---

### 5.3 Auto Mode Testing (Higher Risk, Use Sandbox)

**Prerequisites**:
- ✅ Dry-run testing passed
- ✅ Manual mode testing passed
- ✅ Sandbox AWS account (NOT production)
- ✅ Short TTL (e.g., 15 minutes)
- ✅ Allowlist configured for critical roles

**Test Plan**:
1. Create test IAM role (`test-ci-role`)
2. Configure policy to target this role
3. Trigger cost event
4. Verify:
   - Deny policy attached automatically
   - Test role cannot create restricted resources
   - TTL cleanup removes policy after 15 minutes
   - Audit log is complete

---

## 6. Incident Response

### 6.1 Guardrail Caused Disruption

**Symptoms**: Legitimate operations blocked by guardrail

**Immediate Actions**:
1. **Identify affected execution**:
   ```bash
   # Query DynamoDB
   aws dynamodb query --table-name autoguardrails-audit \
     --key-condition-expression "execution_id = :id"
   ```

2. **Manual rollback**:
   ```bash
   # Detach deny policy
   aws iam detach-role-policy \
     --role-name ci-deployer \
     --policy-arn arn:aws:iam::123456789012:policy/guardrails-deny-ec2
   ```

3. **Update audit log**:
   ```python
   # Mark as rolled back
   execution.status = "rolled_back"
   execution.rolled_back_at = datetime.utcnow()
   ```

4. **Add exception** to policy to prevent recurrence

---

### 6.2 TTL Cleanup Failed

**Symptoms**: Policies not automatically removed after TTL expires

**Diagnosis**:
```bash
# Check TTL cleanup Lambda logs
aws logs tail /aws/lambda/autoguardrails-ttl-cleanup --follow
```

**Mitigation**:
1. **Manual cleanup** (temporary):
   ```bash
   # List expired executions
   aws dynamodb query --table-name autoguardrails-audit \
     --filter-expression "ttl_expires_at < :now AND status = :status" \
     --expression-attribute-values '{":now":{"S":"2025-01-15T10:00:00Z"},":status":{"S":"executed"}}'

   # Detach policies manually
   ```

2. **Fix root cause** (Lambda permissions, code bug, etc.)

---

## 7. Security Considerations

### 7.1 Slack Webhook Protection

**Risk**: Webhook URL leaked → unauthorized alerts sent

**Mitigation**:
1. **Store in SSM Parameter Store** (encrypted):
   ```bash
   aws ssm put-parameter \
     --name /guardrails/slack_webhook \
     --value "https://hooks.slack.com/..." \
     --type SecureString
   ```

2. **Restrict Lambda permissions**:
   ```json
   {
     "Action": "ssm:GetParameter",
     "Resource": "arn:aws:ssm:*:*:parameter/guardrails/*"
   }
   ```

---

### 7.2 Approval URL Tampering

**Risk**: Attacker generates fake approval URLs

**Mitigation**:
1. **HMAC-SHA256 signature**:
   ```python
   import hmac
   import hashlib

   secret = os.getenv("APPROVAL_SECRET")  # Stored in SSM
   signature = hmac.new(
       secret.encode(),
       f"{execution_id}:{timestamp}".encode(),
       hashlib.sha256
   ).hexdigest()

   approval_url = f"https://api.autoguardrails.com/approve?id={execution_id}&sig={signature}&ts={timestamp}"
   ```

2. **Expiration check** (1 hour TTL):
   ```python
   if datetime.now() - timestamp > timedelta(hours=1):
       raise Expired("Approval link expired")
   ```

---

### 7.3 IAM Policy Tampering

**Risk**: Attacker modifies guardrail policies to be ineffective

**Mitigation**:
1. **Policy version tracking** in audit log
2. **Immutable policies** (create new, don't modify existing)
3. **CloudTrail monitoring** for `iam:DeletePolicy` on `guardrails-*` policies

---

## 8. Compliance & Audit

### 8.1 Audit Log Requirements

**Retention**: Minimum 90 days (configurable)

**Fields Required for Compliance**:
- Who executed (user ID or `system:auto`)
- What action (policy attach/detach)
- When (timestamp)
- Why (policy ID, triggering event)
- Impact (target role, permissions affected)
- Duration (TTL, actual rollback time)

---

### 8.2 Export for Compliance

```bash
# Export to CSV
aws dynamodb scan --table-name autoguardrails-audit \
  --output json | jq -r '.Items[] | [.execution_id, .executed_at, .action, .target] | @csv'

# Or via API (future)
curl -X GET https://api.autoguardrails.com/audit/export?format=csv \
  -H "Authorization: Bearer <token>"
```

---

## 9. Safety Checklist (Deployment)

Before deploying to production:

- [ ] **Dry-run mode tested** for all policies
- [ ] **Manual mode tested** in sandbox account
- [ ] **Production roles excluded** from scope
- [ ] **Allowlist configured** for critical systems
- [ ] **TTL configured** (recommended: 3 hours)
- [ ] **Audit logging enabled** and tested
- [ ] **Slack webhook secured** (SSM Parameter Store)
- [ ] **Approval signature** implemented
- [ ] **Rollback procedure documented** and tested
- [ ] **Team trained** on incident response

---

## 10. Best Practices

### 10.1 Start Small
- Begin with `mode: dry_run`
- Target only 1-2 non-critical roles
- Use short TTLs (30 minutes) during testing

### 10.2 Monitor Closely
- Set up Slack channel dedicated to AutoGuardRails
- Review audit logs weekly
- Track false positive rate

### 10.3 Iterate Gradually
- Dry-run (1 week) → Manual (1 week) → Auto (after confidence)
- Expand scope only after 100% success rate
- Document all exceptions and their rationale

---

**Last Updated:** 2025-01-15
**Version:** 1.0
**Status:** MVP Implementation Guide
