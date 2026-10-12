import pytest

from shared.install_profiles import (
    calculate_resource_tuning,
    capabilities_for_profile,
    component_enabled,
    validate_profile,
)


@pytest.mark.parametrize(
    ("gb", "buffer_mb", "connections", "php_mb", "redis_mb"),
    [
        (1, 128, 40, 128, 32),
        (2, 384, 75, 192, 64),
        (4, 1024, 125, 256, 128),
        (8, 2048, 200, 384, 256),
        (16, 5734, 280, 512, 512),
    ],
)
def test_resource_tuning_examples(gb, buffer_mb, connections, php_mb, redis_mb):
    result = calculate_resource_tuning(gb * 1024**3, 4)
    assert result.mariadb_buffer_pool_mb == buffer_mb
    assert result.mariadb_max_connections == connections
    assert result.php_memory_limit_mb == php_mb
    assert result.redis_maxmemory_mb == redis_mb
    assert result.ols_workers == 4


def test_profiles_are_monotonic_and_default_compatibility_is_full():
    minimal = set(capabilities_for_profile("minimal"))
    standard = set(capabilities_for_profile("standard"))
    full = set(capabilities_for_profile(None))
    assert minimal < standard < full
    assert "database" not in minimal
    assert "database" in standard
    assert component_enabled("minimal", "web")
    assert not component_enabled("minimal", "mail")
    assert component_enabled("standard", "mail")
    assert component_enabled("full", "backups")


def test_invalid_profile_is_rejected():
    with pytest.raises(ValueError, match="minimal, standard, full"):
        validate_profile("enterprise")
