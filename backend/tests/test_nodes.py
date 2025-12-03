"""
Test Nodes API
"""

import pytest
from fastapi.testclient import TestClient


class TestNodesAPI:
    """Test nodes endpoints"""
    
    def test_list_nodes_authenticated(self, client, admin_user, admin_token):
        """Test listing nodes with authentication"""
        response = client.get(
            "/api/nodes/",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        assert response.status_code == 200
        assert isinstance(response.json(), list)
    
    def test_list_nodes_unauthenticated(self, client):
        """Test listing nodes without authentication"""
        response = client.get("/api/nodes/")
        
        assert response.status_code == 401
    
    def test_create_node_operator(self, client, operator_user, operator_token):
        """Test operator can create node"""
        response = client.post(
            "/api/nodes/",
            headers={"Authorization": f"Bearer {operator_token}"},
            json={
                "name": "new-node",
                "hostname": "192.168.1.200",
                "ssh_port": 22,
                "ssh_user": "root",
                "ssh_key_path": "/root/.ssh/id_rsa"
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "new-node"
        assert data["hostname"] == "192.168.1.200"
    
    def test_create_node_viewer_forbidden(self, client, viewer_user, viewer_token):
        """Test viewer cannot create node"""
        response = client.post(
            "/api/nodes/",
            headers={"Authorization": f"Bearer {viewer_token}"},
            json={
                "name": "new-node",
                "hostname": "192.168.1.200",
            }
        )
        
        assert response.status_code == 403
    
    def test_create_node_duplicate_name(self, client, admin_token, sample_node):
        """Test cannot create node with duplicate name"""
        response = client.post(
            "/api/nodes/",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "test-node",  # Already exists
                "hostname": "192.168.1.200",
            }
        )
        
        assert response.status_code == 400
    
    def test_get_node(self, client, admin_token, sample_node):
        """Test getting a specific node"""
        response = client.get(
            f"/api/nodes/{sample_node.id}",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        assert response.status_code == 200
        assert response.json()["name"] == "test-node"
    
    def test_get_node_not_found(self, client, admin_token):
        """Test getting non-existent node"""
        response = client.get(
            "/api/nodes/9999",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        assert response.status_code == 404
    
    def test_update_node(self, client, admin_token, sample_node):
        """Test updating a node"""
        response = client.put(
            f"/api/nodes/{sample_node.id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"hostname": "192.168.1.150", "notes": "Updated"}
        )
        
        assert response.status_code == 200
        assert response.json()["hostname"] == "192.168.1.150"
        assert response.json()["notes"] == "Updated"
    
    def test_delete_node_admin_only(self, client, operator_token, sample_node):
        """Test only admin can delete node"""
        response = client.delete(
            f"/api/nodes/{sample_node.id}",
            headers={"Authorization": f"Bearer {operator_token}"}
        )
        
        assert response.status_code == 403
    
    def test_delete_node_admin(self, client, admin_token, sample_node):
        """Test admin can delete node"""
        response = client.delete(
            f"/api/nodes/{sample_node.id}",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        assert response.status_code == 200


class TestNodeAccessControl:
    """Test node access control based on allowed_nodes"""
    
    def test_user_with_restricted_nodes(self, client, db, viewer_user, sample_node):
        """Test user can only see allowed nodes"""
        from services.auth_service import auth_service
        
        # Create another node
        from database import Node
        other_node = Node(
            name="other-node",
            hostname="192.168.1.102"
        )
        db.add(other_node)
        db.commit()
        
        # Restrict user to only sample_node
        viewer_user.allowed_nodes = [sample_node.id]
        db.commit()
        
        token = auth_service.create_access_token({
            "sub": str(viewer_user.id),
            "username": viewer_user.username,
            "role": viewer_user.role,
            "auth_method": viewer_user.auth_method
        })
        
        # Should only see allowed node
        response = client.get(
            "/api/nodes/",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 200
        nodes = response.json()
        assert len(nodes) == 1
        assert nodes[0]["id"] == sample_node.id
    
    def test_user_cannot_access_restricted_node(self, client, db, viewer_user, sample_node):
        """Test user cannot access non-allowed node"""
        from services.auth_service import auth_service
        from database import Node
        
        other_node = Node(
            name="other-node-2",
            hostname="192.168.1.103"
        )
        db.add(other_node)
        db.commit()
        
        # Restrict user
        viewer_user.allowed_nodes = [sample_node.id]
        db.commit()
        
        token = auth_service.create_access_token({
            "sub": str(viewer_user.id),
            "username": viewer_user.username,
            "role": viewer_user.role,
            "auth_method": viewer_user.auth_method
        })
        
        # Try to access other node
        response = client.get(
            f"/api/nodes/{other_node.id}",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 403

