"""Custom company templates: create, list, look up, delete, and resolve for rendering."""

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CustomTemplate, Job
from app.schemas import CustomTemplateForm
from app.services.images import ImageStore
from app.services.templates import TEMPLATES
from app.services.templates.custom import CustomTemplateSpec


class TemplateNotFoundError(LookupError):
    pass


class TemplateInUseError(Exception):
    def __init__(self, count: int) -> None:
        super().__init__(f"Template is used by {count} job(s) and cannot be deleted.")


class BuiltinTemplateError(Exception):
    def __init__(self) -> None:
        super().__init__("Built-in templates cannot be deleted.")


def create_template(
    db: Session, form: CustomTemplateForm, logo: bytes, signature: bytes | None, images: ImageStore
) -> CustomTemplate:
    """Validate and store the images first, then the row. Nothing is left behind if any step fails."""
    stored: list[str] = []
    try:
        logo_file = images.validate_and_save(logo, "logo")
        stored.append(logo_file)
        signature_file = None
        if signature is not None:
            signature_file = images.validate_and_save(signature, "signature")
            stored.append(signature_file)

        template = CustomTemplate(**form.model_dump(), logo_file=logo_file, signature_file=signature_file)
        db.add(template)
        db.commit()
        return template
    except Exception:
        db.rollback()
        for name in stored:
            images.delete(name)
        raise


def list_templates(db: Session) -> list[CustomTemplate]:
    return list(db.scalars(select(CustomTemplate).order_by(CustomTemplate.created_at)).all())


def get_template(db: Session, template_id: str) -> CustomTemplate:
    template = db.get(CustomTemplate, template_id)
    if template is None:
        raise TemplateNotFoundError(template_id)
    return template


def custom_template_exists(db: Session, template_id: str) -> bool:
    return db.get(CustomTemplate, template_id) is not None


def delete_template(db: Session, template_id: str, images: ImageStore) -> None:
    """Refuse while any job uses the template: those jobs may still need to (re)generate certificates."""
    if template_id in TEMPLATES:
        raise BuiltinTemplateError()
    template = get_template(db, template_id)
    used_by = db.scalar(select(func.count()).select_from(Job).where(Job.template_id == template_id)) or 0
    if used_by:
        raise TemplateInUseError(used_by)
    files = (template.logo_file, template.signature_file)
    db.delete(template)
    db.commit()
    for name in files:
        images.delete(name)


def to_spec(template: CustomTemplate, upload_dir: Path) -> CustomTemplateSpec:
    images = ImageStore(upload_dir, max_bytes=0, max_dimension=0, downscale_to=0)  # path resolution only
    return CustomTemplateSpec(
        id=template.id,
        name=template.name,
        primary_color=template.primary_color,
        secondary_color=template.secondary_color,
        font=template.font,
        title_text=template.title_text,
        body_text=template.body_text,
        border_style=template.border_style,
        layout=template.layout,
        logo_path=images.path(template.logo_file),
        signature_path=images.path(template.signature_file) if template.signature_file else None,
        signer_name=template.signer_name,
        signer_title=template.signer_title,
    )


def resolve_template(db: Session, template_id: str, upload_dir: Path | None) -> "str | CustomTemplateSpec":
    """What the PDF generator needs: the id itself for a built-in design, a full spec for a custom one."""
    if template_id in TEMPLATES:
        return template_id
    template = db.get(CustomTemplate, template_id)
    if template is None:
        raise ValueError(f"Template '{template_id}' no longer exists")
    if upload_dir is None:
        raise RuntimeError("UPLOAD_DIR is not configured; custom templates cannot be rendered")
    return to_spec(template, upload_dir)
