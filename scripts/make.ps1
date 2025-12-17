# PowerShell版Makefile
# Usage: .\scripts\make.ps1 <command>

param(
    [Parameter(Position=0)]
    [string]$Command = "help",

    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$RemainingArgs
)

# Set-StrictMode -Version Latest を一時的に無効化（$Args.Countの問題を回避）
$ErrorActionPreference = "Stop"

function Write-ColorOutput {
    param(
        [string]$Message,
        [string]$Color = "White"
    )
    Write-Host $Message -ForegroundColor $Color
}

switch ($Command) {
    "setup" {
        Write-ColorOutput "📦 Installing dependencies..." "Cyan"
        python -m pip install --upgrade pip
        if (Test-Path "pyproject.toml") {
            pip install -e ".[dev]"
        } else {
            Write-ColorOutput "⚠️  pyproject.toml not found. Installing core dependencies..." "Yellow"
            pip install pydantic boto3 pytest ruff requests PyYAML python-dotenv moto
        }
        Write-ColorOutput "✅ Setup complete" "Green"
    }

    "fmt" {
        Write-ColorOutput "🎨 Formatting code with ruff..." "Cyan"
        if (Test-Path "src") {
            ruff format src/ tests/
        } else {
            Write-ColorOutput "⚠️  src/ directory not found" "Yellow"
        }
        Write-ColorOutput "✅ Formatting complete" "Green"
    }

    "lint" {
        Write-ColorOutput "🔍 Linting code with ruff..." "Cyan"
        if (Test-Path "src") {
            ruff check src/ tests/ --fix
        } else {
            Write-ColorOutput "⚠️  src/ directory not found" "Yellow"
        }
        Write-ColorOutput "✅ Linting complete" "Green"
    }

    "test" {
        Write-ColorOutput "🧪 Running all tests..." "Cyan"
        if ($RemainingArgs -and $RemainingArgs.Count -gt 0) {
            pytest @RemainingArgs -v
        } elseif (Test-Path "tests") {
            pytest tests/ -v
        } else {
            Write-ColorOutput "⚠️  tests/ directory not found" "Yellow"
            exit 1
        }
        if ($LASTEXITCODE -eq 0) {
            Write-ColorOutput "✅ All tests passed" "Green"
        } else {
            Write-ColorOutput "❌ Tests failed" "Red"
            exit $LASTEXITCODE
        }
    }

    "test-unit" {
        Write-ColorOutput "🧪 Running unit tests..." "Cyan"
        pytest tests/unit/ -v
        if ($LASTEXITCODE -eq 0) {
            Write-ColorOutput "✅ Unit tests passed" "Green"
        } else {
            Write-ColorOutput "❌ Unit tests failed" "Red"
            exit $LASTEXITCODE
        }
    }

    "test-integration" {
        Write-ColorOutput "🧪 Running integration tests..." "Cyan"
        pytest tests/integration/ -v
        if ($LASTEXITCODE -eq 0) {
            Write-ColorOutput "✅ Integration tests passed" "Green"
        } else {
            Write-ColorOutput "❌ Integration tests failed" "Red"
            exit $LASTEXITCODE
        }
    }

    "test-cov" {
        Write-ColorOutput "🧪 Running tests with coverage..." "Cyan"
        pytest tests/ --cov=src --cov-report=html --cov-report=term -v
        Write-ColorOutput "📊 Coverage report: htmlcov/index.html" "Cyan"
    }

    "clean" {
        Write-ColorOutput "🧹 Cleaning cache files..." "Cyan"

        # Python cache
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue .pytest_cache, .ruff_cache, .mypy_cache, htmlcov

        # __pycache__
        Get-ChildItem -Recurse -Filter __pycache__ | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

        # Egg info
        Get-ChildItem -Recurse -Filter *.egg-info | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

        # Pyc files
        Get-ChildItem -Recurse -Filter *.pyc | Remove-Item -Force -ErrorAction SilentlyContinue

        Write-ColorOutput "✅ Clean complete" "Green"
    }

    "run-local" {
        Write-ColorOutput "🚀 Running local test handler..." "Cyan"
        if (Test-Path "src/guardrails/handlers/cost_alert_handler.py") {
            python -m src.guardrails.handlers.cost_alert_handler
        } else {
            Write-ColorOutput "⚠️  cost_alert_handler.py not found" "Yellow"
        }
    }

    "synth" {
        Write-ColorOutput "🏗️  Synthesizing CloudFormation..." "Cyan"
        if (Test-Path "infra/cdk") {
            Push-Location infra/cdk
            try {
                cdk synth
                Write-ColorOutput "✅ Synth complete" "Green"
            } finally {
                Pop-Location
            }
        } else {
            Write-ColorOutput "⚠️  infra/cdk not found" "Yellow"
        }
    }

    "deploy-dry-run" {
        Write-ColorOutput "🔍 CDK deploy (dry-run)..." "Cyan"
        if (Test-Path "infra/cdk") {
            Push-Location infra/cdk
            try {
                cdk deploy --dry-run
                Write-ColorOutput "✅ Dry-run complete" "Green"
            } finally {
                Pop-Location
            }
        } else {
            Write-ColorOutput "⚠️  infra/cdk not found" "Yellow"
        }
    }

    "deploy" {
        Write-ColorOutput "🚀 Deploying to AWS..." "Yellow"
        Write-ColorOutput "⚠️  WARNING: This will deploy to your AWS account!" "Red"
        $confirm = Read-Host "Continue? (yes/no)"
        if ($confirm -eq "yes") {
            if (Test-Path "infra/cdk") {
                Push-Location infra/cdk
                try {
                    cdk deploy
                    Write-ColorOutput "✅ Deploy complete" "Green"
                } finally {
                    Pop-Location
                }
            } else {
                Write-ColorOutput "⚠️  infra/cdk not found" "Yellow"
            }
        } else {
            Write-ColorOutput "❌ Deploy cancelled" "Yellow"
        }
    }

    "check" {
        Write-ColorOutput "🔍 Running pre-commit checks..." "Cyan"
        & $PSScriptRoot\make.ps1 fmt
        & $PSScriptRoot\make.ps1 lint
        & $PSScriptRoot\make.ps1 test
        Write-ColorOutput "✅ All checks passed" "Green"
    }

    "docs" {
        Write-ColorOutput "📚 Generating documentation..." "Cyan"
        if (Test-Path "docs") {
            Write-ColorOutput "Available documentation:" "White"
            Get-ChildItem docs/*.md | ForEach-Object {
                Write-ColorOutput "  - $($_.Name)" "Cyan"
            }
        } else {
            Write-ColorOutput "⚠️  docs/ directory not found" "Yellow"
        }
    }

    "venv" {
        Write-ColorOutput "🐍 Creating virtual environment..." "Cyan"
        if (Test-Path ".venv") {
            Write-ColorOutput "⚠️  .venv already exists" "Yellow"
        } else {
            python -m venv .venv
            Write-ColorOutput "✅ Virtual environment created" "Green"
            Write-ColorOutput "💡 Activate with: .\.venv\Scripts\Activate.ps1" "Cyan"
        }
    }

    default {
        Write-ColorOutput @"

📚 AutoGuardRails - Available Commands

Setup & Environment:
  .\scripts\make.ps1 setup           - Install dependencies
  .\scripts\make.ps1 venv            - Create virtual environment

Code Quality:
  .\scripts\make.ps1 fmt             - Format code with ruff
  .\scripts\make.ps1 lint            - Lint code with ruff
  .\scripts\make.ps1 check           - Run all pre-commit checks

Testing:
  .\scripts\make.ps1 test            - Run all tests
  .\scripts\make.ps1 test-unit       - Run unit tests only
  .\scripts\make.ps1 test-integration - Run integration tests only
  .\scripts\make.ps1 test-cov        - Run tests with coverage report

Local Development:
  .\scripts\make.ps1 run-local       - Run local test handler
  .\scripts\make.ps1 clean           - Clean cache files

Infrastructure:
  .\scripts\make.ps1 synth           - Synthesize CloudFormation
  .\scripts\make.ps1 deploy-dry-run  - Deploy dry-run (safe)
  .\scripts\make.ps1 deploy          - Deploy to AWS (careful!)

Documentation:
  .\scripts\make.ps1 docs            - List available documentation

Usage: .\scripts\make.ps1 <command> [args]

Examples:
  .\scripts\make.ps1 test tests/unit/test_models.py
  .\scripts\make.ps1 test-cov

"@ "White"
    }
}
