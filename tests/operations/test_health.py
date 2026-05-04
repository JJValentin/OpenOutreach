from linkedin.operations.health import FailureType

def test_failure_type_includes_network_error():
    assert FailureType.NETWORK_ERROR.value == "network_error"
