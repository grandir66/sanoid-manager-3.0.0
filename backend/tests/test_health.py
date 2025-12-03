"""
Test Health and Basic Endpoints
"""

import pytest
from fastapi.testclient import TestClient


class TestHealthEndpoints:
    """Test basic health and status endpoints"""
    
    def test_health_check(self, client):
        """Test health check endpoint (no auth required)"""
        response = client.get("/api/health")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "version" in data
        assert data["auth_enabled"] == True
    
    def test_setup_required(self, client):
        """Test setup required endpoint (no auth required)"""
        response = client.get("/api/setup-required")
        
        assert response.status_code == 200
        assert "setup_required" in response.json()


class TestCORS:
    """Test CORS configuration"""
    
    def test_cors_headers(self, client):
        """Test CORS headers are present"""
        response = client.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:8420",
                "Access-Control-Request-Method": "GET"
            }
        )
        
        # Should not fail
        assert response.status_code in [200, 405]


class TestErrorHandling:
    """Test error handling"""
    
    def test_404_endpoint(self, client, admin_token):
        """Test 404 for non-existent endpoint"""
        response = client.get(
            "/api/nonexistent",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        assert response.status_code == 404
    
    def test_invalid_json(self, client, admin_token):
        """Test invalid JSON handling"""
        response = client.post(
            "/api/nodes/",
            headers={
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            },
            content="invalid json"
        )
        
        assert response.status_code == 422

