from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.core.database import get_db, get_engine
from app.main import app
from tests.conftest import TEST_ENV

client = TestClient(app)


class _Result:
    def scalar_one(self) -> int:
        return 1


class HealthySession:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement):
        self.statements.append(str(statement))
        return _Result()


class FailingSession:
    def execute(self, statement):
        raise OperationalError(
            "SELECT 1",
            {},
            Exception(
                f"(1045) Access denied for user '{TEST_ENV['DB_USER']}'@'{TEST_ENV['DB_HOST']}' "
                f"password={TEST_ENV['DB_PASSWORD']}"
            ),
        )


def test_db_health_success() -> None:
    session = HealthySession()
    app.dependency_overrides[get_db] = lambda: session

    response = client.get("/api/health/db")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "reachable"}
    assert session.statements == ["SELECT 1"]


def test_db_health_failure_returns_503_without_leaking_details() -> None:
    app.dependency_overrides[get_db] = lambda: FailingSession()

    response = client.get("/api/health/db")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}
    for sensitive in (TEST_ENV["DB_PASSWORD"], TEST_ENV["DB_USER"], TEST_ENV["DB_HOST"], "Access denied"):
        assert sensitive not in response.text


def test_app_health_is_independent_of_database() -> None:
    app.dependency_overrides[get_db] = lambda: FailingSession()

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_engine_is_lazy_and_hides_password() -> None:
    engine = get_engine()  # creating the engine must not open a connection

    assert engine.pool.checkedout() == 0
    assert TEST_ENV["DB_PASSWORD"] not in str(engine.url)
    assert TEST_ENV["DB_PASSWORD"] not in repr(engine)
    assert engine.url.query["charset"] == "utf8mb4"
    assert engine.url.drivername == "mysql+pymysql"
