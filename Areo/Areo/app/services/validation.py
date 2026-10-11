"""Recipient-level validation.

Each recipient is validated independently so that one bad row is reported
(and stored as INVALID) without rejecting the other recipients.
"""

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.schemas import RecipientIn


@dataclass
class ValidRecipient:
    row_index: int
    data: RecipientIn


@dataclass
class InvalidRecipientRow:
    row_index: int
    errors: list[str]
    raw: Any


def _format_errors(exc: ValidationError) -> list[str]:
    """Turn Pydantic errors into short, human-readable messages like 'email: ...'."""
    messages = []
    for err in exc.errors():
        field = ".".join(str(part) for part in err["loc"]) or "recipient"
        message = err["msg"].removeprefix("Value error, ")
        messages.append(f"{field}: {message}")
    return messages


def validate_recipients(
    raw_recipients: list[Any], existing_emails: dict[str, int] | None = None
) -> tuple[list[ValidRecipient], list[InvalidRecipientRow]]:
    """Validate each row on its own.

    `existing_emails` maps lower-cased emails already in the job to their row,
    so rows added later (PATCH) cannot duplicate an earlier recipient either.
    """
    valid: list[ValidRecipient] = []
    invalid: list[InvalidRecipientRow] = []
    seen_emails: dict[str, int] = {}
    existing_emails = existing_emails or {}

    for index, raw in enumerate(raw_recipients):
        if not isinstance(raw, dict):
            invalid.append(InvalidRecipientRow(index, ["recipient: must be a JSON object"], raw))
            continue

        try:
            recipient = RecipientIn.model_validate(raw)
        except ValidationError as exc:
            invalid.append(InvalidRecipientRow(index, _format_errors(exc), raw))
            continue

        email_key = recipient.email.lower()
        if email_key in existing_emails:
            invalid.append(
                InvalidRecipientRow(
                    index,
                    [f"email: already a recipient of this job (row {existing_emails[email_key]})"],
                    raw,
                )
            )
            continue
        if email_key in seen_emails:
            invalid.append(
                InvalidRecipientRow(
                    index,
                    [f"email: duplicate of recipient at row {seen_emails[email_key]}"],
                    raw,
                )
            )
            continue

        seen_emails[email_key] = index
        valid.append(ValidRecipient(index, recipient))

    return valid, invalid
