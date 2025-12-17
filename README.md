# AutoGuardRails

**AWS Cost Guardrails - Automatic cost anomaly detection and prevention**

AutoGuardRails is a safety-first AWS cost management system that automatically detects cost anomalies and applies graduated guardrails (dry-run → manual approval → automatic) to prevent runaway costs.

## 🎯 Mission

Detect AWS cost anomalies early and apply **safe, graduated, and recoverable** guardrails (restrictions/isolation/approval flows) automatically.

## ✨ Features

### Free Tier (Detection & Notification)
- ✅ AWS Budgets event integration
- ✅ Cost Anomaly Detection support
- ✅ Slack notifications with rich context
- ✅ Dry-run mode (safe by default)

### Pro Tier (Automated Guardrails)
- 🔒 IAM deny policy attachment (safe & reversible)
- 👤 Quarantine mode for specific IAM roles/users
- ⏰ TTL-based automatic rollback
- 📝 Full audit trail (DynamoDB)
- ✅ Manual approval workflow

### Coming Soon
- 🚫 Service Control Policies (SCP) support
- 🔍 Root cause analysis
- 🎨 Web UI for management
- 📊 Cost analytics dashboard

## 🚀 Quick Start

### Prerequisites

- **Python 3.11+**
- **AWS Account** with appropriate permissions
- **Slack Webhook** (for notifications)

### Installation

#### Windows (PowerShell)

```powershell
# Clone repository
git clone <repository-url>
cd AutoGuardRails

# Create virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install dependencies
.\scripts\make.ps1 setup
```

#### Linux/Mac

```bash
# Clone repository
git clone <repository-url>
cd AutoGuardRails

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
make setup
```

### Configuration

Create a `.env` file in the project root:

```env
# Slack webhook URL (from Slack App settings)
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL

# AWS region
AWS_REGION=ap-northeast-1

# Policy directory
POLICY_DIR=./policies

# DynamoDB table name (for audit logs)
DYNAMODB_TABLE_NAME=autoguardrails-audit
```

### Running Tests

```powershell
# Windows
.\scripts\make.ps1 test

# Linux/Mac
make test
```

## 📖 Documentation

- **[CLAUDE.md](CLAUDE.md)** - Main implementation guide
- **[docs/practical-workflow.md](docs/practical-workflow.md)** - Step-by-step implementation workflow
- **[docs/windows-setup.md](docs/windows-setup.md)** - Windows-specific setup guide
- **[docs/parallel-implementation-plan.md](docs/parallel-implementation-plan.md)** - Detailed technical design
- **[docs/safety.md](docs/safety.md)** - Safety principles and IAM permissions
- **[docs/policies.md](docs/policies.md)** - Policy YAML specification

## 🏗️ Architecture

```
Event Sources (AWS Budgets/Anomaly Detection)
    ↓
SNS/EventBridge
    ↓
Lambda Handler
    ↓
Policy Engine → Matches cost event against policies
    ↓
┌─────────────┬──────────────┬──────────────┐
│  Dry-run    │   Manual     │   Auto       │
│  (Notify)   │   (Approve)  │   (Execute)  │
└─────────────┴──────────────┴──────────────┘
    ↓
Slack Notifier + IAM Executor + Audit Store
```

## 🔒 Safety Principles

1. **Default is Dry-run** - No actions without explicit opt-in
2. **Scope Minimization** - Target specific roles/users/tags, not entire accounts
3. **Easy Rollback** - TTL auto-release + manual release API
4. **Allowlist Support** - Exceptions for critical accounts/time windows
5. **Two-stage Approval** - Notification → Approval → Execution
6. **Mandatory Audit Logs** - Who, why, what, when, how long

## 📋 Development Commands

### Windows (PowerShell)

```powershell
.\scripts\make.ps1 setup          # Install dependencies
.\scripts\make.ps1 fmt            # Format code
.\scripts\make.ps1 lint           # Lint code
.\scripts\make.ps1 test           # Run tests
.\scripts\make.ps1 test-unit      # Unit tests only
.\scripts\make.ps1 test-cov       # Tests with coverage
.\scripts\make.ps1 clean          # Clean cache files
```

### Linux/Mac

```bash
make setup          # Install dependencies
make fmt            # Format code
make lint           # Lint code
make test           # Run tests
make clean          # Clean cache files
```

## 🗂️ Project Structure

```
AutoGuardRails/
├── src/
│   └── guardrails/
│       ├── models.py              # Pydantic models (CostEvent, Policy, etc.)
│       ├── policy_engine.py       # Policy evaluation logic
│       ├── notifier_slack.py      # Slack notification
│       ├── executor_iam.py        # IAM guardrail execution
│       ├── audit_store.py         # DynamoDB audit logging
│       └── handlers/
│           ├── budgets_event.py   # AWS Budgets event handler
│           ├── anomaly_event.py   # Cost Anomaly event handler
│           └── approval_webhook.py # Manual approval webhook
├── tests/
│   ├── unit/                      # Unit tests
│   └── integration/               # Integration tests
├── policies/                      # YAML policy definitions
├── infra/
│   ├── cdk/                       # AWS CDK (optional)
│   └── terraform/                 # Terraform (optional)
├── docs/                          # Documentation
└── scripts/                       # Utility scripts
```

## 📝 Example Policy

```yaml
# policies/example-dry-run.yaml
policy_id: "budget-spike-ci-quarantine"
mode: "manual"          # dry_run | manual | auto
ttl_minutes: 180        # Auto-release after 3 hours

match:
  source: ["budgets", "anomaly"]
  account_ids: ["123456789012"]
  min_amount_usd: 200

scope:
  principals:
    - type: "iam_role"
      arn: "arn:aws:iam::123456789012:role/ci-deployer"
  regions: ["ap-northeast-1"]

actions:
  - type: "attach_deny_policy"
    deny:
      - "ec2:RunInstances"
      - "ec2:CreateNatGateway"
      - "ec2:CreateVpc"

notify:
  slack_webhook_ssm_param: "/guardrails/slack_webhook"
  channel_hint: "#cost-alerts"
```

## 🧪 Testing

```powershell
# Run all tests
.\scripts\make.ps1 test

# Run specific test
.\scripts\make.ps1 test tests/unit/test_models.py

# Run with coverage
.\scripts\make.ps1 test-cov

# View coverage report
start htmlcov/index.html  # Windows
```

## 🚢 Deployment

### AWS CDK

```bash
cd infra/cdk
cdk synth
cdk deploy --dry-run
cdk deploy
```

### Terraform

```bash
cd infra/terraform
terraform init
terraform plan
terraform apply
```

## 🔐 Required IAM Permissions

### Read-only (Detection)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ce:GetAnomalies",
        "ce:GetCostForecast",
        "budgets:ViewBudget",
        "sns:Publish"
      ],
      "Resource": "*"
    }
  ]
}
```

### Write (Execution - Opt-in Required)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy",
        "iam:CreatePolicy",
        "iam:DeletePolicy"
      ],
      "Resource": [
        "arn:aws:iam::*:role/ci-*",
        "arn:aws:iam::*:policy/guardrails-*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:PutItem",
        "dynamodb:GetItem",
        "dynamodb:Query"
      ],
      "Resource": "arn:aws:dynamodb:*:*:table/autoguardrails-*"
    }
  ]
}
```

## 🤝 Contributing

This project is under active development. See [docs/practical-workflow.md](docs/practical-workflow.md) for the implementation roadmap.

## 📄 License

MIT License - See [LICENSE](LICENSE) file for details.

## 🆘 Support

- **Documentation**: See `docs/` directory
- **Issues**: GitHub Issues
- **Discussions**: GitHub Discussions

---

**⚠️ Important**: This tool can modify IAM permissions. Always test in a non-production environment first and review all policies before enabling automatic mode.
