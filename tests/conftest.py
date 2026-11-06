import pytest

from teamlab.store import Store
from teamlab.tools import Session


@pytest.fixture
def store():
    return Store()


@pytest.fixture
def ana(store):
    return Session("ana@example.test", store)


@pytest.fixture
def ben(store):
    return Session("ben@example.test", store)
