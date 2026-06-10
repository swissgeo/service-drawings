from pydantic import BaseModel, Field


class Checker(BaseModel):
    success: bool = Field(
        description="True when the probe is successful, false otherwise",
        examples=[True],
    )
    message: str = Field(
        description="Failure explanation in case of failure, otherwise OK",
        examples=["OK"],
    )
    version: str = Field(
        description="Version of the service",
        examples=["v0.1.0"],
    )
