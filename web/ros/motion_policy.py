from __future__ import annotations


def velocity_source(
    control_mode: str,
    command_age: float,
    feedback_age: float,
    command_timeout: float,
    feedback_timeout: float,
) -> str:
    if control_mode == "NAV":
        return "feedback" if feedback_age <= feedback_timeout else "zero"
    if command_age <= command_timeout:
        return "command"
    if feedback_age <= feedback_timeout:
        return "feedback"
    return "zero"
