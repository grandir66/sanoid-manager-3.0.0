"""
Test Settings API
"""

import pytest
from fastapi.testclient import TestClient


class TestSettingsAPI:
    """Test settings endpoints"""
    
    def test_get_all_settings(self, client, admin_token):
        """Test getting all settings"""
        response = client.get(
            "/api/settings/",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        assert response.status_code == 200
        assert isinstance(response.json(), dict)
    
    def test_get_all_settings_unauthenticated(self, client):
        """Test settings require auth"""
        response = client.get("/api/settings/")
        
        assert response.status_code == 401
    
    def test_update_setting_admin(self, client, admin_token):
        """Test admin can update settings"""
        response = client.put(
            "/api/settings/test_setting",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"value": "test_value"}
        )
        
        assert response.status_code == 200
        assert response.json()["value"] == "test_value"
    
    def test_update_setting_non_admin(self, client, operator_token):
        """Test non-admin cannot update settings"""
        response = client.put(
            "/api/settings/test_setting",
            headers={"Authorization": f"Bearer {operator_token}"},
            json={"value": "test_value"}
        )
        
        assert response.status_code == 403


class TestSystemConfig:
    """Test system configuration endpoints"""
    
    def test_get_system_config(self, client, admin_token):
        """Test getting system config"""
        response = client.get(
            "/api/settings/system/all",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)
    
    def test_get_system_config_by_category(self, client, admin_token):
        """Test getting config by category"""
        response = client.get(
            "/api/settings/system/all?category=auth",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        assert response.status_code == 200
    
    def test_update_system_config(self, client, admin_token):
        """Test updating system config"""
        response = client.put(
            "/api/settings/system/test_config",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"value": "new_value", "description": "Test config"}
        )
        
        assert response.status_code == 200


class TestAuthConfig:
    """Test authentication configuration"""
    
    def test_get_auth_settings(self, client, admin_token):
        """Test getting auth settings"""
        response = client.get(
            "/api/settings/auth/config",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "auth_method" in data
        assert "auth_session_timeout" in data
    
    def test_update_auth_settings(self, client, admin_token):
        """Test updating auth settings"""
        response = client.put(
            "/api/settings/auth/config",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "auth_method": "local",
                "auth_session_timeout": 240
            }
        )
        
        assert response.status_code == 200
    
    def test_auth_settings_non_admin(self, client, operator_token):
        """Test non-admin cannot update auth settings"""
        response = client.put(
            "/api/settings/auth/config",
            headers={"Authorization": f"Bearer {operator_token}"},
            json={"auth_method": "local"}
        )
        
        assert response.status_code == 403


class TestNotificationConfig:
    """Test notification configuration"""
    
    def test_get_notification_config(self, client, admin_token):
        """Test getting notification config"""
        response = client.get(
            "/api/settings/notifications",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "smtp_enabled" in data
        assert "webhook_enabled" in data
    
    def test_update_notification_config(self, client, admin_token):
        """Test updating notification config"""
        response = client.put(
            "/api/settings/notifications",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "smtp_enabled": True,
                "smtp_host": "smtp.example.com",
                "smtp_port": 587,
                "notify_on_failure": True
            }
        )
        
        assert response.status_code == 200
    
    def test_get_categories(self, client, admin_token):
        """Test getting config categories"""
        response = client.get(
            "/api/settings/categories",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "categories" in data
        assert len(data["categories"]) > 0

