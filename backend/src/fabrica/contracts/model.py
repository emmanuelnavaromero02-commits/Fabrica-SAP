from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from fabrica.sap.kinds import KINDS, ROLE_KIND

Profile = Literal["cloud", "classic"]


class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore")


class DesignObject(_Loose):
    name: str
    type: str
    role: str
    package: str
    description: str = ""
    binding_type: str = ""
    service_definition: str = ""
    root_entity: str = ""

    @field_validator("name", "type", "package", "root_entity", "service_definition")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.strip().upper()

    @property
    def known_kind(self) -> bool:
        return self.type in KINDS

    @property
    def role_matches_kind(self) -> bool:
        return ROLE_KIND.get(self.role) == self.type


class DataOrigin(_Loose):
    entity: str
    field: str
    origin: str
    to_confirm: bool = False


class BusinessRule(_Loose):
    id: str
    text: str


class ContractAssertion(_Loose):
    id: str
    dado: str
    cuando: str
    entonces: str
    regla: str = ""
    datos: str = ""
    evidencia: list[str] = Field(default_factory=list)

    @field_validator("id", "regla")
    @classmethod
    def _normalize_id(cls, value: str) -> str:
        return value.strip().upper()


class DesignSpec(_Loose):
    spec_markdown: str
    profile: Profile = "cloud"
    objects: list[DesignObject] = Field(default_factory=list)
    data_origins: list[DataOrigin] = Field(default_factory=list)
    rules: list[BusinessRule] = Field(default_factory=list)
    assertions: list[ContractAssertion] = Field(default_factory=list)

    def object_named(self, name: str) -> DesignObject | None:
        wanted = name.strip().upper()
        return next((o for o in self.objects if o.name == wanted), None)

    def assertions_document(self, requirement_id: int) -> dict[str, Any]:
        return {
            "requirement_id": requirement_id,
            "count": len(self.assertions),
            "assertions": [a.model_dump() for a in self.assertions],
        }
