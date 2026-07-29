"""Neutral configuration primitives shared by repository applications."""

from maas_common.catalog import (
    Capabilities,
    CatalogDefaults,
    EndpointReference,
    ModelCatalog,
    ModelDeployment,
    Precision,
    RequestProfile,
    ServingMetadata,
    StrictModel,
    load_dotenv,
    load_model_catalog,
)

__all__ = [
    "Capabilities",
    "CatalogDefaults",
    "EndpointReference",
    "ModelCatalog",
    "ModelDeployment",
    "Precision",
    "RequestProfile",
    "ServingMetadata",
    "StrictModel",
    "load_dotenv",
    "load_model_catalog",
]
