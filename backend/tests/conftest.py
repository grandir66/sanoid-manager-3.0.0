"""
Pytest Configuration e Fixtures
"""

import pytest
import os
import tempfile
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Set test environment
os.environ["SANOID_MANAGER_DB"] = ":memory:"
os.environ["SANOID_MANAGER_SECRET_KEY"] = "test-secret-key-for-testing-only"

from database import Base, get_db, User, Node, SyncJob, Dataset
from main import app
from services.auth_service import auth_service


# Test database setup
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    """Override database dependency for testing"""
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(scope="function")
def db():
    """Create fresh database for each test"""
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    yield db
    db.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def client(db):
    """Create test client with fresh database"""
    Base.metadata.create_all(bind=engine)
    yield TestClient(app)
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def admin_user(db):
    """Create an admin user for testing"""
    user = User(
        username="admin",
        email="admin@test.com",
        password_hash=auth_service.get_password_hash("Admin123!"),
        full_name="Test Admin",
        role="admin",
        auth_method="local"
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def operator_user(db):
    """Create an operator user for testing"""
    user = User(
        username="operator",
        email="operator@test.com",
        password_hash=auth_service.get_password_hash("Operator123!"),
        full_name="Test Operator",
        role="operator",
        auth_method="local"
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def viewer_user(db):
    """Create a viewer user for testing"""
    user = User(
        username="viewer",
        email="viewer@test.com",
        password_hash=auth_service.get_password_hash("Viewer123!"),
        full_name="Test Viewer",
        role="viewer",
        auth_method="local"
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def admin_token(admin_user):
    """Get JWT token for admin user"""
    return auth_service.create_access_token(
        data={
            "sub": str(admin_user.id),
            "username": admin_user.username,
            "role": admin_user.role,
            "auth_method": admin_user.auth_method
        }
    )


@pytest.fixture
def operator_token(operator_user):
    """Get JWT token for operator user"""
    return auth_service.create_access_token(
        data={
            "sub": str(operator_user.id),
            "username": operator_user.username,
            "role": operator_user.role,
            "auth_method": operator_user.auth_method
        }
    )


@pytest.fixture
def viewer_token(viewer_user):
    """Get JWT token for viewer user"""
    return auth_service.create_access_token(
        data={
            "sub": str(viewer_user.id),
            "username": viewer_user.username,
            "role": viewer_user.role,
            "auth_method": viewer_user.auth_method
        }
    )


@pytest.fixture
def auth_headers(admin_token):
    """Get authorization headers for admin"""
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def sample_node(db):
    """Create a sample node for testing"""
    node = Node(
        name="test-node",
        hostname="192.168.1.100",
        ssh_port=22,
        ssh_user="root",
        ssh_key_path="/root/.ssh/id_rsa"
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


@pytest.fixture
def sample_dataset(db, sample_node):
    """Create a sample dataset for testing"""
    dataset = Dataset(
        node_id=sample_node.id,
        name="rpool/data/vm-100-disk-0",
        used="10G",
        available="100G",
        mountpoint="/rpool/data"
    )
    db.add(dataset)
    db.commit()
    db.refresh(dataset)
    return dataset


@pytest.fixture
def sample_sync_job(db, sample_node):
    """Create a sample sync job for testing"""
    # Create destination node
    dest_node = Node(
        name="dest-node",
        hostname="192.168.1.101",
        ssh_port=22,
        ssh_user="root",
        ssh_key_path="/root/.ssh/id_rsa"
    )
    db.add(dest_node)
    db.commit()
    
    job = SyncJob(
        name="test-job",
        source_node_id=sample_node.id,
        source_dataset="rpool/data/vm-100-disk-0",
        dest_node_id=dest_node.id,
        dest_dataset="rpool/replica/vm-100-disk-0",
        schedule="0 */4 * * *"
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job

