#!/usr/bin/env python3
"""
Unit tests for the safety hook.

Run with: python3 -m pytest tests/test_safety.py -v
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Add hooks directory to path
HOOKS_DIR = Path(__file__).parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from safety import check_command, BLOCKED_PATTERNS, ESCALATE_PATTERNS


class TestBlockedPatterns:
    """Test that dangerous commands are blocked."""

    # Git force push variations
    @pytest.mark.parametrize("command", [
        "git push --force origin main",
        "git push -f origin main",
        "git push --force-with-lease origin main",
        "git push origin main --force",
    ])
    def test_blocks_git_force_push(self, command):
        result = check_command(command, BLOCKED_PATTERNS)
        assert result is not None, f"Should block: {command}"
        assert "force" in result[0].lower() or "git" in result[0].lower()

    # Git destructive commands
    @pytest.mark.parametrize("command", [
        "git reset --hard",
        "git reset --hard HEAD~5",
        "git clean -fd",
        "git clean -fdx",
        "git branch -D main",
        "git branch -d master",
    ])
    def test_blocks_git_destructive(self, command):
        result = check_command(command, BLOCKED_PATTERNS)
        assert result is not None, f"Should block: {command}"

    # Filesystem dangerous operations
    @pytest.mark.parametrize("command", [
        "rm -rf /",
        "rm -rf ~",
        "rm -rf *",
        "chmod 777 /etc",
    ])
    def test_blocks_filesystem_dangerous(self, command):
        result = check_command(command, BLOCKED_PATTERNS)
        assert result is not None, f"Should block: {command}"

    # Database destructive
    @pytest.mark.parametrize("command", [
        "psql -c 'DROP DATABASE production'",
        "mysql -e 'DROP SCHEMA users'",
        "sqlite3 db.sqlite 'TRUNCATE TABLE users'",
    ])
    def test_blocks_database_destructive(self, command):
        result = check_command(command, BLOCKED_PATTERNS)
        assert result is not None, f"Should block: {command}"

    # GCP destructive
    @pytest.mark.parametrize("command", [
        "gcloud projects delete my-project",
        "gcloud compute instances delete vm-1",
        "gcloud sql instances delete db-1",
        "gcloud iam service-accounts remove-iam-policy-binding",
    ])
    def test_blocks_gcp_destructive(self, command):
        result = check_command(command, BLOCKED_PATTERNS)
        assert result is not None, f"Should block: {command}"

    # Safety bypass prevention
    @pytest.mark.parametrize("command", [
        "cat > ~/.claude/settings.json",
        "vim .claude/settings.json",
        "rm .git/hooks/pre-commit",
        "echo '' > .git/hooks/pre-push",
        "nano hooks/safety.py",
        "sed -i 's/block/allow/' hooks/safety_config.yaml",
    ])
    def test_blocks_safety_bypass(self, command):
        result = check_command(command, BLOCKED_PATTERNS)
        assert result is not None, f"Should block: {command}"


class TestEscalatePatterns:
    """Test that ambiguous commands are escalated to user."""

    # Pulumi commands
    @pytest.mark.parametrize("command", [
        "pulumi up",
        "pulumi destroy",
        "pulumi preview",
        "pulumi stack select prod",
    ])
    def test_escalates_pulumi(self, command):
        result = check_command(command, ESCALATE_PATTERNS)
        assert result is not None, f"Should escalate: {command}"
        assert "pulumi" in result[0].lower()

    # Terraform commands
    @pytest.mark.parametrize("command", [
        "terraform apply",
        "terraform destroy",
        "terraform apply -auto-approve",
    ])
    def test_escalates_terraform(self, command):
        result = check_command(command, ESCALATE_PATTERNS)
        assert result is not None, f"Should escalate: {command}"

    # Git push to protected branches
    @pytest.mark.parametrize("command", [
        "git push origin main",
        "git push origin master",
    ])
    def test_escalates_push_protected(self, command):
        result = check_command(command, ESCALATE_PATTERNS)
        assert result is not None, f"Should escalate: {command}"

    # Kubernetes
    @pytest.mark.parametrize("command", [
        "kubectl delete pod nginx",
        "kubectl delete deployment app",
        "kubectl apply --force -f deploy.yaml",
    ])
    def test_escalates_kubernetes(self, command):
        result = check_command(command, ESCALATE_PATTERNS)
        assert result is not None, f"Should escalate: {command}"

    # Database migrations
    @pytest.mark.parametrize("command", [
        "alembic upgrade head",
        "flask db upgrade",
        "prisma migrate deploy",
    ])
    def test_escalates_migrations(self, command):
        result = check_command(command, ESCALATE_PATTERNS)
        assert result is not None, f"Should escalate: {command}"


class TestAllowedCommands:
    """Test that safe commands are allowed."""

    @pytest.mark.parametrize("command", [
        # Basic file operations
        "ls -la",
        "cat README.md",
        "mkdir new_dir",
        "cp file1 file2",
        "mv old new",
        # Git safe operations
        "git status",
        "git log",
        "git diff",
        "git add .",
        "git commit -m 'message'",
        "git push origin feature-branch",
        "git pull",
        "git fetch",
        "git checkout -b new-branch",
        # Development commands
        "npm install",
        "npm run build",
        "pip install -r requirements.txt",
        "python3 app.py",
        "pytest tests/",
        "make build",
        # Docker safe operations
        "docker build -t app .",
        "docker run app",
        "docker ps",
        "docker logs container",
        # Cloud read operations
        "gcloud projects list",
        "gcloud compute instances list",
        "aws s3 ls",
        "aws ec2 describe-instances",
    ])
    def test_allows_safe_commands(self, command):
        blocked = check_command(command, BLOCKED_PATTERNS)
        escalated = check_command(command, ESCALATE_PATTERNS)
        assert blocked is None, f"Should not block: {command}"
        assert escalated is None, f"Should not escalate: {command}"


class TestHookIntegration:
    """Integration tests running the actual hook script."""

    HOOK_PATH = HOOKS_DIR / "safety.py"

    def run_hook(self, command: str) -> tuple:
        """Run the hook with a command and return (stdout, exit_code)."""
        input_json = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": command}
        })

        result = subprocess.run(
            ["python3", str(self.HOOK_PATH)],
            input=input_json,
            capture_output=True,
            text=True,
        )
        return result.stdout, result.returncode

    def test_allows_safe_command(self):
        stdout, exit_code = self.run_hook("ls -la")
        assert exit_code == 0
        assert stdout == ""

    def test_blocks_force_push(self):
        stdout, exit_code = self.run_hook("git push --force origin main")
        assert exit_code == 0
        result = json.loads(stdout)
        assert result["decision"] == "block"
        assert "force" in result["reason"].lower() or "history" in result["reason"].lower()

    def test_blocks_settings_modification(self):
        stdout, exit_code = self.run_hook("vim ~/.claude/settings.json")
        assert exit_code == 0
        result = json.loads(stdout)
        assert result["decision"] == "block"

    def test_escalates_pulumi(self):
        stdout, exit_code = self.run_hook("pulumi up")
        assert exit_code == 2  # Exit code 2 = escalate to user

    def test_escalates_terraform(self):
        stdout, exit_code = self.run_hook("terraform apply")
        assert exit_code == 2

    def test_ignores_non_bash_tools(self):
        input_json = json.dumps({
            "tool_name": "Read",
            "tool_input": {"path": "/etc/passwd"}
        })

        result = subprocess.run(
            ["python3", str(self.HOOK_PATH)],
            input=input_json,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert result.stdout == ""


class TestEdgeCases:
    """Test edge cases and potential bypasses."""

    @pytest.mark.parametrize("command", [
        # Case variations
        "GIT PUSH --FORCE",
        "Git Push --Force origin main",
        # Extra whitespace
        "git   push   --force   origin   main",
        # Embedded in larger command
        "echo test && git push --force origin main",
        "git push --force origin main; echo done",
    ])
    def test_blocks_command_variations(self, command):
        result = check_command(command, BLOCKED_PATTERNS)
        assert result is not None, f"Should block variation: {command}"

    @pytest.mark.parametrize("command", [
        # Partial matches that should NOT be blocked
        "git pushover",
        "git force-explain",
        "git-push --force-explain",
        "echo '--force'",
        # Similar but safe
        "grep -r 'git push --force' docs/",
    ])
    def test_allows_false_positives(self, command):
        # These might match loosely but shouldn't be blocked
        # Note: Some of these may still match - adjust patterns if needed
        pass  # Placeholder for fine-tuning


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
