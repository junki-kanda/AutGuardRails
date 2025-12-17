# Windows環境でのセットアップガイド

## 0. 前提条件

### 必須ツール
- **Python 3.11+** (Microsoft Store or python.org)
- **Git for Windows** (git-scm.com)
- **PowerShell 7+** (推奨) or Windows PowerShell 5.1
- **VS Code** (推奨) or 任意のエディタ

### オプション（推奨）
- **Windows Terminal** (Microsoft Store)
- **WSL2** (Ubuntu推奨) - Linuxコマンド実行用

---

## 1. 環境構築（クイックスタート）

### 1.1 Pythonのインストール確認
```powershell
python --version  # Python 3.11以上
pip --version
```

### 1.2 リポジトリのクローン
```powershell
cd C:\Users\<YourName>\Documents
git clone <repository-url> AutoGuardRails
cd AutoGuardRails
```

### 1.3 仮想環境の作成
```powershell
# 仮想環境作成
python -m venv .venv

# 仮想環境の有効化（PowerShell）
.\.venv\Scripts\Activate.ps1

# 仮想環境の有効化（CMD）
.venv\Scripts\activate.bat
```

**注意**: PowerShellでスクリプト実行が無効な場合:
```powershell
# 管理者権限で実行
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### 1.4 依存関係のインストール
```powershell
# Phase 0完了後に実行
pip install -e ".[dev]"

# または直接指定
pip install pydantic boto3 pytest ruff requests PyYAML
```

---

## 2. Windows固有のMakefile代替

Windowsには標準でmakeコマンドが無いため、以下の選択肢があります。

### オプション1: PowerShellスクリプト（推奨）

**scripts/make.ps1**:
```powershell
# PowerShell版Makefile
param(
    [Parameter(Position=0)]
    [string]$Command = "help"
)

switch ($Command) {
    "setup" {
        Write-Host "📦 Installing dependencies..."
        python -m pip install --upgrade pip
        pip install -e ".[dev]"
    }
    "fmt" {
        Write-Host "🎨 Formatting code..."
        ruff format src/ tests/
    }
    "lint" {
        Write-Host "🔍 Linting code..."
        ruff check src/ tests/
    }
    "test" {
        Write-Host "🧪 Running tests..."
        pytest tests/ -v
    }
    "test-unit" {
        Write-Host "🧪 Running unit tests..."
        pytest tests/unit/ -v
    }
    "test-integration" {
        Write-Host "🧪 Running integration tests..."
        pytest tests/integration/ -v
    }
    "clean" {
        Write-Host "🧹 Cleaning cache..."
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue .pytest_cache, __pycache__, .ruff_cache, *.egg-info
        Get-ChildItem -Recurse -Filter __pycache__ | Remove-Item -Recurse -Force
    }
    "run-local" {
        Write-Host "🚀 Running local test..."
        python -m src.guardrails.handlers.cost_alert_handler
    }
    default {
        Write-Host @"
📚 AutoGuardRails - Available commands:

  .\scripts\make.ps1 setup         - Install dependencies
  .\scripts\make.ps1 fmt           - Format code with ruff
  .\scripts\make.ps1 lint          - Lint code with ruff
  .\scripts\make.ps1 test          - Run all tests
  .\scripts\make.ps1 test-unit     - Run unit tests only
  .\scripts\make.ps1 test-integration - Run integration tests only
  .\scripts\make.ps1 clean         - Clean cache files
  .\scripts\make.ps1 run-local     - Run local test

Usage: .\scripts\make.ps1 <command>
"@
    }
}
```

**使用例**:
```powershell
.\scripts\make.ps1 setup
.\scripts\make.ps1 lint
.\scripts\make.ps1 test
```

---

### オプション2: バッチファイル

**make.bat**:
```batch
@echo off
setlocal

if "%1"=="setup" goto setup
if "%1"=="fmt" goto fmt
if "%1"=="lint" goto lint
if "%1"=="test" goto test
if "%1"=="" goto help
goto help

:setup
echo Installing dependencies...
python -m pip install --upgrade pip
pip install -e ".[dev]"
goto end

:fmt
echo Formatting code...
ruff format src/ tests/
goto end

:lint
echo Linting code...
ruff check src/ tests/
goto end

:test
echo Running tests...
pytest tests/ -v
goto end

:help
echo Available commands:
echo   make setup  - Install dependencies
echo   make fmt    - Format code
echo   make lint   - Lint code
echo   make test   - Run tests
goto end

:end
```

**使用例**:
```cmd
make.bat setup
make.bat test
```

---

### オプション3: WSL2でLinux makeを使用

WSL2（Ubuntu）をインストール済みの場合:
```bash
# WSL2内で実行
cd /mnt/c/Users/<YourName>/Documents/AutoGuardRails
make setup
make test
```

---

## 3. 並行エージェント実行（Windows版）

### 3.1 PowerShellでの並行実行

**scripts/run-phase0.ps1**:
```powershell
# Phase 0: Foundation
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "🚀 Starting Phase 0: Foundation" -ForegroundColor Cyan

# Step 1: A0-1, A0-2並行起動
Write-Host "`nStep 1: Starting A0-1, A0-2..." -ForegroundColor Yellow

$job1 = Start-Job -ScriptBlock {
    Set-Location $using:PWD
    claude --agent-id="A0-1" --task-file="docs/parallel-implementation-plan.md#agent-a0-1"
}

$job2 = Start-Job -ScriptBlock {
    Set-Location $using:PWD
    claude --agent-id="A0-2" --task-file="docs/parallel-implementation-plan.md#agent-a0-2"
}

# ジョブ完了待機
$jobs = @($job1, $job2)
$jobs | Wait-Job | Receive-Job

Write-Host "✅ A0-1, A0-2 completed" -ForegroundColor Green

# Step 2: 検証
Write-Host "`nStep 2: Validating..." -ForegroundColor Yellow
.\scripts\make.ps1 setup

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Setup failed" -ForegroundColor Red
    exit 1
}

# Step 3: A0-3実行（最優先）
Write-Host "`nStep 3: Starting A0-3 (CRITICAL)..." -ForegroundColor Yellow
claude --agent-id="A0-3" --task-file="docs/parallel-implementation-plan.md#agent-a0-3"

# Step 4: 検証
Write-Host "`nStep 4: Validating models.py..." -ForegroundColor Yellow
.\scripts\make.ps1 test-unit

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Tests failed" -ForegroundColor Red
    exit 1
}

Write-Host "`n✅ Phase 0 completed" -ForegroundColor Green
```

**実行**:
```powershell
.\scripts\run-phase0.ps1
```

---

**scripts/run-phase1.ps1**:
```powershell
# Phase 1: Free Tier
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# 事前確認
if (-not (Test-Path "src/guardrails/models.py")) {
    Write-Host "❌ models.py not found. Run Phase 0 first." -ForegroundColor Red
    exit 1
}

Write-Host "🚀 Starting Phase 1: Free Tier" -ForegroundColor Cyan

# Step 1: A1-1,2,3並行起動
Write-Host "`nStep 1: Starting A1-1, A1-2, A1-3..." -ForegroundColor Yellow

$jobs = @()
$jobs += Start-Job -ScriptBlock {
    Set-Location $using:PWD
    claude --agent-id="A1-1" --task-file="docs/parallel-implementation-plan.md#agent-a1-1"
}
$jobs += Start-Job -ScriptBlock {
    Set-Location $using:PWD
    claude --agent-id="A1-2" --task-file="docs/parallel-implementation-plan.md#agent-a1-2"
}
$jobs += Start-Job -ScriptBlock {
    Set-Location $using:PWD
    claude --agent-id="A1-3" --task-file="docs/parallel-implementation-plan.md#agent-a1-3"
}

# 完了待機
$jobs | Wait-Job | Receive-Job

Write-Host "✅ A1-1, A1-2, A1-3 completed" -ForegroundColor Green

# Step 2: ユニットテスト
Write-Host "`nStep 2: Running unit tests..." -ForegroundColor Yellow
.\scripts\make.ps1 test-unit

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Unit tests failed" -ForegroundColor Red
    exit 1
}

# Step 3: 統合
Write-Host "`nStep 3: Starting A1-4 (Integration)..." -ForegroundColor Yellow
claude --agent-id="A1-4" --task-file="docs/parallel-implementation-plan.md#agent-a1-4"

# Step 4: E2Eテスト
Write-Host "`nStep 4: Running E2E tests..." -ForegroundColor Yellow
pytest tests/integration/test_e2e_phase1.py -v

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ E2E tests failed" -ForegroundColor Red
    exit 1
}

Write-Host "`n✅ Phase 1 completed - MVP Free Tier ready!" -ForegroundColor Green
```

---

### 3.2 完全自動化スクリプト

**scripts/run-all-phases.ps1**:
```powershell
# 全Phase自動実行
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "🚀 Starting AutoGuardRails Full Implementation" -ForegroundColor Cyan

# Phase 0
Write-Host "`n=== Phase 0: Foundation ===" -ForegroundColor Magenta
.\scripts\run-phase0.ps1
if ($LASTEXITCODE -ne 0) { exit 1 }

# Phase 1
Write-Host "`n=== Phase 1: Free Tier ===" -ForegroundColor Magenta
.\scripts\run-phase1.ps1
if ($LASTEXITCODE -ne 0) { exit 1 }

# Phase 2
Write-Host "`n=== Phase 2: Manual Approval ===" -ForegroundColor Magenta
.\scripts\run-phase2.ps1
if ($LASTEXITCODE -ne 0) { exit 1 }

# Phase 3
Write-Host "`n=== Phase 3: Auto Mode ===" -ForegroundColor Magenta
.\scripts\run-phase3.ps1
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "`n✅ All phases completed!" -ForegroundColor Green
Write-Host "`n📊 Running full test suite..." -ForegroundColor Yellow
.\scripts\make.ps1 test

Write-Host "`n🎉 AutoGuardRails MVP完成！" -ForegroundColor Green
Write-Host "次のステップ: cdk deploy --dry-run (or terraform plan)" -ForegroundColor Cyan
```

**実行**:
```powershell
.\scripts\run-all-phases.ps1
```

---

## 4. パス関連の注意事項

### 4.1 Windows固有のパス表記

PowerShell/CMD では以下のパス表記を使用:

```powershell
# バックスラッシュ（Windowsネイティブ）
C:\Users\jkwrr\Documents\AutoGuardRails\src\guardrails\models.py

# スラッシュ（Python/Git互換、推奨）
C:/Users/jkwrr/Documents/AutoGuardRails/src/guardrails/models.py

# 相対パス（推奨）
.\src\guardrails\models.py
./src/guardrails/models.py  # PowerShellでも動作
```

### 4.2 Python内でのパス処理

```python
from pathlib import Path

# Windows/Linux互換（推奨）
project_root = Path(__file__).parent.parent
models_path = project_root / "src" / "guardrails" / "models.py"

# 文字列結合は避ける（NG）
# path = "src\\" + "guardrails\\" + "models.py"  # 非推奨
```

---

## 5. 環境変数の設定

### 5.1 PowerShellでの設定

```powershell
# 一時的な設定（現在のセッションのみ）
$env:SLACK_WEBHOOK_URL = "https://hooks.slack.com/services/..."
$env:AWS_REGION = "ap-northeast-1"

# 永続的な設定（ユーザー環境変数）
[System.Environment]::SetEnvironmentVariable("SLACK_WEBHOOK_URL", "https://...", "User")
```

### 5.2 .envファイルの使用（推奨）

**.env** (gitignore必須):
```env
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
AWS_REGION=ap-northeast-1
POLICY_DIR=./policies
DYNAMODB_TABLE_NAME=autoguardrails-audit
```

**Python側で読み込み**:
```python
from dotenv import load_dotenv
import os

load_dotenv()  # .envを読み込み
slack_webhook = os.getenv("SLACK_WEBHOOK_URL")
```

**依存追加**:
```powershell
pip install python-dotenv
```

---

## 6. トラブルシューティング（Windows版）

### 6.1 PowerShellスクリプトが実行できない

**エラー**: `実行ポリシーにより、このスクリプトの実行が禁止されています`

**解決策**:
```powershell
# 現在のユーザーのみ許可
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# 一時的にバイパス（非推奨）
powershell -ExecutionPolicy Bypass -File .\scripts\run-phase0.ps1
```

### 6.2 パスが長すぎる（260文字制限）

**エラー**: `指定されたパス、ファイル名、またはその両方が長すぎます`

**解決策1**: Gitで長いパスを有効化
```powershell
git config --global core.longpaths true
```

**解決策2**: プロジェクトを浅い階層に配置
```powershell
# NG: C:\Users\jkwrr\Documents\Projects\AWS\CostManagement\AutoGuardRails\...
# OK: C:\Projects\AutoGuardRails\...
```

### 6.3 CRLF vs LF（改行コード問題）

**症状**: Gitで大量のファイルが変更扱いになる

**解決策**:
```powershell
# .gitattributesを作成
@"
* text=auto
*.py text eol=lf
*.sh text eol=lf
*.ps1 text eol=crlf
*.bat text eol=crlf
"@ | Out-File -FilePath .gitattributes -Encoding utf8

# 既存ファイルの正規化
git add --renormalize .
git commit -m "Normalize line endings"
```

### 6.4 Python仮想環境が認識されない

**症状**: VSCodeでlinterが動かない

**解決策**:
1. VSCodeで `Ctrl+Shift+P` → `Python: Select Interpreter`
2. `.venv/Scripts/python.exe` を選択
3. VSCodeを再起動

---

## 7. Windows Terminalの推奨設定

**settings.json** (Windows Terminal):
```json
{
  "profiles": {
    "defaults": {
      "fontFace": "Cascadia Code",
      "fontSize": 11,
      "colorScheme": "One Half Dark"
    },
    "list": [
      {
        "name": "PowerShell 7",
        "commandline": "pwsh.exe -NoLogo",
        "startingDirectory": "C:/Users/jkwrr/Documents/AutoGuardRails"
      },
      {
        "name": "Git Bash",
        "commandline": "C:/Program Files/Git/bin/bash.exe",
        "startingDirectory": "C:/Users/jkwrr/Documents/AutoGuardRails"
      }
    ]
  }
}
```

---

## 8. クイックスタート（全体の流れ）

```powershell
# 1. リポジトリ移動
cd C:\Users\jkwrr\Documents\AutoGuardRails

# 2. 仮想環境作成・有効化
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Phase 0実行（基盤構築）
.\scripts\run-phase0.ps1

# 4. Phase 1実行（Free Tier）
.\scripts\run-phase1.ps1

# 5. 全Phase自動実行（または個別実行）
.\scripts\run-all-phases.ps1

# 6. テスト実行
.\scripts\make.ps1 test

# 7. AWS デプロイ（後で）
cd infra/cdk
cdk synth
cdk deploy --dry-run
```

---

## 9. VS Code推奨設定

**.vscode/settings.json**:
```json
{
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/Scripts/python.exe",
  "python.formatting.provider": "none",
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
      "source.organizeImports": true
    }
  },
  "files.eol": "\n",
  "files.exclude": {
    "**/__pycache__": true,
    "**/.pytest_cache": true,
    "**/.ruff_cache": true
  },
  "terminal.integrated.defaultProfile.windows": "PowerShell"
}
```

**.vscode/extensions.json**:
```json
{
  "recommendations": [
    "ms-python.python",
    "ms-python.vscode-pylance",
    "charliermarsh.ruff",
    "ms-vscode.powershell"
  ]
}
```

---

## 10. まとめ（Windows版クイックリファレンス）

### コマンド対応表

| Linux/Mac | Windows PowerShell | Windows CMD |
|-----------|-------------------|-------------|
| `make setup` | `.\scripts\make.ps1 setup` | `make.bat setup` |
| `make test` | `.\scripts\make.ps1 test` | `make.bat test` |
| `./script.sh` | `.\script.ps1` | `script.bat` |
| `export VAR=value` | `$env:VAR="value"` | `set VAR=value` |
| `ls -la` | `Get-ChildItem` or `ls` | `dir` |
| `cat file.txt` | `Get-Content file.txt` | `type file.txt` |

### 推奨環境
✅ **最良**: PowerShell 7 + Windows Terminal + VS Code
✅ **良い**: PowerShell 5.1 + VS Code
✅ **代替**: WSL2 (Ubuntu) + Linux環境そのまま使用
⚠️ **非推奨**: CMD（機能制限あり）

### 次のステップ
1. `.\scripts\make.ps1 setup` でセットアップ
2. `.\scripts\run-phase0.ps1` で基盤構築
3. 各Phaseを順次実行または `.\scripts\run-all-phases.ps1` で自動化
