"""
Test Authentication and Authorization
"""

import pytest
from fastapi.testclient import TestClient
from services.auth_service import auth_service


class TestAuthService:
    """Test auth service functions"""
    
    def test_password_hash(self):
        """Test password hashing"""
        password = "TestPassword123!"
        hashed = auth_service.get_password_hash(password)
        
        assert hashed != password
        assert auth_service.verify_password(password, hashed)
        assert not auth_service.verify_password("wrong", hashed)
    
    def test_password_strength_valid(self):
        """Test valid password"""
        is_valid, msg = auth_service.validate_password_strength("ValidPass1")
        assert is_valid
    
    def test_password_strength_too_short(self):
        """Test password too short"""
        is_valid, msg = auth_service.validate_password_strength("Short1")
        assert not is_valid
        assert "8 caratteri" in msg
    
    def test_password_strength_no_uppercase(self):
        """Test password without uppercase"""
        is_valid, msg = auth_service.validate_password_strength("nouppercase1")
        assert not is_valid
    
    def test_password_strength_no_digit(self):
        """Test password without digit"""
        is_valid, msg = auth_service.validate_password_strength("NoDigitsHere")
        assert not is_valid
    
    def test_create_access_token(self):
        """Test JWT token creation"""
        token = auth_service.create_access_token({"sub": "1", "username": "test"})
        
        assert token is not None
        assert len(token) > 0
    
    def test_verify_token(self):
        """Test JWT token verification"""
        token = auth_service.create_access_token({"sub": "1", "username": "test"})
        
        success, payload = auth_service.verify_token(token)
        
        assert success
        assert payload["sub"] == "1"
        assert payload["username"] == "test"
        assert payload["type"] == "access"
    
    def test_verify_invalid_token(self):
        """Test invalid token verification"""
        success, payload = auth_service.verify_token("invalid.token.here")
        
        assert not success
        assert payload is None
    
    def test_refresh_token(self):
        """Test refresh token creation"""
        token = auth_service.create_refresh_token({"sub": "1"})
        success, payload = auth_service.verify_token(token)
        
        assert success
        assert payload["type"] == "refresh"


class TestSetup:
    """Test initial setup endpoint"""
    
    def test_setup_required_initially(self, client):
        """Test setup is required when no users exist"""
        response = client.get("/api/setup-required")
        
        assert response.status_code == 200
        assert response.json()["setup_required"] == True
    
    def test_initial_setup(self, client):
        """Test initial admin setup"""
        response = client.post("/api/auth/setup", json={
            "username": "admin",
            "email": "admin@test.com",
            "password": "Admin123!",
            "full_name": "Admin User"
        })
        
        assert response.status_code == 200
        assert "Setup completato" in response.json()["message"]
    
    def test_setup_already_done(self, client, admin_user):
        """Test setup fails if users exist"""
        response = client.post("/api/auth/setup", json={
            "username": "admin2",
            "email": "admin2@test.com",
            "password": "Admin123!",
        })
        
        assert response.status_code == 400
        assert "già completato" in response.json()["detail"]


class TestLogin:
    """Test login endpoint"""
    
    def test_login_success(self, client, admin_user):
        """Test successful login"""
        response = client.post("/api/auth/login", json={
            "username": "admin",
            "password": "Admin123!"
        })
        
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["user"]["username"] == "admin"
        assert data["user"]["role"] == "admin"
    
    def test_login_wrong_password(self, client, admin_user):
        """Test login with wrong password"""
        response = client.post("/api/auth/login", json={
            "username": "admin",
            "password": "wrongpassword"
        })
        
        assert response.status_code == 401
    
    def test_login_unknown_user(self, client):
        """Test login with unknown user"""
        response = client.post("/api/auth/login", json={
            "username": "unknown",
            "password": "Password123!"
        })
        
        assert response.status_code == 401
    
    def test_login_disabled_user(self, client, db, admin_user):
        """Test login with disabled user"""
        admin_user.is_active = False
        db.commit()
        
        response = client.post("/api/auth/login", json={
            "username": "admin",
            "password": "Admin123!"
        })
        
        assert response.status_code == 403


class TestTokenRefresh:
    """Test token refresh endpoint"""
    
    def test_refresh_token_success(self, client, admin_user):
        """Test successful token refresh"""
        # First login
        login_response = client.post("/api/auth/login", json={
            "username": "admin",
            "password": "Admin123!"
        })
        refresh_token = login_response.json()["refresh_token"]
        
        # Refresh
        response = client.post(
            "/api/auth/refresh",
            headers={"Authorization": f"Bearer {refresh_token}"}
        )
        
        assert response.status_code == 200
        assert "access_token" in response.json()
    
    def test_refresh_with_access_token(self, client, admin_token):
        """Test refresh fails with access token"""
        response = client.post(
            "/api/auth/refresh",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        assert response.status_code == 401


class TestCurrentUser:
    """Test current user endpoint"""
    
    def test_get_current_user(self, client, admin_user, admin_token):
        """Test get current user info"""
        response = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["username"] == "admin"
        assert data["role"] == "admin"
    
    def test_get_current_user_no_auth(self, client):
        """Test get current user without auth"""
        response = client.get("/api/auth/me")
        
        assert response.status_code == 401


class TestPasswordChange:
    """Test password change endpoint"""
    
    def test_change_password_success(self, client, admin_user, admin_token):
        """Test successful password change"""
        response = client.put(
            "/api/auth/me/password",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "current_password": "Admin123!",
                "new_password": "NewAdmin123!"
            }
        )
        
        assert response.status_code == 200
    
    def test_change_password_wrong_current(self, client, admin_user, admin_token):
        """Test password change with wrong current"""
        response = client.put(
            "/api/auth/me/password",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "current_password": "WrongPassword",
                "new_password": "NewAdmin123!"
            }
        )
        
        assert response.status_code == 400
    
    def test_change_password_weak_new(self, client, admin_user, admin_token):
        """Test password change with weak new password"""
        response = client.put(
            "/api/auth/me/password",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "current_password": "Admin123!",
                "new_password": "weak"
            }
        )
        
        assert response.status_code == 400


class TestUserManagement:
    """Test user management endpoints"""
    
    def test_list_users_admin(self, client, admin_user, admin_token):
        """Test admin can list users"""
        response = client.get(
            "/api/auth/users",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        assert response.status_code == 200
        assert len(response.json()) >= 1
    
    def test_list_users_non_admin(self, client, viewer_user, viewer_token):
        """Test non-admin cannot list users"""
        response = client.get(
            "/api/auth/users",
            headers={"Authorization": f"Bearer {viewer_token}"}
        )
        
        assert response.status_code == 403
    
    def test_create_user_admin(self, client, admin_user, admin_token):
        """Test admin can create user"""
        response = client.post(
            "/api/auth/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "username": "newuser",
                "email": "new@test.com",
                "password": "NewUser123!",
                "role": "operator"
            }
        )
        
        assert response.status_code == 200
        assert response.json()["username"] == "newuser"
        assert response.json()["role"] == "operator"
    
    def test_create_user_duplicate(self, client, admin_user, admin_token):
        """Test cannot create duplicate user"""
        response = client.post(
            "/api/auth/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "username": "admin",  # Already exists
                "email": "new@test.com",
                "password": "NewUser123!",
            }
        )
        
        assert response.status_code == 400
    
    def test_update_user(self, client, admin_user, operator_user, admin_token):
        """Test admin can update user"""
        response = client.put(
            f"/api/auth/users/{operator_user.id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"role": "viewer"}
        )
        
        assert response.status_code == 200
        assert response.json()["role"] == "viewer"
    
    def test_delete_user(self, client, admin_user, operator_user, admin_token):
        """Test admin can delete user"""
        response = client.delete(
            f"/api/auth/users/{operator_user.id}",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        assert response.status_code == 200
    
    def test_cannot_delete_self(self, client, admin_user, admin_token):
        """Test admin cannot delete themselves"""
        response = client.delete(
            f"/api/auth/users/{admin_user.id}",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        assert response.status_code == 400


class TestAuditLog:
    """Test audit log endpoint"""
    
    def test_get_audit_log_admin(self, client, admin_user, admin_token):
        """Test admin can access audit log"""
        response = client.get(
            "/api/auth/audit-log",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        assert response.status_code == 200
    
    def test_get_audit_log_non_admin(self, client, viewer_user, viewer_token):
        """Test non-admin cannot access audit log"""
        response = client.get(
            "/api/auth/audit-log",
            headers={"Authorization": f"Bearer {viewer_token}"}
        )
        
        assert response.status_code == 403

