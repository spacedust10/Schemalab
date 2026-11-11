"""The model gateway as a FastAPI dependency."""

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, status

from schemalab.config import Settings, get_settings
from schemalab.llm import Gateway, NoModelsError, gateway_from_settings

logger = logging.getLogger(__name__)


def get_gateway(settings: Annotated[Settings, Depends(get_settings)]) -> Gateway:
    try:
        return gateway_from_settings(settings)
    except NoModelsError as e:
        # The details name config and env vars; they belong in the server log only.
        logger.error("no usable model: %s", e)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "No language model is available right now."
        ) from None


GatewayDep = Annotated[Gateway, Depends(get_gateway)]
