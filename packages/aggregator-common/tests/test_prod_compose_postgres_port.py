"""
Regression test for: docker-compose.prod.yml must publish postgres on
${POSTGRES_PUBLISH_ADDR:-127.0.0.1}:5432 so that the metrics-review tunnel
survives install.sh update.

Before the fix: no ports entry on the postgres service.
After the fix: ports entry with the configurable POSTGRES_PUBLISH_ADDR variable
and a loopback default so 0.0.0.0 is never the default.
"""

import pathlib

import pytest
import yaml

COMPOSE_PATH = pathlib.Path(__file__).parents[3] / "docker-compose.prod.yml"


@pytest.fixture(scope="module")
def prod_compose():
    with COMPOSE_PATH.open() as f:
        return yaml.safe_load(f)


def test_postgres_service_has_ports(prod_compose):
    pg = prod_compose["services"]["postgres"]
    assert "ports" in pg, "postgres service must publish a port for the metrics-review tunnel"
    assert pg["ports"], "postgres service ports list must not be empty"


def test_postgres_port_uses_publish_addr_variable(prod_compose):
    pg = prod_compose["services"]["postgres"]
    port_entry = pg["ports"][0]
    assert "POSTGRES_PUBLISH_ADDR" in port_entry, (
        "postgres port binding must reference POSTGRES_PUBLISH_ADDR so operators can "
        "configure LAN exposure without editing the compose file"
    )


def test_postgres_port_default_is_loopback(prod_compose):
    pg = prod_compose["services"]["postgres"]
    port_entry = pg["ports"][0]
    # The pattern must be ${POSTGRES_PUBLISH_ADDR:-127.0.0.1}:5432:5432
    # Verify the default is 127.0.0.1, never 0.0.0.0
    assert "127.0.0.1" in port_entry, (
        "postgres port binding must default to 127.0.0.1 to avoid exposing the "
        "database on all interfaces when POSTGRES_PUBLISH_ADDR is unset"
    )
    assert "0.0.0.0" not in port_entry, (
        "postgres port binding must never default to 0.0.0.0"
    )


def test_postgres_port_maps_5432(prod_compose):
    pg = prod_compose["services"]["postgres"]
    port_entry = pg["ports"][0]
    # Container port must be 5432
    assert port_entry.endswith(":5432:5432"), (
        f"postgres port entry must end in ':5432:5432', got: {port_entry!r}"
    )
