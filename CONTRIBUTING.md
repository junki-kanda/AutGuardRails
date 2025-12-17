# Contributing to AutoGuardRails

Thank you for your interest in contributing to AutoGuardRails! This document provides guidelines for contributing to the project.

## 🎯 Project Philosophy

AutoGuardRails follows a **safety-first** approach:

1. **Default to Dry-run** - No destructive actions without explicit opt-in
2. **Minimize Scope** - Target specific resources, not entire accounts
3. **Easy Rollback** - All actions must be reversible
4. **Audit Everything** - Log all decisions and actions

## 📋 Development Setup

### Prerequisites

- Python 3.11+
- Git
- AWS CLI (configured)
- Slack webhook (for testing notifications)

### Setup

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/AutoGuardRails.git
cd AutoGuardRails

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install development dependencies
pip install -r requirements-dev.txt

# Run tests
python -m pytest tests/ -v
```

## 🧪 Testing Requirements

All contributions must include tests:

- **Unit tests** for new functions/classes
- **Integration tests** for new workflows
- Minimum **80% code coverage**

```bash
# Run tests with coverage
python -m pytest tests/ --cov=src --cov-report=term-missing

# Run specific test
python -m pytest tests/unit/test_models.py -v
```

## 📝 Code Style

We use:

- **ruff** for linting and formatting
- **Type hints** (Python 3.11+)
- **Pydantic** for data validation

```bash
# Format code
ruff format .

# Lint code
ruff check .

# Fix auto-fixable issues
ruff check . --fix
```

## 🔀 Pull Request Process

1. **Fork** the repository
2. **Create a branch** for your feature (`git checkout -b feature/amazing-feature`)
3. **Write tests** for your changes
4. **Ensure tests pass** (`pytest`)
5. **Format code** (`ruff format`)
6. **Commit** with clear message
7. **Push** to your fork
8. **Open a Pull Request**

### Commit Message Format

```
<type>: <subject>

<body>

<footer>
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation
- `test`: Tests
- `refactor`: Code refactoring
- `chore`: Maintenance

Example:
```
feat: add TTL-based automatic rollback

Implement scheduled Lambda function to automatically rollback
expired guardrails based on TTL expiration time.

Closes #42
```

## 🚫 What NOT to Commit

- Secrets (AWS keys, Slack webhooks, etc.)
- `.env` files
- Personal configurations
- Large binary files
- Temporary test data

Always check `.gitignore` before committing.

## 🔐 Security

- Never commit credentials or secrets
- Use AWS Systems Manager Parameter Store for production secrets
- Report security vulnerabilities privately (see SECURITY.md)
- Follow principle of least privilege for IAM permissions

## 📚 Documentation

- Update README.md for user-facing changes
- Update docs/ for technical documentation
- Add docstrings to all public functions/classes
- Include examples for new features

## 🐛 Bug Reports

Include:
- Clear title and description
- Steps to reproduce
- Expected vs actual behavior
- Environment details (OS, Python version, AWS region)
- Relevant logs/screenshots

## 💡 Feature Requests

Include:
- Use case description
- Proposed solution
- Alternatives considered
- Impact on existing functionality

## 📞 Questions?

- Check [docs/](docs/) directory
- Review [CLAUDE.md](CLAUDE.md) for implementation guide
- Open a GitHub Discussion
- Tag issues with `question` label

## 🎉 Recognition

Contributors will be recognized in:
- README.md contributors section
- Release notes
- Project documentation

Thank you for contributing to AutoGuardRails! 🚀
