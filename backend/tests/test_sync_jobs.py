"""
Test Sync Jobs API
"""

import pytest
from fastapi.testclient import TestClient


class TestSyncJobsAPI:
    """Test sync jobs endpoints"""
    
    def test_list_sync_jobs(self, client, admin_token, sample_sync_job):
        """Test listing sync jobs"""
        response = client.get(
            "/api/sync-jobs/",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        assert response.status_code == 200
        jobs = response.json()
        assert len(jobs) >= 1
        assert jobs[0]["name"] == "test-job"
    
    def test_list_sync_jobs_unauthenticated(self, client):
        """Test listing jobs without auth"""
        response = client.get("/api/sync-jobs/")
        
        assert response.status_code == 401
    
    def test_create_sync_job(self, client, admin_token, sample_node, db):
        """Test creating a sync job"""
        from database import Node
        
        # Create destination node
        dest = Node(name="dest-node-new", hostname="192.168.1.105")
        db.add(dest)
        db.commit()
        
        response = client.post(
            "/api/sync-jobs/",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "new-sync-job",
                "source_node_id": sample_node.id,
                "source_dataset": "rpool/data/vm-200-disk-0",
                "dest_node_id": dest.id,
                "dest_dataset": "rpool/replica/vm-200-disk-0",
                "schedule": "0 2 * * *",
                "compress": "lz4"
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "new-sync-job"
        assert data["schedule"] == "0 2 * * *"
    
    def test_create_sync_job_invalid_source(self, client, admin_token, sample_node):
        """Test creating job with invalid source node"""
        response = client.post(
            "/api/sync-jobs/",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "invalid-job",
                "source_node_id": 9999,  # Non-existent
                "source_dataset": "rpool/data",
                "dest_node_id": sample_node.id,
                "dest_dataset": "rpool/replica"
            }
        )
        
        assert response.status_code == 400
    
    def test_create_sync_job_viewer_forbidden(self, client, viewer_token, sample_node, db):
        """Test viewer cannot create sync job"""
        from database import Node
        
        dest = Node(name="dest-node-v", hostname="192.168.1.106")
        db.add(dest)
        db.commit()
        
        response = client.post(
            "/api/sync-jobs/",
            headers={"Authorization": f"Bearer {viewer_token}"},
            json={
                "name": "forbidden-job",
                "source_node_id": sample_node.id,
                "source_dataset": "rpool/data",
                "dest_node_id": dest.id,
                "dest_dataset": "rpool/replica"
            }
        )
        
        assert response.status_code == 403
    
    def test_get_sync_job(self, client, admin_token, sample_sync_job):
        """Test getting a specific sync job"""
        response = client.get(
            f"/api/sync-jobs/{sample_sync_job.id}",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        assert response.status_code == 200
        assert response.json()["name"] == "test-job"
    
    def test_get_sync_job_not_found(self, client, admin_token):
        """Test getting non-existent job"""
        response = client.get(
            "/api/sync-jobs/9999",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        assert response.status_code == 404
    
    def test_update_sync_job(self, client, admin_token, sample_sync_job):
        """Test updating a sync job"""
        response = client.put(
            f"/api/sync-jobs/{sample_sync_job.id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "schedule": "0 */6 * * *",
                "compress": "zstd"
            }
        )
        
        assert response.status_code == 200
        assert response.json()["schedule"] == "0 */6 * * *"
        assert response.json()["compress"] == "zstd"
    
    def test_toggle_sync_job(self, client, admin_token, sample_sync_job):
        """Test toggling sync job active state"""
        # Initially active
        assert sample_sync_job.is_active
        
        # Toggle off
        response = client.post(
            f"/api/sync-jobs/{sample_sync_job.id}/toggle",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        assert response.status_code == 200
        assert response.json()["is_active"] == False
        
        # Toggle on
        response = client.post(
            f"/api/sync-jobs/{sample_sync_job.id}/toggle",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        assert response.status_code == 200
        assert response.json()["is_active"] == True
    
    def test_delete_sync_job(self, client, admin_token, sample_sync_job):
        """Test deleting a sync job"""
        response = client.delete(
            f"/api/sync-jobs/{sample_sync_job.id}",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        assert response.status_code == 200
        
        # Verify deleted
        response = client.get(
            f"/api/sync-jobs/{sample_sync_job.id}",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert response.status_code == 404
    
    def test_get_job_logs(self, client, admin_token, sample_sync_job):
        """Test getting sync job logs"""
        response = client.get(
            f"/api/sync-jobs/{sample_sync_job.id}/logs",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        assert response.status_code == 200
        assert isinstance(response.json(), list)
    
    def test_get_sync_stats(self, client, admin_token):
        """Test getting sync statistics"""
        response = client.get(
            "/api/sync-jobs/stats/summary",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "total_jobs" in data
        assert "active_jobs" in data

