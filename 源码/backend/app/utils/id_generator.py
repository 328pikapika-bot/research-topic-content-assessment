import uuid


def generate_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:12]}"
