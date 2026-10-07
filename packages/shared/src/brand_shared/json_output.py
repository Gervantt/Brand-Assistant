from pydantic import BaseModel


def parse_json_model[M: BaseModel](schema: type[M], text: str) -> M:
    """Validate LLM output, tolerating markdown fences and prose around the JSON object.

    Raises `ValueError` (incl. pydantic `ValidationError`) when the output doesn't fit.
    """
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in the response")
    return schema.model_validate_json(text[start : end + 1])
