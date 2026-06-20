from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProviderStatus(BaseModel):
    connected: bool
    login: str | None = None
    email: str | None = None
    email_matched: bool | None = None
    connected_at: datetime | None = None


class IntegrationsStatusOut(BaseModel):
    user_id: str
    user_email: str
    github: ProviderStatus
    google: ProviderStatus


class ConnectUrlOut(BaseModel):
    url: str


class RepoLinkIn(BaseModel):
    url: str = Field(examples=["https://github.com/org-name/capstone-project"])


class RepoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    group_id: str
    owner: str
    repo: str
    default_branch: str | None
    url: str
    created_at: datetime


class DocumentLinkIn(BaseModel):
    url: str = Field(
        examples=["https://docs.google.com/document/d/1abc.../edit"]
    )


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    group_id: str
    file_id: str
    title: str
    url: str
    created_at: datetime
