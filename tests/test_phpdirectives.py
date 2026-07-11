"""QA round 2, item 9: hardened php.ini default + disable_functions validation."""
import pytest

from daemon import phpdirectives
from shared.validation import ValidationError


def test_default_disable_functions_includes_the_standard_hardening_set():
    defaults = set(phpdirectives.DEFAULT_DISABLE_FUNCTIONS)
    assert {"exec", "system", "shell_exec", "passthru", "proc_open", "popen"} <= defaults


def test_validate_disable_functions_accepts_comma_string():
    assert phpdirectives.validate_disable_functions("exec, system,  shell_exec") == "exec,shell_exec,system"


def test_validate_disable_functions_accepts_list():
    assert phpdirectives.validate_disable_functions(["exec", "system"]) == "exec,system"


def test_validate_disable_functions_dedupes():
    assert phpdirectives.validate_disable_functions(["exec", "exec", "system"]) == "exec,system"


def test_validate_disable_functions_empty_list_is_valid():
    """Explicitly clearing every function for a scope is a real, if
    unusual, request -- distinct from no override existing at all."""
    assert phpdirectives.validate_disable_functions([]) == ""


def test_validate_disable_functions_rejects_bad_names():
    with pytest.raises(ValidationError):
        phpdirectives.validate_disable_functions(["exec; rm -rf /"])


def test_validate_disable_functions_rejects_names_with_spaces():
    with pytest.raises(ValidationError):
        phpdirectives.validate_disable_functions(["not a function"])


def test_validate_disable_functions_rejects_wrong_type():
    with pytest.raises(ValidationError):
        phpdirectives.validate_disable_functions(123)


def test_validate_disable_functions_rejects_too_many():
    with pytest.raises(ValidationError):
        phpdirectives.validate_disable_functions([f"fn{i}" for i in range(200)])
