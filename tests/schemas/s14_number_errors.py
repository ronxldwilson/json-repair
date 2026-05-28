from pydantic import BaseModel


class NumericData(BaseModel):
    temperature: float
    count: int
    big_number: int
    hex_value: int
    negative: float
    infinity: float
