from typing import Annotated

from pydantic import Field

ClientId = Annotated[str, Field(description="ID клиента. Подставляется системой автоматически.")]
