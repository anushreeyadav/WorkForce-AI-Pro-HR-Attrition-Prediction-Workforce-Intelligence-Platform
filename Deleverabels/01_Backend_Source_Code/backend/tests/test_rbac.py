"""RBAC is exercised through test-only routes; no business endpoints exist yet."""

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import CurrentUser, require_roles
from app.core.database import get_db
from app.core.security import create_access_token
from app.models import User, UserRole
from tests.conftest import auth_header

ALL_ROLES = list(UserRole)

router = APIRouter(prefix="/__test__/rbac")


@router.get("/admin")
def admin_only(user: User = Depends(require_roles(UserRole.ADMIN))) -> dict:
    return {"role": user.role}


@router.get("/hr")
def hr_or_admin(user: User = Depends(require_roles(UserRole.ADMIN, UserRole.HR))) -> dict:
    return {"role": user.role}


@router.get("/manager")
def managers(user: User = Depends(require_roles(UserRole.ADMIN, UserRole.HR, UserRole.MANAGER))) -> dict:
    return {"role": user.role}


@router.get("/employee")
def employees_only(user: User = Depends(require_roles(UserRole.EMPLOYEE))) -> dict:
    return {"role": user.role}


@router.get("/any")
def any_authenticated(user: CurrentUser) -> dict:
    return {"role": user.role}


ROUTE_ALLOWED = {
    "admin": {UserRole.ADMIN},
    "hr": {UserRole.ADMIN, UserRole.HR},
    "manager": {UserRole.ADMIN, UserRole.HR, UserRole.MANAGER},
    "employee": {UserRole.EMPLOYEE},
    "any": set(ALL_ROLES),
}


@pytest.fixture
def rbac_client(session_factory) -> TestClient:
    """A separate app holding only the test routes, so the real app's routes are never modified."""

    def _override_get_db():
        with session_factory() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_db] = _override_get_db
    return TestClient(test_app)


@pytest.mark.parametrize("route", list(ROUTE_ALLOWED))
@pytest.mark.parametrize("role", ALL_ROLES, ids=[r.value for r in ALL_ROLES])
def test_role_matrix(rbac_client, make_user, route: str, role: UserRole) -> None:
    user = make_user(email=f"{role.value.lower()}@example.com", role=role)
    token = create_access_token(user.id).token

    response = rbac_client.get(f"/__test__/rbac/{route}", headers=auth_header(token))

    if role in ROUTE_ALLOWED[route]:
        assert response.status_code == 200
        assert response.json() == {"role": role.value}
    else:
        assert response.status_code == 403
        assert response.json() == {"detail": "You do not have permission to perform this action"}


@pytest.mark.parametrize("route", list(ROUTE_ALLOWED))
def test_role_routes_require_authentication(rbac_client, route: str) -> None:
    assert rbac_client.get(f"/__test__/rbac/{route}").status_code == 401


def test_role_comes_from_database_not_token(rbac_client, make_user, db) -> None:
    user = make_user(role=UserRole.ADMIN)
    token = create_access_token(user.id).token
    assert rbac_client.get("/__test__/rbac/admin", headers=auth_header(token)).status_code == 200

    # Demotion takes effect immediately for already-issued tokens.
    user.role = UserRole.EMPLOYEE
    db.commit()
    assert rbac_client.get("/__test__/rbac/admin", headers=auth_header(token)).status_code == 403


def test_require_roles_rejects_empty_or_invalid_roles() -> None:
    with pytest.raises(ValueError):
        require_roles()
    with pytest.raises(ValueError):
        require_roles("ADMIN")  # type: ignore[arg-type]
