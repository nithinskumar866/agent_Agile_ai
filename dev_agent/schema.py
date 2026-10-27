"""Pydantic schemas for the technical architecture and project design."""

from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class TechStackItem(BaseModel):
    category: str = Field(..., description="E.g., frontend, backend, database, cache, auth, infrastructure")
    technology: str = Field(..., description="E.g., FastAPI, PostgreSQL, Next.js, Redis")
    rationale: str = Field(..., description="Why this choice fits the requirements and constraints")


class ArchitectureComponent(BaseModel):
    name: str = Field(..., description="Component or service name, e.g., API Gateway, Auth Service")
    role: str = Field(..., description="High-level purpose of this component")
    responsibilities: List[str] = Field(default_factory=list, description="Key duties handled by this component")
    dependencies: List[str] = Field(default_factory=list, description="Other components or external services it calls")
    addresses: List[str] = Field(default_factory=list, description="PRD requirement IDs this component delivers, e.g. FR-1, NFR-2")


class ScaffoldEntry(BaseModel):
    path: str = Field(..., description="Relative file or folder path, e.g., src/services/auth.py")
    description: str = Field(..., description="Purpose and role of this file/directory")


class GeneratedFile(BaseModel):
    """A source file the Dev agent wants written into the project sandbox."""
    path: str = Field(..., description="Relative path from the architecture's scaffold, e.g. backend/app/main.py")
    content: str = Field(..., description="Full text content of the file")


class ImplementedFile(BaseModel):
    path: str
    bytes: int = 0
    status: Literal["written", "failed", "not_attempted"] = "written"
    error: str = ""


class Implementation(BaseModel):
    """What implement_scaffold has actually put on disk, across calls."""
    project: str = Field(default="", description="Folder name under workspace/; fixed after the first write")
    architecture_version: int = 0
    files: List[ImplementedFile] = Field(default_factory=list)


class TechDesign(BaseModel):
    title: str = Field(default="Technical Architecture & Project Scaffold")
    summary: str = Field(default="")
    version: int = Field(default=0)
    published_at: Optional[str] = None
    based_on_prd_version: int = Field(default=0, description="PRD version the last published architecture was built from")
    tech_stack: List[TechStackItem] = Field(default_factory=list)
    components: List[ArchitectureComponent] = Field(default_factory=list)
    project_scaffold: List[ScaffoldEntry] = Field(default_factory=list)
    change_log: List[str] = Field(default_factory=list)
    has_unpublished_changes: bool = Field(default=False, description="Design edited since the last publish")
    implementation: Implementation = Field(default_factory=Implementation)
